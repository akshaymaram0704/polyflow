# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first (better layer caching).
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

# Copy the application.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

# Run as a non-root user.
RUN useradd --create-home --uid 10001 polyflow \
    && chown -R polyflow:polyflow /app
USER polyflow

EXPOSE 8000

# Default: run the API. The worker service overrides the command in compose.
ENTRYPOINT ["./entrypoint.sh"]
CMD ["api"]
