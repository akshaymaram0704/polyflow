"""Paper/live PnL tracking — closes the loop from recommendation to outcome.

Two mechanisms:
  * **Mark-to-market** — open orders are valued at the current outcome price, so
    you can see running (unrealized) PnL at any time.
  * **Settlement** — when a market resolves, the order is graded against the
    winning outcome and its realized PnL is locked in (status -> ``settled``).

The arithmetic lives in tiny pure helpers (``settlement_pnl``, ``mark``,
``winning_asset``) that are unit-tested directly; the DB functions just apply them.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Market, Order
from app.logging import get_logger

log = get_logger(__name__)

_RESOLVED_THRESHOLD = 0.99  # a token priced at/above this is treated as the winner
_ACTIVE_STATES = ("submitted", "filled")


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def settlement_pnl(
    order_asset: str, winning_asset: str | None, size_shares: float, size_usd: float
) -> float:
    """Realized PnL at resolution: shares pay 1.0 if they won, else 0.0."""
    payoff = size_shares * (1.0 if order_asset == winning_asset else 0.0)
    return round(payoff - size_usd, 2)


def mark(size_shares: float, size_usd: float, cur_price: float) -> float:
    """Unrealized PnL of an open position marked at the current price."""
    return round(size_shares * cur_price - size_usd, 2)


def winning_asset(
    clob_token_ids: list, outcome_prices: list, threshold: float = _RESOLVED_THRESHOLD
) -> str | None:
    """Return the resolved winning token id, or None if the market isn't resolved."""
    if not clob_token_ids or not outcome_prices:
        return None
    for tok, price in zip(clob_token_ids, outcome_prices, strict=False):
        try:
            if float(price) >= threshold:
                return str(tok)
        except (TypeError, ValueError):
            continue
    return None


# --------------------------------------------------------------------------- #
# DB operations
# --------------------------------------------------------------------------- #
async def _price_and_winner_maps(
    session: AsyncSession, condition_ids: set[str]
) -> tuple[dict[str, float], dict[str, str]]:
    """Build asset->current_price and asset->winning (if resolved) maps."""
    if not condition_ids:
        return {}, {}
    rows = (
        await session.execute(select(Market).where(Market.condition_id.in_(condition_ids)))
    ).scalars()
    price_map: dict[str, float] = {}
    winner_map: dict[str, str] = {}
    for m in rows:
        tokens, prices = m.clob_token_ids or [], m.outcome_prices or []
        for tok, price in zip(tokens, prices, strict=False):
            price_map[str(tok)] = float(price)
        resolved = m.closed or (prices and max(float(p) for p in prices) >= _RESOLVED_THRESHOLD)
        if resolved:
            w = winning_asset(tokens, prices)
            if w:
                for tok in tokens:
                    winner_map[str(tok)] = w
    return price_map, winner_map


async def settle_orders(session: AsyncSession, mode: str | None = None) -> dict:
    """Settle open orders whose markets have resolved. Returns a summary."""
    stmt = select(Order).where(Order.status.in_(_ACTIVE_STATES))
    if mode:
        stmt = stmt.where(Order.mode == mode)
    orders = list((await session.execute(stmt)).scalars())

    _, winner_map = await _price_and_winner_maps(
        session, {o.condition_id for o in orders if o.condition_id}
    )

    settled, realized_total = 0, 0.0
    for o in orders:
        winner = winner_map.get(o.asset)
        if winner is None:
            continue  # market not resolved yet
        o.pnl = settlement_pnl(o.asset, winner, o.size_shares, o.size_usd)
        o.status = "settled"
        o.detail = {**(o.detail or {}), "settled_winner": winner}
        realized_total += o.pnl
        settled += 1

    await session.commit()
    log.info("settle_orders: settled %d orders, realized %.2f USD", settled, realized_total)
    return {"settled": settled, "realized_pnl": round(realized_total, 2)}


async def pnl_summary(session: AsyncSession, mode: str = "paper") -> dict:
    """Portfolio PnL for a mode: realized (settled) + unrealized (mark-to-market)."""
    orders = list(
        (
            await session.execute(
                select(Order).where(
                    Order.mode == mode, Order.status.notin_(("rejected", "skipped"))
                )
            )
        ).scalars()
    )
    price_map, _ = await _price_and_winner_maps(
        session, {o.condition_id for o in orders if o.condition_id}
    )

    invested = realized = unrealized = current_value = 0.0
    n_open = n_settled = wins = 0
    for o in orders:
        invested += o.size_usd
        if o.status == "settled":
            n_settled += 1
            realized += o.pnl
            wins += 1 if o.pnl > 0 else 0
        else:
            n_open += 1
            cur_price = price_map.get(o.asset, o.price)
            current_value += o.size_shares * cur_price
            unrealized += mark(o.size_shares, o.size_usd, cur_price)

    total_pnl = round(realized + unrealized, 2)
    return {
        "mode": mode,
        "orders": len(orders),
        "open": n_open,
        "settled": n_settled,
        "invested_usd": round(invested, 2),
        "open_current_value_usd": round(current_value, 2),
        "realized_pnl": round(realized, 2),
        "unrealized_pnl": round(unrealized, 2),
        "total_pnl": total_pnl,
        "roi": round(total_pnl / invested, 4) if invested else 0.0,
        "settled_win_rate": round(wins / n_settled, 4) if n_settled else None,
    }
