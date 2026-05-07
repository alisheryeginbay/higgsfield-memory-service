# Changelog

Iteration history for the memory service. Newest first. Each entry tracks
a single commit — what changed, why, what was observed, and what comes
next.

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
