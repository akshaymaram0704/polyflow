#!/usr/bin/env bash
# Container entrypoint. Usage: entrypoint.sh [api|worker|migrate]
set -euo pipefail

ROLE="${1:-api}"

run_migrations() {
    echo "[entrypoint] Applying database migrations..."
    alembic upgrade head
}

case "$ROLE" in
    api)
        run_migrations
        echo "[entrypoint] Starting API (uvicorn)..."
        exec uvicorn app.main:app --host 0.0.0.0 --port 8000
        ;;
    worker)
        # Give the API a moment to run migrations on a cold start.
        echo "[entrypoint] Starting worker (scheduler + price streamer)..."
        exec python -m app.worker.run
        ;;
    migrate)
        run_migrations
        ;;
    *)
        echo "[entrypoint] Unknown role: $ROLE (expected api|worker|migrate)" >&2
        exec "$@"
        ;;
esac
