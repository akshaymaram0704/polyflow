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

# Leagues / sports / events.
_SPORTS_TERMS = (
    "nba", "nfl", "nhl", "mlb", "mls", "ncaa", "ufc", "mma", "epl", "uefa", "fifa",
    "premier league", "la liga", "serie a", "bundesliga", "ligue 1", "champions league",
    "europa", "world cup", "super bowl", "stanley cup", "world series", "playoff",
    "grand prix", "formula 1", " f1 ", "olympic", "wimbledon", "ryder cup",
    "soccer", "basketball", "baseball", "hockey", "tennis", "golf", "cricket",
    "rugby", "boxing", "nascar", "pga", "atp", "wta", "wnba", "sport",
    "championship", "tournament", "qualify", "grand slam", "heavyweight", "playoffs",
    # generic event words (guarded by the exclusion list below)
    "win the", "to win", "champion", "vs", " @ ", "beat the", "defeat", "advance",
    "clinch", "mvp", "relegat", "semifinal", "quarterfinal", "the cup", "the final",
)

# Common team / club / athlete names so "Will the Lakers win?" is caught.
_TEAMS = (
    "lakers", "celtics", "warriors", "nuggets", "bucks", "heat", "knicks", "76ers", "sixers",
    "mavericks", "suns", "clippers", "nets", "bulls", "cavaliers", "thunder", "timberwolves",
    "pelicans", "grizzlies", "chiefs", "eagles", "49ers", "niners", "cowboys", "ravens", "bills",
    "bengals", "packers", "lions", "dolphins", "patriots", "steelers", "vikings", "jaguars",
    "chargers", "rams", "seahawks", "commanders", "texans", "browns", "yankees", "dodgers",
    "red sox", "astros", "braves", "mets", "cubs", "phillies", "padres", "guardians", "rangers",
    "oilers", "panthers", "bruins", "avalanche", "maple leafs", "golden knights", "real madrid",
    "barcelona", "man city", "manchester", "liverpool", "arsenal", "chelsea", "tottenham",
    "bayern", "psg", "juventus", "inter milan", "ac milan", "dortmund", "messi", "ronaldo",
    "lebron",
)

# If any of these appear, it's NOT sports (guards the broad generic terms above).
_NOT_SPORTS = (
    "election", "president", "trump", "biden", "senate", "congress", "governor", "fed ",
    "interest rate", "bitcoin", "ethereum", "crypto", "gdp", "inflation", "nominee", "approval",
    "supreme court", "shutdown", "tariff", "recession", "stock", "nasdaq",
)


def _haystack(m: dict) -> str:
    return " ".join(str(m.get(k) or "") for k in ("category", "slug", "question")).lower()


def is_sports(m: dict) -> bool:
    """Heuristic: does this market look like a sports market?"""
    hay = _haystack(m)
    if any(term in hay for term in _NOT_SPORTS):
        return False
    if any(term in hay for term in _SPORTS_TERMS):
        return True
    if any(team in hay for team in _TEAMS):
        return True
    return " vs " in hay or " vs. " in hay


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
    fetch = min(limit * 6, 1200) if category else limit
    markets = await client.get_markets(limit=fetch, active=True)

    # Also pull markets ending SOON (next ~2 days) — this is where live/in-play
    # matches live, regardless of volume. Use Gamma's end-date filter directly.
    from datetime import timedelta

    from app.db.base import utcnow

    n = utcnow()
    soon_hi = (n + timedelta(days=2)).isoformat()
    soon_lo = (n - timedelta(hours=6)).isoformat()
    for keys in (
        {"end_date_max": soon_hi, "end_date_min": soon_lo},
        {"endDateMax": soon_hi},  # alternate param spelling
    ):
        try:
            soon = await client.get_markets(
                limit=min(fetch, 400), active=True, order="endDate",
                ascending=True, extra_params=keys,
            )
            seen = {m.get("condition_id") for m in markets}
            added = [m for m in soon if m.get("condition_id") not in seen]
            markets.extend(added)
            if added:
                log.info("sync_markets: +%d soon-ending markets via %s", len(added), list(keys)[0])
        except Exception as exc:  # noqa: BLE001 - non-fatal; volume set still works
            log.warning("soon-ending fetch failed: %s", exc)

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
        # Keep a generous cap so live/in-play matches aren't truncated away.
        markets = matched[: max(limit, 800)]

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
