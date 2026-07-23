"""Shared pytest fixtures. Runs fully offline: fixtures data + SQLite + memory cache."""

from __future__ import annotations

import asyncio
import json
import os
import pathlib

# --- Configure the environment BEFORE importing any app module -------------- #
# (app.config caches Settings on first import, so these must be set first.)
_TEST_DB = pathlib.Path(__file__).parent / "_test_polyflow.db"
os.environ.update(
    POLYFLOW_USE_FIXTURES="true",
    POLYFLOW_RUN_WORKER_IN_API="false",
    POLYFLOW_DATABASE_URL=f"sqlite+aiosqlite:///{_TEST_DB.as_posix()}",
    # Point Redis at a closed port so the cache cleanly falls back to in-memory.
    POLYFLOW_REDIS_URL="redis://127.0.0.1:1/0",
    POLYFLOW_LOG_LEVEL="WARNING",
)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def raw_samples() -> dict:
    return json.loads((FIXTURES_DIR / "raw_samples.json").read_text())


@pytest.fixture(scope="session", autouse=True)
def seeded_db():
    """Create the schema and run one full pipeline cycle once for the session."""
    if _TEST_DB.exists():
        _TEST_DB.unlink()

    from app.db.session import init_db
    from app.worker import pipeline

    async def _seed() -> None:
        await init_db()
        await pipeline.run_full_cycle()

    asyncio.run(_seed())
    yield
    if _TEST_DB.exists():
        _TEST_DB.unlink()


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
