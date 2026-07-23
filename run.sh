#!/usr/bin/env bash
# PolyFlow launcher.  Usage:
#   ./run.sh          # LIVE Polymarket data (needs open internet)
#   ./run.sh offline  # synthetic fixtures (no network needed)
set -e
cd "$(dirname "$0")"

# Activate the local virtualenv if present.
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if [ "$1" = "offline" ]; then
  export POLYFLOW_USE_FIXTURES=true
  echo "[run.sh] Mode: OFFLINE (synthetic fixtures)"
else
  export POLYFLOW_USE_FIXTURES=false
  echo "[run.sh] Mode: LIVE (real Polymarket data)"
fi

export POLYFLOW_DATABASE_URL="${POLYFLOW_DATABASE_URL:-sqlite+aiosqlite:///./polyflow_live.db}"
export POLYFLOW_RUN_WORKER_IN_API="${POLYFLOW_RUN_WORKER_IN_API:-true}"
export POLYFLOW_MARKET_LIMIT="${POLYFLOW_MARKET_LIMIT:-150}"
# Keep the first live pull gentle on Polymarket's rate limits.
export POLYFLOW_TOP_TRADERS="${POLYFLOW_TOP_TRADERS:-300}"
export POLYFLOW_HTTP_CONCURRENCY="${POLYFLOW_HTTP_CONCURRENCY:-4}"

echo "[run.sh] Open http://localhost:8000/  (Ctrl+C to stop)"
exec python -m uvicorn app.main:app --port 8000
