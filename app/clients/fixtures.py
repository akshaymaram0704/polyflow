"""Offline fixture client.

Generates a deterministic synthetic universe — 500+ markets and 1000+ traders
whose positions/PnL are correlated with a hidden per-trader "skill", so the
ranking algorithm produces meaningful, testable signal without any network.

The dataset is built once (seeded RNG) and shared across instances. This client
implements the exact same interface as ``LivePolymarketClient`` so PolyFlow's
ingestion/scoring/API run end-to-end identically in fixtures mode.
"""

from __future__ import annotations

import random
from functools import lru_cache

from app.clients.polymarket import BasePolymarketClient

N_MARKETS = 520
N_TRADERS = 1200
SEED = 20260501

CATEGORIES = [
    "Politics",
    "Crypto",
    "Sports",
    "Economics",
    "Pop Culture",
    "Tech",
    "Science",
    "Geopolitics",
    "Elections",
    "Weather",
]
QUESTION_TEMPLATES = [
    "Will {subj} happen before {yr}?",
    "Will {subj} exceed the threshold in {yr}?",
    "Is {subj} resolving YES by {yr}?",
    "Will {subj} be confirmed in {yr}?",
]
SUBJECTS = [
    "BTC hit $150k",
    "the incumbent win",
    "the rate cut",
    "the merger close",
    "the launch succeed",
    "the team qualify",
    "inflation cool",
    "the bill pass",
    "the record break",
    "the deal finalize",
    "the upgrade ship",
    "the poll lead",
]


def _addr(i: int) -> str:
    return f"0x{i:040x}"


class _Universe:
    """Holds the generated markets, traders, positions and lookup indexes."""

    def __init__(self) -> None:
        rng = random.Random(SEED)
        self.markets: dict[str, dict] = {}
        self.market_order: list[str] = []
        self.token_to_price: dict[str, float] = {}
        self.positions_by_trader: dict[str, list[dict]] = {}
        self.holders_by_market: dict[str, list[dict]] = {}
        self.trader_meta: dict[str, dict] = {}

        self._build_markets(rng)
        self._build_traders(rng)

    def _build_markets(self, rng: random.Random) -> None:
        for m in range(N_MARKETS):
            cid = f"0xcond{m:05d}"
            yes_tok, no_tok = f"{m}0001", f"{m}0002"
            p_yes = round(rng.uniform(0.05, 0.95), 3)
            subj = rng.choice(SUBJECTS)
            question = rng.choice(QUESTION_TEMPLATES).format(subj=subj, yr=2026 + rng.randint(0, 2))
            self.markets[cid] = {
                "condition_id": cid,
                "question": question,
                "slug": f"market-{m}",
                "category": rng.choice(CATEGORIES),
                "outcomes": ["Yes", "No"],
                "clob_token_ids": [yes_tok, no_tok],
                "outcome_prices": [p_yes, round(1 - p_yes, 3)],
                "volume": round(rng.uniform(5_000, 5_000_000), 2),
                "liquidity": round(rng.uniform(1_000, 500_000), 2),
                "active": True,
                "closed": False,
                "start_date": None,
                "end_date": None,
            }
            self.market_order.append(cid)
            self.token_to_price[yes_tok] = p_yes
            self.token_to_price[no_tok] = round(1 - p_yes, 3)

    def _build_traders(self, rng: random.Random) -> None:
        for t in range(N_TRADERS):
            wallet = _addr(t + 1)
            # Hidden skill in [-1, 1]; drives edge, win-rate and sizing discipline.
            skill = max(-1.0, min(1.0, rng.gauss(0.0, 0.5)))
            typical_size = rng.uniform(50, 5000) * (1 + max(0, skill))
            n_pos = rng.randint(3, 16)
            self.trader_meta[wallet] = {"skill": skill, "username": f"trader_{t}"}

            positions: list[dict] = []
            chosen = rng.sample(self.market_order, min(n_pos, len(self.market_order)))
            for cid in chosen:
                mkt = self.markets[cid]
                outcome_idx = rng.randint(0, 1)
                asset = mkt["clob_token_ids"][outcome_idx]
                outcome = mkt["outcomes"][outcome_idx]
                cur_price = mkt["outcome_prices"][outcome_idx]

                # Entry price near current with noise; skilled traders enter cheaper.
                edge = skill * rng.uniform(0.02, 0.12)
                avg_price = min(0.98, max(0.02, cur_price - edge + rng.gauss(0, 0.03)))
                # Conviction sizing: skilled traders size up their better ideas.
                conviction = 1 + max(0.0, skill) * rng.uniform(0.0, 2.0)
                size = round(typical_size * conviction, 2)

                initial_value = round(size * avg_price, 2)
                current_value = round(size * cur_price, 2)
                cash_pnl = round(current_value - initial_value, 2)
                percent_pnl = round(cash_pnl / initial_value, 4) if initial_value else 0.0
                realized = round(cash_pnl * rng.uniform(0.0, 0.4), 2)
                redeemable = rng.random() < 0.1

                pos = {
                    "proxyWallet": wallet,
                    "name": self.trader_meta[wallet]["username"],
                    "asset": asset,
                    "conditionId": cid,
                    "outcome": outcome,
                    "size": size,
                    "avgPrice": round(avg_price, 4),
                    "curPrice": cur_price,
                    "initialValue": initial_value,
                    "currentValue": current_value,
                    "cashPnl": cash_pnl,
                    "percentPnl": percent_pnl,
                    "realizedPnl": realized,
                    "redeemable": redeemable,
                }
                positions.append(pos)
                self.holders_by_market.setdefault(cid, []).append(
                    {"proxyWallet": wallet, "amount": size}
                )
            self.positions_by_trader[wallet] = positions

        # Rank holders per market by size (largest first) to mimic /holders.
        for holders in self.holders_by_market.values():
            holders.sort(key=lambda h: h["amount"], reverse=True)


