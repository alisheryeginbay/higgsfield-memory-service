# Changelog

Iteration history for the memory service. Newest first. Each entry tracks
a single commit — what changed, why, what was observed, and what comes
next.

## v0.5 — feat: docker + compose stack (api + pgvector pg18 with persistent volume) (2026-05-07)

**What changed:** Multi-stage `Dockerfile` (uv 0.11.11 →
`python:3.13-slim-bookworm` builder doing `uv sync --frozen --no-dev` →
slim runtime). Runtime CMD runs `alembic upgrade head` then
`exec uvicorn` so migrations apply on every cold start and uvicorn becomes
PID 1. `docker-compose.yml` spins up `pgvector/pgvector:pg18` with a
`pg_isready` healthcheck and a named volume, plus the `api` with a
urllib-based `/health` healthcheck and `depends_on: db: service_healthy`.
`.dockerignore` updated to keep `README.md` in the build context (hatchling
needs it).

**Observed:** Postgres 18 changed the volume mount convention — it expects
`/var/lib/postgresql` (not `/var/lib/postgresql/data`) so the major-version
subdirectory layout works. Fixed in compose.

**Verified end-to-end through compose:** `/health` 200, `/turns` 201 + UUID,
`/recall` returns `{"context":"","citations":[]}`, `/search` returns
`{"results":[]}`, `/users/{u}/memories` returns `{"memories":[]}`, both
DELETEs return 204, malformed body returns 422 (no crash). Persistence:
wrote a turn, `compose down` (no `-v`), `compose up -d`, row still in
`turns`.

**Next:** Lock the contract behaviour into a smoke test suite.

## v0.4 — feat: stub all 7 contract endpoints (2026-05-07)

**What changed:** Five new routers under `src/memory_service/api/`.
`/turns` inserts a row with messages serialised to JSONB and returns the
new UUID. `/recall` and `/search` return well-formed empty payloads
(cold-session contract: never error). `/users/{user_id}/memories` queries
`memories` directly (returns `[]` until extraction lands).
`/sessions/{session_id}` and `/users/{user_id}` issue idempotent DELETEs
and return 204. All endpoints (except `/health`) are gated by the optional
bearer-auth dependency.

**Verified:** All 7 routes register and resolve to handlers — `GET /health`,
`POST /turns`, `POST /recall`, `POST /search`,
`GET /users/{user_id}/memories`, `DELETE /sessions/{session_id}`,
`DELETE /users/{user_id}`.

**Next:** Dockerfile + docker-compose.yml so `docker compose up` boots the
whole stack.

## v0.3 — feat: db pool + alembic init migration (turns, memories, pgvector) (2026-05-07)

**What changed:** `db.py` opens an asyncpg pool that registers the pgvector
codec on every fresh connection (so vector columns added in later migrations
Just Work). Lifespan opens/closes the pool on `app.state.db`; `get_db`
dependency for routes. Alembic configured async-mode (`migrations/env.py`
builds a SQLAlchemy URL via a new `Settings.sqlalchemy_url` property).
`0001_init` creates `turns` and `memories` tables plus `vector` and
`uuid-ossp` extensions, with indexes on the access patterns we'll exercise
first (`turns(session_id)`, `turns(user_id, ts DESC)`,
`memories(user_id, active)`, `memories(user_id, key)`,
`memories(source_session)`).

**Verified:** `alembic history` shows `0001_init` at head; `alembic upgrade
head --sql` renders valid SQL offline.

**Next:** Wire all 7 contract endpoints — `/turns` insert, cold stubs for
recall/search/memories, idempotent admin DELETEs.

## v0.2 — feat: app factory + contract schemas + /health (2026-05-07)

**What changed:** `schemas.py` with Pydantic v2 models for all 7 contract
endpoints (`Message`, `TurnIn/Out`, `RecallIn/Out` + `Citation`,
`SearchIn/Out` + `SearchHit`, `Memory` + `MemoriesOut`, `HealthOut`,
`ErrorOut`). FastAPI `create_app()` factory with lifespan, env-driven
`Settings` via pydantic-settings, optional bearer-token auth dependency,
global exception handler that maps unhandled errors to 500 + `ErrorOut`
(no stack-trace leak). `/health` router wired up.

**Verified:** TestClient probe of `/health` returns `{"status":"ok"}`.

**Next:** asyncpg pool + pgvector codec + Alembic migration with `turns`
and `memories` tables.

## v0.1 — chore: scaffold project skeleton + lock stack (2026-05-07)

**What changed:** Initialised the project skeleton — `pyproject.toml`,
`.gitignore`, `.dockerignore`, `.env.example`, `README.md`, this changelog,
and an empty `src/memory_service/` package layout. No service code yet.

**Stack pinned:** Python 3.13 + FastAPI `~=0.136`, Postgres 18 + pgvector,
Anthropic Claude (`claude-haiku-4-5` / `claude-sonnet-4-6`) for extraction,
Voyage AI `voyage-4-lite` (1024 dims) for embeddings, asyncpg + Alembic,
uv + ruff. Versions verified current as of 2026-05-07.

**Why this stack:** Python has the deepest LLM/extraction toolbox; pgvector
keeps relational memories, vector search, and BM25 (`tsvector`) in one DB
and one Docker volume; Voyage `voyage-4-lite` chosen over OpenAI-small to
avoid the cosine-top-k trap.

**Next:** Pydantic schemas for the HTTP contract + FastAPI app factory +
`/health`.
