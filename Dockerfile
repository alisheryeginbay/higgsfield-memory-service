# syntax=docker/dockerfile:1.7

# --- Pull a pinned uv binary ----------------------------------------------
FROM ghcr.io/astral-sh/uv:0.11.11 AS uv


# --- Builder: resolve + install deps + project into a venv ----------------
FROM python:3.13-slim-bookworm AS builder

WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

# Project metadata + lockfile first for layer caching.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

RUN uv sync --frozen --no-dev


# --- Runtime --------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

# Apply pending migrations, then start the server. `exec` so uvicorn is PID 1
# and gets SIGTERM directly on `docker stop`.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn memory_service.main:app --host 0.0.0.0 --port 8080"]
