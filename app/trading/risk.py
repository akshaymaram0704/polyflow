"""Pure order-planning / risk logic (no DB, no network — fully unit-testable).

Given the candidate recommendations and the current day's exposure, decide which
orders to place and at what size, enforcing the configured risk limits. Every
candidate yields a decision with an explicit reason, so the executor (and tests)
can see exactly why an order was sized, skipped or capped.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskParams:
    min_confidence: float
    max_order_usd: float
    max_daily_usd: float
    max_open_positions: int


@dataclass
class PlannedOrder:
    recommendation_id: int | None
    condition_id: str | None
    asset: str
    outcome: str | None
    price: float
    action: str  # "submit" | "skip"
    size_usd: float
    reason: str


def plan_orders(
    candidates: list[dict],
    params: RiskParams,
    *,
    spent_today: float,
    open_positions: int,
    held_assets: set[str] | None = None,
) -> list[PlannedOrder]:
    """Return a sizing/skip decision for each candidate recommendation.

    ``candidates`` are dicts with: id, condition_id, asset, outcome, direction,
    confidence, price. They should already be ordered by desired priority
    (typically confidence descending).
    """
    held = set(held_assets or set())
    remaining_daily = max(0.0, params.max_daily_usd - spent_today)
    open_count = open_positions
    plans: list[PlannedOrder] = []

    for c in candidates:
        asset = c["asset"]
        price = float(c.get("price") or 0.0)
        base = PlannedOrder(
            recommendation_id=c.get("id"),
            condition_id=c.get("condition_id"),
            asset=asset,
            outcome=c.get("outcome"),
            price=price,
            action="skip",
            size_usd=0.0,
            reason="",
        )

        if c.get("direction", "BUY").upper() != "BUY":
            base.reason = "non-buy direction"
        elif float(c.get("confidence", 0)) < params.min_confidence:
            base.reason = f"confidence<{params.min_confidence}"
        elif asset in held:
            base.reason = "already holding asset"
        elif open_count >= params.max_open_positions:
            base.reason = "max open positions reached"
        elif remaining_daily <= 0:
            base.reason = "daily budget exhausted"
        elif price <= 0.0 or price >= 1.0:
            base.reason = "invalid price"
        else:
            size_usd = round(min(params.max_order_usd, remaining_daily), 2)
            base.action = "submit"
            base.size_usd = size_usd
            base.reason = "ok"
            remaining_daily -= size_usd
            open_count += 1
            held.add(asset)

        plans.append(base)

    return plans