@lru_cache
def _universe() -> _Universe:
    return _Universe()


class FixturePolymarketClient(BasePolymarketClient):
    """Serves the synthetic universe through the standard client interface."""

    def __init__(self) -> None:
        self.u = _universe()

    async def get_markets(
        self, limit: int = 500, active: bool = True, min_volume: float = 0.0
    ) -> list[dict]:
        rows = [self.u.markets[cid] for cid in self.u.market_order]
        rows = [m for m in rows if m["volume"] >= min_volume]
        rows.sort(key=lambda m: m["volume"], reverse=True)
        return [dict(m) for m in rows[:limit]]

    async def get_market(self, condition_id: str) -> dict | None:
        m = self.u.markets.get(condition_id)
        return dict(m) if m else None

    async def get_trades(
        self, condition_id: str | None = None, user: str | None = None, limit: int = 100
    ) -> list[dict]:
        # Synthesize a trade tape from positions (entry fills).
        from app.clients.polymarket import normalize_trade

        pools: list[dict] = []
        if user:
            pools = self.u.positions_by_trader.get(user, [])
        elif condition_id:
            for poss in self.u.positions_by_trader.values():
                pools.extend(p for p in poss if p["conditionId"] == condition_id)
        else:
            for poss in self.u.positions_by_trader.values():
                pools.extend(poss)
        trades = []
        for p in pools[:limit]:
            txid = abs(hash((p["proxyWallet"], p["asset"]))) % 10**16
            trades.append(
                normalize_trade(
                    {
                        "transactionHash": f"0xtx{txid:016x}",
                        "proxyWallet": p["proxyWallet"],
                        "asset": p["asset"],
                        "conditionId": p["conditionId"],
                        "outcome": p["outcome"],
                        "side": "BUY",
                        "price": p["avgPrice"],
                        "size": p["size"],
                        "usdcSize": p["initialValue"],
                        "timestamp": 1_760_000_000,
                    }
                )
            )
        return trades

    async def get_positions(self, user: str, limit: int = 100) -> list[dict]:
        from app.clients.polymarket import normalize_position

        raw = self.u.positions_by_trader.get(user, [])[:limit]
        return [normalize_position(p) for p in raw]

    async def get_holders(self, condition_id: str, limit: int = 50) -> list[dict]:
        from app.clients.polymarket import normalize_holder

        raw = self.u.holders_by_market.get(condition_id, [])[:limit]
        return [normalize_holder(h) for h in raw]

    async def get_price(self, token_id: str, side: str = "buy") -> float | None:
        return self.u.token_to_price.get(str(token_id))
