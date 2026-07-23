"""Market ingestion: pull the active market catalog from Gamma and upsert it.

Supports an optional category filter (``POLYFLOW_CATEGORY``). When set to
``sports`` the pipeline keeps only sports markets, so the entire platform —
markets, traders, signals — becomes sports-only.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.polymarket import get_client
from app.config import settings
from app.db.models import Market
from app.logging import get_logger

log = get_logger(__name__)

# Strong sports signals: leagues, sports, and matchup markers.
_SPORTS_TERMS = (
    "nba", "nfl", "nhl", "mlb", "mls", "ncaa", "ufc", "mma", "epl", "uefa", "fifa",
    "premier league", "la liga", "serie a", "bundesliga", "ligue 1", "champions league",
    "europa", "world cup", "super bowl", "stanley cup", "world series", "playoff",
    "finals", "grand prix", "formula 1", "olympic", "wimbledon", "ryder cup",
    "soccer", "basketball", "baseball", "hockey", "tennis", "golf", "cricket",
    "rugby", "boxing", "nascar", "pga", "atp", "wta", "wnba", "sport",
    "championship", "tournament", "qualify", "cup final", "grand slam", "heavyweight",
)


def _haystack(m: dict) -> str:
    return " ".join(str(m.get(k) or "") for k in ("category", "slug", "question")).lower()


def is_sports(m: dict) -> bool:
    """Heuristic: does this market look like a sports market?"""
    hay = _haystack(m)
    if any(term in hay for term in _SPORTS_TERMS):
        return True
    # Team-vs-team matchup markers (very common for sports).
    return " vs " in hay or " vs. " in hay or " @ " in hay


def matches_category(m: dict, category: str) -> bool:
    category = category.strip().lower()
    if not category:
        return True
    if category in ("sport", "sports"):
        return is_sports(m)
    return category in _haystack(m)


async def sync_markets(session: AsyncSession, limit: int | None = None) -> list[str]:
    """Fetch active markets (optionally filtered by category) and upsert them."""
    client = get_client()
    limit = limit or settings.market_limit
    category = settings.category.strip()

    # When filtering, over-fetch so we still end up with ~`limit` matches.
    fetch = min(limit * 8, 800) if category else limit
    markets = await client.get_markets(limit=fetch, active=True)
    if category:
        matched = [m for m in markets if matches_category(m, category)]
        if len(matched) < 5:
            # Help diagnose a heuristic miss: show what the live data actually looks like.
            samples = [
                f"cat={m.get('category')!r} q={(m.get('question') or '')[:48]!r}"
                for m in markets[:8]
            ]
            log.warning(
                "category=%s matched only %d of %d fetched markets. Samples: %s",
                category, len(matched), len(markets), samples,
            )
        markets = matched[:limit]

    token_ids: list[str] = []
    for m in markets:
        if not m.get("condition_id"):
            continue
        await session.merge(
            Market(
                condition_id=m["condition_id"],
                question=m["question"][:512],
                slug=m.get("slug"),
                category=m.get("category"),
                outcomes=m.get("outcomes", []),
                clob_token_ids=m.get("clob_token_ids", []),
                outcome_prices=m.get("outcome_prices", []),
                volume=m.get("volume", 0.0),
                liquidity=m.get("liquidity", 0.0),
                active=m.get("active", True),
                closed=m.get("closed", False),
                start_date=m.get("start_date"),
                end_date=m.get("end_date"),
            )
        )
        token_ids.extend(str(t) for t in m.get("clob_token_ids", []) if t)

    await session.commit()
    suffix = f" [{category}]" if category else ""
    log.info(
        "sync_markets: upserted %d markets (%d tokens)%s", len(markets), len(token_ids), suffix
    )
    return token_ids
