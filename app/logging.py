"""Minimal structured logging setup."""

from __future__ import annotations

import logging
import sys

from app.config import settings

_CONFIGURED = False


def configure_logging() -> None:
    """Configure root logging once, idempotently."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    root.handlers[:] = [handler]

    # Tame noisy third-party loggers (keep our app.* logs at the chosen level).
    for noisy in (
        "httpx", "httpcore", "websockets.client",
        "apscheduler.scheduler", "apscheduler.executors.default", "apscheduler",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
