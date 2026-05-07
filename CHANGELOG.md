# Changelog

Iteration history for the memory service. Newest first. Each entry tracks
a single commit — what changed, why, what was observed, and what comes
next.

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
