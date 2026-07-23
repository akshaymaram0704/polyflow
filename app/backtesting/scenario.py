"""Backtest scenario generation.

To evaluate the ranking/recommendation engine we need *resolved* markets (known
outcomes) plus trader books — data the live public API only exposes partially and
historically. This module builds a deterministic synthetic scenario with a clean
train/test split so the evaluation is honest (no look-ahead):

* ``history_positions`` — each trader's resolved *past* book, with realized PnL.
  This is what the ranking model is allowed to see (used to score traders).
* ``test_positions``    — the same traders' picks on a disjoint set of *test*
  markets. Consensus recommendations are built from these.
* ``resolution``        — the true winning token per test market (the answer key).
* ``market_prices``     — pre-resolution outcome prices per test market, used to
  compute a "follow the crowd favorite" baseline to compare the algorithm against.

Traders have a hidden skill in [-1, 1] that raises both their historical PnL and
their probability of picking the winning side on test markets — so a good ranking
model should select traders whose consensus beats the crowd.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.config import settings


@dataclass
class Scenario:
    history_positions: dict[str, list[dict]] = field(default_factory=dict)
    test_positions: dict[str, list[dict]] = field(default_factory=dict)
    resolution: dict[str, str] = field(default_factory=dict)  # cid -> winning asset
    market_prices: dict[str, list[float]] = field(default_factory=dict)  # cid -> [p0, p1]


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def build_scenario(
    n_markets: int | None = None,
    n_traders: int = 600,
    seed: int = 424242,
) -> Scenario:
    n_markets = n_markets or settings.backtest_markets
    rng = random.Random(seed)
    sc = Scenario()

    # --- Test markets: assign a true winner + crowd price (weakly informative) ---
    test_markets: list[tuple[str, int, list[str], list[float]]] = []
    for m in range(n_markets):
        cid = f"0xbt{m:05d}"
        yes_tok, no_tok = f"bt{m}y", f"bt{m}n"
        winning_idx = rng.randint(0, 1)
        # Crowd price leans slightly toward the true winner (crowd is ~weakly right).
        mu = 0.55 if winning_idx == 0 else 0.45
        p_yes = round(_clip(rng.gauss(mu, 0.15), 0.1, 0.9), 3)
        prices = [p_yes, round(1 - p_yes, 3)]
        tokens = [yes_tok, no_tok]
        test_markets.append((cid, winning_idx, tokens, prices))
        sc.resolution[cid] = tokens[winning_idx]
        sc.market_prices[cid] = prices

    # A separate pool of history markets (already resolved in the "past").
    history_markets = []
    for m in range(n_markets):
        cid = f"0xhist{m:05d}"
        winning_idx = rng.randint(0, 1)
        p = round(_clip(rng.gauss(0.5, 0.18), 0.1, 0.9), 3)
        history_markets.append((cid, winning_idx, [f"h{m}y", f"h{m}n"], [p, round(1 - p, 3)]))

    outcomes = ["Yes", "No"]

    def _make_position(cid, idx, tokens, prices, winning_idx, size):
        """Build a position dict; PnL is realized against the known resolution."""
        entry = prices[idx]
        payoff = 1.0 if idx == winning_idx else 0.0
        pct = (payoff - entry) / entry if entry else 0.0
        return {
            "condition_id": cid,
            "asset": tokens[idx],
            "outcome": outcomes[idx],
            "size": size,
            "avg_price": entry,
            "cur_price": payoff,  # resolved: token worth 1 or 0
            "initial_value": size * entry,
            "current_value": size * payoff,
            "cash_pnl": size * (payoff - entry),
            "percent_pnl": pct,
            "realized_pnl": size * (payoff - entry),
        }

    for t in range(n_traders):
        wallet = f"0x{t + 1:040x}"
        skill = _clip(rng.gauss(0.0, 0.5), -1.0, 1.0)
        base_size = rng.uniform(50, 3000) * (1 + max(0.0, skill))
        pick_winner_p = _clip(0.5 + 0.4 * skill, 0.05, 0.95)

        # History book (resolved) -> drives the trader's score.
        hist: list[dict] = []
        for cid, widx, tokens, prices in rng.sample(history_markets, rng.randint(5, 14)):
            idx = widx if rng.random() < pick_winner_p else 1 - widx
            size = base_size * (1 + max(0.0, skill) * rng.uniform(0, 1.5))
            hist.append(_make_position(cid, idx, tokens, prices, widx, round(size, 2)))
        sc.history_positions[wallet] = hist

        # Test picks (disjoint markets) -> drive consensus recommendations.
        # These are valued PRE-resolution (entry price only): the true outcome must
        # NOT leak into how the consensus is built — it is used only for scoring
        # results in the engine.
        test: list[dict] = []
        for cid, widx, tokens, prices in rng.sample(test_markets, rng.randint(4, 10)):
            idx = widx if rng.random() < pick_winner_p else 1 - widx
            size = round(base_size * (1 + max(0.0, skill) * rng.uniform(0, 1.5)), 2)
            entry = prices[idx]
            test.append(
                {
                    "condition_id": cid,
                    "asset": tokens[idx],
                    "outcome": outcomes[idx],
                    "size": size,
                    "avg_price": entry,
                    "cur_price": entry,
                    "initial_value": size * entry,
                    "current_value": size * entry,
                    "percent_pnl": 0.0,
                    "cash_pnl": 0.0,
                    "realized_pnl": 0.0,
                }
            )
        sc.test_positions[wallet] = test

    return sc
