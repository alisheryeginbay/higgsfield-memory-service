# Higgsfield Memory Service

By [Alisher](https://yeginbay.com)

A memory service for an AI agent. Ingests conversation turns, extracts
structured knowledge, and answers recall queries that decide what context
the agent sees on the next turn.

## Status

Scaffold-in-progress. See `CHANGELOG.md` for the iteration history. Architecture,
extraction strategy, and recall pipeline are documented here as each lands.

## Stack

- **Python 3.13 + FastAPI** — service layer
- **Postgres 18 + pgvector** — single backing store for relational memories,
  vector search, and BM25 (`tsvector`)
- **Anthropic Claude** (`claude-haiku-4-5` / `claude-sonnet-4-6`) — extraction
  and contradiction/merge decisions
- **Voyage AI `voyage-4-lite`** (1024 dims) — embeddings

## HTTP contract

| Method | Path                              | Purpose                                  |
|--------|-----------------------------------|------------------------------------------|
| GET    | `/health`                         | Liveness/readiness probe                 |
| POST   | `/turns`                          | Ingest a completed conversation turn     |
| POST   | `/recall`                         | Return formatted context for the agent   |
| POST   | `/search`                         | Structured memory search                 |
| GET    | `/users/{user_id}/memories`       | Inspect stored memories for a user       |
| DELETE | `/sessions/{session_id}`          | Delete all data for a session            |
| DELETE | `/users/{user_id}`                | Delete all data for a user               |

## Run

```bash
cp .env.example .env       # fill in ANTHROPIC_API_KEY and VOYAGE_API_KEY
docker compose up -d --build
until curl -sf http://localhost:8080/health; do sleep 1; done
```

Service listens on `http://localhost:8080`.

## Tests

```bash
uv sync --extra dev
uv run pytest
```
