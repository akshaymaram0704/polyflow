# PolyFlow

**Automated trading-intelligence pipeline for [Polymarket](https://polymarket.com).**

PolyFlow ingests on-chain prediction-market activity, ranks traders with a
quantitative scoring model, and surfaces high-confidence, consensus-driven trade
recommendations over a fast async API with sub-second price streaming.

- **Ingestion pipeline** across 500+ active prediction markets (markets, trades, positions).
- **Quantitative trader ranking** on *profitability*, *historical consistency*,
  *position sizing*, and *risk-adjusted performance*.
- **Recommendation engine** aggregating top traders into score-weighted directional consensus.
- **Async backend**: FastAPI + PostgreSQL + Redis + Docker, with a live WebSocket price feed.
- **Dashboard UI** — a zero-build web view of markets, the leaderboard and live recommendations.
- **Backtesting** — measures the engine's historical hit-rate / ROI vs a crowd baseline.
- **Trading module** — optional, opt-in order execution (paper by default; live is gated).

All Polymarket **data** used is read-only and public — **no API key required**. A key
(a Polygon wallet) is only needed for the optional *live* trading module.

---

## Architecture

```
                Polymarket public APIs (no key)
      Gamma  /  Data API  /  CLOB REST  /  CLOB WebSocket
                          │
        ┌─────────────────┴──────────────────┐
        │  Worker (APScheduler + WS streamer) │
        │   sync_markets → sync_trades →      │
        │   sync_traders → run_scoring        │
        └───────┬───────────────────┬─────────┘
                │ persist           │ live prices
          ┌─────▼─────┐        ┌────▼────┐
          │ PostgreSQL │        │  Redis  │  (cache + pub/sub)
          └─────┬─────┘        └────┬────┘
                │                   │
             ┌──▼───────────────────▼──┐
             │  FastAPI  (REST + WS)    │
             │  /markets /traders       │
             │  /recommendations        │
             │  /ws/prices              │
             └──────────────────────────┘
```

Everything runs **offline too**: a fixtures mode generates a deterministic
synthetic universe (520 markets, 1,200 traders) so the full pipeline, scoring,
API and streaming work with no network and no database server.

### Layout

| Path | Purpose |
|------|---------|
| `app/clients/` | Polymarket client (`polymarket.py`), WS streamer (`websocket.py`), offline `fixtures.py` |
| `app/ingestion/` | `markets.py`, `trades.py`, `traders.py` ingestion jobs |
| `app/scoring/ranking.py` | The quant ranking + recommendation engine (pure, unit-tested) |
| `app/backtesting/` | `scenario.py` (resolved-market scenario) + `engine.py` (hit-rate/ROI/Brier) |
| `app/trading/` | `risk.py` (pure order sizing), `executor.py` (paper/live), `client.py` (CLOB) |
| `app/web/static/` | Dashboard UI (`index.html`, `app.js`, `styles.css`) |
| `app/api/routes/` | `health`, `markets`, `traders`, `recommendations`, `backtest`, `trading`, `stream` (WS) |
| `app/worker/` | `pipeline.py` (orchestration), `scheduler.py` (APScheduler), `run.py` (entrypoint) |
| `app/db/` | SQLAlchemy async models + session |
| `alembic/` | Database migrations |
| `.github/workflows/ci.yml` | CI: ruff lint + format, pytest (3.11–3.13), Docker build |

---

## Quick start

### Option A — Offline demo (no Docker, no network)

Runs against synthetic fixtures on SQLite + in-memory cache. One process does it all.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

# Configure for a single-process offline demo
$env:POLYFLOW_USE_FIXTURES = "true"
$env:POLYFLOW_DATABASE_URL = "sqlite+aiosqlite:///./polyflow.db"
$env:POLYFLOW_RUN_WORKER_IN_API = "true"

uvicorn app.main:app --reload
```

Then open the **dashboard at http://localhost:8000/** (API docs at `/docs`). On
startup the pipeline seeds the DB and the price streamer begins ticking. Try:

```
GET  /markets?limit=10&sort=volume
GET  /traders/leaderboard?limit=20
GET  /recommendations?min_confidence=0.7
GET  /backtest?min_confidence=0.6            # hit-rate / ROI vs crowd baseline
WS   /ws/prices                              # streams live price ticks
```

You can also run the pipeline once from the CLI:

```powershell
python -m app.worker.run --once
```

### Option B — Full stack with Docker (live or fixtures)

```bash
cp .env.example .env
# For LIVE data, set POLYFLOW_USE_FIXTURES=false in .env (needs unrestricted network)
docker compose up --build
```

This starts `postgres`, `redis`, `api` (runs migrations then serves on :8000), and
`worker` (scheduler + WebSocket streamer). API docs at http://localhost:8000/docs.

> **Note on networks:** the live Polymarket endpoints must be reachable from wherever
> you run PolyFlow. Corporate proxies often block them — use fixtures mode there.

---

## The ranking algorithm

For each trader we snapshot their open positions (size, entry/current price, PnL)
and compute raw metrics, then normalize each across the whole cohort with a robust
**fractional percentile rank** (outlier-insensitive, deterministic). Four
interpretable sub-scores in `[0, 1]` are blended into a `composite`:

| Sub-score | What it measures | Built from |
|-----------|------------------|-----------|
| **Profitability** | Absolute + relative returns | ROI and total PnL (percentile-ranked) |
| **Consistency** | Repeatable, low-variance edge | Win rate + Sharpe-like ratio + track-record depth |
| **Sizing** | Sizing discipline & conviction | Size-weighted vs equal-weight return edge + diversification (1 − Herfindahl) |
| **Risk-adjusted** | Return per unit of downside | Sortino-like ratio + drawdown penalty |

```
composite = w_prof·profitability + w_cons·consistency
          + w_size·sizing       + w_risk·risk_adjusted
```

Weights are configurable (`POLYFLOW_W_*`, default 0.35 / 0.25 / 0.15 / 0.25).

**Recommendations:** for every market, the positions of the top-ranked traders are
aggregated per outcome, weighted by trader score × capital. The outcome with the
most score-weighted capital wins, with a **confidence** that blends directional
*agreement*, supporters' average *skill score*, and consensus *size*. Only
recommendations at/above `POLYFLOW_MIN_CONFIDENCE` are surfaced. Each carries a
rationale listing the contributing traders and their weights.

The math lives in `app/scoring/ranking.py` as pure functions
(`compute_metrics`, `score_cohort`, `build_recommendations`) and is unit-tested in
`tests/test_ranking.py`.

---

## Backtesting

Does the ranking actually pick winners? `GET /backtest` (or
`app/backtesting/engine.py`) evaluates it on a resolved-market scenario with a
clean **train/test split** — traders are ranked on their *past* resolved book,
then the top traders' consensus on a *disjoint* set of markets is graded against
the true outcomes. It reports hit-rate, ROI, Brier score, a per-confidence-bucket
calibration, and the **edge over a "follow the crowd favorite" baseline**.

On the bundled synthetic scenario the engine lands around **~80% hit-rate vs the
crowd's ~60%** with well-calibrated confidence buckets — i.e. higher-confidence
recommendations resolve correctly more often. (These reuse the *production*
scoring functions, so the backtest measures the real algorithm.)

## Trading (optional, opt-in)

PolyFlow can act on its own recommendations. **Disabled by default**, and even
when enabled it runs in **paper mode** (records intended `Order` rows without
sending anything) unless you explicitly configure live mode with a Polygon wallet.

- `GET /trading/status` — current mode + risk limits (never returns secrets).
- `POST /trading/execute` — evaluate active recs and place orders (paper/live).
- `GET /trading/orders` — order history.
- `GET /trading/pnl` — portfolio PnL: realized (settled) + unrealized (mark-to-market).
- `POST /trading/settle` — grade open orders against markets that have since resolved.

**PnL loop:** open paper orders are marked-to-market at the current outcome price;
once a market resolves, `settle` locks in realized PnL (winning shares pay \$1,
losing shares \$0). This closes the loop with the backtester — the backtest
estimates edge on historical resolutions, while the PnL tracker measures the live
paper book forward. The math is in `app/trading/pnl.py` (unit-tested).

Risk limits (`app/trading/risk.py`, pure + unit-tested) enforce per-order,
per-day and max-open-position caps and de-dupe already-held assets *before*
anything is recorded or sent. Live execution additionally requires
`pip install '.[trading]'` (`py-clob-client`) and a private key. **Trade at your
own risk — start in paper mode.**

---

## API reference (summary)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard UI (redirect) · `/docs` for OpenAPI |
| GET | `/health`, `/health/ready` | Liveness / readiness (DB + cache) |
| GET | `/markets` | List markets (filter by category/active, sort by volume/liquidity) |
| GET | `/markets/{condition_id}` | Single market |
| GET | `/markets/{condition_id}/price` | Latest price per outcome token (cache-first) |
| GET | `/traders/leaderboard` | Ranked traders with sub-scores + PnL |
| GET | `/traders/{wallet}` | Trader detail: score + top positions |
| GET | `/recommendations` | High-confidence recommendations (filterable) |
| GET | `/backtest` | Backtest metrics (hit-rate, ROI, Brier, crowd edge) |
| GET | `/trading/status` | Trading config + risk limits |
| POST | `/trading/execute` | Execute recommendations (paper unless configured live) |
| GET | `/trading/orders` | Paper/live order history |
| GET | `/trading/pnl` | Portfolio PnL (realized + unrealized) |
| POST | `/trading/settle` | Settle orders on resolved markets |
| WS  | `/ws/prices` | Live price stream; send `{"assets": [...]}` to filter |

Interactive OpenAPI docs at `/docs`.

---

## Configuration

All settings are environment variables prefixed `POLYFLOW_` (see `.env.example`).
Highlights:

| Var | Default | Meaning |
|-----|---------|---------|
| `POLYFLOW_USE_FIXTURES` | `true` | Serve synthetic data instead of the live API |
| `POLYFLOW_RUN_WORKER_IN_API` | `false` | Run pipeline + streamer inside the API process |
| `POLYFLOW_DATABASE_URL` | sqlite | `postgresql+asyncpg://…` in docker |
| `POLYFLOW_REDIS_URL` | localhost | Redis connection (falls back to in-memory cache) |
| `POLYFLOW_MARKET_LIMIT` | `500` | Active markets to track |
| `POLYFLOW_TOP_TRADERS` | `1000` | Traders to score |
| `POLYFLOW_MIN_CONFIDENCE` | `0.6` | Recommendation confidence floor |
| `POLYFLOW_W_*` | see above | Ranking weights |
| `POLYFLOW_TRADING_ENABLED` | `false` | Master switch for order execution |
| `POLYFLOW_TRADING_MODE` | `paper` | `paper` (record only) or `live` (sends orders) |
| `POLYFLOW_TRADING_MAX_ORDER_USD` | `50` | Per-order size cap |
| `POLYFLOW_TRADING_MAX_DAILY_USD` | `500` | Daily spend cap |
| `POLYFLOW_POLYGON_PRIVATE_KEY` | – | Live-only wallet key (never commit) |

---

## Tests & tooling

```powershell
pytest            # ranking math, client parsing, API smoke tests
ruff check .      # lint
ruff format .     # format
```

The test suite runs fully offline (fixtures + SQLite + in-memory cache).

---

## License

MIT
