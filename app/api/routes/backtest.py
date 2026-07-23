"""Backtesting endpoint.

Evaluates the ranking + recommendation engine on a resolved-market scenario and
reports hit-rate, ROI, calibration and edge over a crowd-favorite baseline.

The computation is CPU-bound and deterministic, so results are cached per
(min_confidence, top_n). The handler is a sync ``def`` so FastAPI runs it in a
threadpool and never blocks the event loop.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Query

from app.backtesting.engine import run_backtest
from app.schemas.models import BacktestOut

router = APIRouter(prefix="/backtest", tags=["backtest"])


@lru_cache(maxsize=32)
def _cached_backtest(min_confidence: float, top_n: int) -> dict:
    return run_backtest(min_confidence=min_confidence, top_n=top_n).as_dict()


@router.get("", response_model=BacktestOut)
def backtest(
    min_confidence: float = Query(0.6, ge=0.0, le=1.0),
    top_n: int = Query(100, ge=1, le=1000),
) -> BacktestOut:
    """Run (or return cached) backtest metrics for the given parameters."""
    return BacktestOut(**_cached_backtest(round(min_confidence, 3), top_n))
