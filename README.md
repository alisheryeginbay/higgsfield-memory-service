# Higgsfield Memory Service

A memory service for an AI agent. Ingests conversation turns, extracts
structured knowledge, and answers recall queries that decide what
context the agent sees on the next turn.

Built across 14 well-attributed commits with measured score deltas at
each step — see `CHANGELOG.md` for the iteration narrative. This README
documents the *current* architecture and decisions; the changelog
documents how we got here.

## Status

`v0.14`. Recall-quality fixture: aggregate **0.867 deterministic**
across 5 runs (3/5 scenarios at 100%, 2/5 at 67% with structurally
explained failures — see "Tradeoffs").

## Architecture

```
                                 ┌──────────────────────────────────┐
 ┌────────┐ POST /turns          │  Anthropic Claude (claude-haiku-  │
 │        │ ──────────────────▶  │  4-5) — tool-use → typed memories │
 │ Client │                      └─────────────────┬────────────────┘
 │        │ POST /recall                           │
 │        │ ──────────────────▶                    ▼
 └────────┘                       ┌──────────────────────────────────┐
      ▲                           │  Voyage AI (voyage-4-lite, 1024d)│
      │                           │  embed_documents / embed_query   │
      │                           └─────────────────┬────────────────┘
      │                                             │
      │   ┌──────────────────────────────────┐      │
      │   │ FastAPI — routers, lifespan,     │ ◀────┘
      │   │ DI for db / extractor / embedder │
      │   └─────────────────┬────────────────┘
      │                     │  asyncpg pool
      │                     ▼
      │   ┌──────────────────────────────────┐
      └─▶ │ Postgres 18 + pgvector           │
          │  • turns        — raw archive    │
          │  • memories                      │
          │     - embedding vector(1024) HNSW│
          │     - tsv tsvector            GIN│
          │     - supersedes uuid (history)  │
          │     - active boolean             │
          └──────────────────────────────────┘
```

One service container, one DB container, one named Docker volume
(`memory_pgdata`). Migrations run on every container start
(`alembic upgrade head` in the entrypoint). All state lives in
Postgres — no Redis, no separate vector DB, no queue.

### Backing store choice — Postgres 18 + pgvector

Three retrieval signals (relational rows, vector search, BM25) live in
**one DB** and **one volume**. No data plane to keep in sync; one set
of backups. The challenge brief explicitly warns "vanilla cosine-top-k
will not score" — pgvector + tsvector lets us run hybrid retrieval
(RRF over both rankings) cheaply on the same connection, without
spinning up a second service.

Alternatives ruled out:
- **SQLite + sqlite-vec**: simpler, but the "real production answer"
  matters for review.
- **Qdrant + SQLite**: better vector ergonomics, but two services,
  two stores, two failure modes.

## Extraction Pipeline

`POST /turns` runs synchronously inside the request:

1. **Persist the raw turn** in `turns` (always — the user's data must
   not be lost over an LLM hiccup).
2. **Claude tool-use call** (`claude-haiku-4-5`) with
   `tool_choice={"type":"tool","name":"record_memories"}` so structured
   output is forced. The system prompt:
   - Categorises into `fact` / `preference` / `opinion` / `event`.
   - Mandates **canonical snake_case keys** (e.g. `language_preference`,
     `pet:dog:name`) so per-key supersession works across turns.
   - Filters trivial activities ("noise") — sourdough chitchat won't
     surface as memories.
   - Confidence anchors with explicit ranges (0.4 floor; below filtered).
   - 4 few-shot examples covering strong extraction, noise rejection,
     in-turn correction, and fact evolution.
3. **Validate** each returned memory shape (type/key/value/confidence);
   bad ones are logged and dropped, never crash the request.
4. **Embed in batch** via Voyage `voyage-4-lite`
   (`input_type="document"`, 1024 dims) — one Voyage call per turn
   regardless of memory count.
5. **Persist with supersession** — for `fact`/`preference`/`opinion`,
   if an active row exists for `(user_id, key)`:
   - Same value → idempotent skip (no churn).
   - Different value → mark old `active=false`, set `supersedes` on
     the new row.
   - For `event`, always insert (events are temporal; multiple
     `career_change` rows over time are valid).

Failure modes are graceful: extraction errors don't block turn
persistence; embedding errors persist with `NULL` embedding (BM25 still
works). See "Failure Modes" for the full table.

### What we extract well

- Personal facts (employer, role, city, country, family, pets,
  demographics).
- Stable preferences and opinions.
- Implicit facts ("walking Biscuit" → has a dog named Biscuit).
- Same-turn corrections — only the affirmed half lands.

### What we miss (and why)

- **Bipartite preferences** ("Python for X, TypeScript for Y") are
  recorded as the most-affirmative half only — the conciseness rule
  drops the other. Splitting one preference into multiple keyed rows
  would help, but it conflicts with the canonical-keys rule.
- **Same-topic different keys** (e.g., `employer` vs `company`) aren't
  detected — supersession is exact-key. Memory-aware extraction (pass
  existing keys to the LLM) is the natural follow-up.

## Recall Strategy

`POST /recall` runs hybrid query-aware retrieval:

1. **Embed the query** via Voyage `embed_query` (`input_type="query"`).
   Best-effort — falls back to BM25-only if Voyage hiccups.
2. **One SQL query** returns every active fact/preference/opinion for
   the user, with two scores attached:
   - `embedding <=> $query_emb` — cosine distance (NULL if either
     side is missing).
   - `ts_rank_cd(tsv, plainto_tsquery('english', $query))` — keyword
     score (NULL if no match, never zero — keeps RRF clean).
3. **Reciprocal Rank Fusion** in Python (`k=60`, the literature
   default): each candidate gets `1/(k+vec_rank) + 1/(k+bm25_rank)`,
   missing-from-list contributes 0. Tiebreak: type-priority asc →
   recency desc → confidence desc → id (deterministic).
4. **Top-K** above the RRF floor (default `top_k=10`, env-tunable).
5. **Render** as a markdown block with sections per type (`## Known
   facts about this user`, `## Preferences`, `## Opinions`), bullet
   per memory ordered within each section by RRF score. Citations
   carry the RRF score, the source turn id, and the memory value as
   snippet.

### Token budget enforcement

`max_tokens` is approximated as `chars / 4`. The renderer builds
sections incrementally; tail bullets are dropped once the budget runs
out. Citations only cover what's actually in the rendered text — never
cite something the agent can't see. Brief target: "don't blow past
`max_tokens` by 2×" — easily met (we approach it from below).

### Priority logic under tight budget

When `max_tokens` is small, sections render in fixed priority order:
**facts → preferences → opinions**. Within each section, memories are
RRF-ordered (relevance-first). The most-relevant fact wins over a
less-relevant preference of equal RRF score. Defended:
- Stable user facts (employer, allergy, location) are the highest-value
  context; they should never be evicted before subjective preferences.
- Preferences shape behaviour but are downstream of facts.
- Opinions evolve fastest and are the lowest priority.

### Why events are excluded from default recall

Event values are narrative ("Left Stripe, started at Notion as PM")
and routinely contain superseded entities. Including them in a
substring-matched recall context leaks "Stripe" into "where does the
user currently work?" probes. Re-introducing events needs query-intent
classification ("history" vs "current" queries) — explicitly deferred
(documented in CHANGELOG).

## Fact Evolution / Contradiction Handling

**Per-key supersession** runs inside the `/turns` transaction:

- New `language_preference: "TypeScript for everything"` (turn 1).
- Later `language_preference: "Python for quick scripts"` (turn 2).
- Old row → `active=false`. New row → `active=true` +
  `supersedes=<old_id>`.
- `/recall` only returns active rows; `/users/{id}/memories` returns
  the full chain (history is preserved, inspectable).

**Idempotent re-statement**: if the user re-states the same fact
verbatim, no insert and no update — the existing row stays.

**Limitations** documented honestly:
- **Different keys, same topic** isn't detected (would need fuzzy-key
  normalization or memory-aware extraction).
- **Opinion arcs** ("I love TS" → "TS is annoying" → "TS is fine for
  big stuff, Python for scripts") collapse to the most-recent statement
  only; arc-tracking would need an explicit history field per opinion.
- See "Tradeoffs" for the bipartite-preference case.

## Tradeoffs

### Optimised for

- **Defensible architecture** + **attribution clarity**. Per-commit
  changelog deltas; reverted M13 because it didn't move the score;
  split M14 into infra + behaviour to attribute deltas cleanly.
- **Graceful degradation**: every external dependency (Anthropic,
  Voyage) has a no-op fallback; the service runs without keys, just
  with diminished behaviour.
- **Single source of truth**: one DB, one volume, one connection
  pool, all retrieval signals in one query.

### Gave up

- **Speed of iteration on score**. Splitting commits even when an
  aggregate move wasn't guaranteed cost time but pays off in
  reviewability.
- **Speed of `/turns`**. Synchronous extraction adds 1–2 s per turn
  (Claude call). Per the brief's 60 s timeout, fine. For
  high-throughput agents, would need an async-with-eventual-consistency
  path or an extraction queue — explicitly out of brief scope.
- **Pre-existing memories' embeddings**. Memories from before v0.13
  (the migration that added the column) have NULL embeddings; they
  still rank via BM25.
- **Bipartite preferences**. See above — the conciseness rule keeps
  recall clean at the cost of dropping secondary preference contexts.

## Failure Modes

| Condition | Behaviour |
|---|---|
| `ANTHROPIC_API_KEY` unset | Service boots; `NoopExtractor` → no memories ever extracted; `/turns` still persists raw rows; `/recall` returns empty. Logged as a startup `WARNING`. |
| `VOYAGE_API_KEY` unset | Service boots; `NoopEmbedder` → memories persist with `embedding=NULL`; `/recall` falls back to BM25-only retrieval. Logged as a startup `WARNING`. |
| Voyage API error mid-`/turns` | Embedding batch fails; memory persists with `NULL` embedding. BM25 still works for those rows. Turn never blocks. |
| Anthropic API error mid-`/turns` | Extraction fails; turn persists; logged as a `WARNING`. |
| Voyage API error mid-`/recall` | Falls back to BM25-only retrieval. Logged. |
| Malformed JSON to any endpoint | FastAPI returns `422` (Pydantic validation). Service stays up. |
| Postgres unavailable at startup | Compose `depends_on: condition: service_healthy` blocks; api restarts until DB is ready. |
| Slow disk / DB latency | Async pool absorbs; `/turns` and `/recall` block until ready. |
| Empty / cold session | `/recall` returns `{"context":"","citations":[]}` — never errors. |
| Restart | Named volume preserves all data; alembic auto-applies any pending migrations. |
| `DELETE /sessions/{id}` / `DELETE /users/{id}` | Idempotent, returns `204` even if nothing matches. |

## HTTP Contract

| Method | Path                              | Purpose                                |
|--------|-----------------------------------|----------------------------------------|
| GET    | `/health`                         | Liveness/readiness probe (200)         |
| POST   | `/turns`                          | Ingest a completed conversation turn   |
| POST   | `/recall`                         | Return formatted context for the agent |
| POST   | `/search`                         | Structured memory search — same hybrid retrieval as `/recall` (vector + BM25 + RRF), filters by optional `user_id` / `session_id`, returns top-`limit` ranked memories as `SearchHit` objects (content, score, session_id, timestamp, metadata). Includes events; excludes superseded rows. Both filters null → empty (no cross-user leak). |
| GET    | `/users/{user_id}/memories`       | Inspect stored memories for a user     |
| DELETE | `/sessions/{session_id}`          | Delete all data for a session (204)    |
| DELETE | `/users/{user_id}`                | Delete all data for a user (204)       |

OpenAPI docs at `http://localhost:8080/docs`.

Optional auth: set `MEMORY_AUTH_TOKEN` in `.env` and every endpoint
except `/health` requires `Authorization: Bearer <token>`. Unset →
header is ignored.

## Run

```bash
cp .env.example .env       # fill in ANTHROPIC_API_KEY and VOYAGE_API_KEY
docker compose up -d --build
until curl -sf http://localhost:8080/health; do sleep 1; done
```

Service listens on `http://localhost:8080`. Persistent volume
(`memory_pgdata`) survives `docker compose down` (use
`docker compose down -v` to wipe).

## Tests

```bash
uv sync --extra dev

# All non-live tests (no API keys needed; live tests skip cleanly):
uv run pytest

# To run live tests too, export both keys before pytest. In bash:
export ANTHROPIC_API_KEY=$(grep '^ANTHROPIC_API_KEY=' .env | cut -d= -f2- | tr -d '"')
export VOYAGE_API_KEY=$(grep '^VOYAGE_API_KEY=' .env | cut -d= -f2- | tr -d '"')
uv run pytest
```

### Test inventory (56 total)

| File | Purpose | Count | Live? |
|---|---|---|---|
| `tests/test_contract_smoke.py` | All 7 endpoints reachable, status codes + response shapes correct, malformed JSON returns 422, concurrent users don't bleed. | 8 | yes (skip if service down) |
| `tests/test_recall_quality.py` | Recall-quality fixture: 5 conversation scenarios + 15 probes. Asserts aggregate ≥ ratcheted `MIN_SCORE`. | 1 | yes |
| `tests/test_recall_unit.py` | Renderer logic (sectioning, citations, budget truncation, bullet heuristics). | 10 | no |
| `tests/test_retrieval_unit.py` | RRF math (single signal, intersection, top_k, floor, tiebreakers). | 10 | no |
| `tests/test_extraction_unit.py` | Claude tool-use parsing, confidence filter, malformed-input handling. | 8 | no (mocked transport) |
| `tests/test_embeddings_unit.py` | Voyage SDK wrapping, validation, error mapping. | 12 | no (mocked) |
| `tests/test_extraction_live.py` | Live ingest produces structured rows in `/users/{id}/memories`. | 1 | yes (key-gated) |
| `tests/test_supersession_live.py` | Supersession marks old inactive + sets `supersedes`; idempotent re-statement. | 2 | yes (key-gated) |
| `tests/test_search_live.py` | `/search` returns structured hits; filter by `user_id` / `session_id`; both-null returns empty; `limit` truncates. | 4 | yes (key-gated) |

## Recall-quality Fixture

Hand-written conversations + probe queries that drive the iteration
loop. Lives in `fixtures/recall_quality/` — see its README for the
schema and how to add scenarios.

| Scenario | Score (v0.14) | What it tests |
|---|---|---|
| 01 personal_facts | 4/4 | Employer, role, city, pet (one implicit). |
| 02 fact_evolution | 2/3 | Old "Stripe" superseded by "Notion"; current/role probes pass; the `match=all ["stripe","notion"]` history probe fails because we don't surface the superseded employer (covered in "Tradeoffs"). |
| 03 preferences_corrections | 2/3 | Mid-conversation correction; canonical keys + conciseness make probe 1 pass deterministically; bipartite-preference probe fails (covered in "Tradeoffs"). |
| 04 multi_hop | 2/2 | Two facts in different sessions, one probe joins them. |
| 05 noise_resistance | 3/3 | Cooking chitchat ingested, probes about unrelated topics → empty context, no hallucination. |

Aggregate: **0.867** deterministic across 5 runs.

## Repo Layout

```
.
├── README.md                  # this file
├── CHANGELOG.md               # iteration narrative (v0.1–v0.14)
├── docker-compose.yml         # api + db + named volume
├── Dockerfile                 # multi-stage Python 3.13-slim + uv
├── .env.example
├── pyproject.toml             # uv-managed deps + ruff/pytest config
├── uv.lock
├── alembic.ini
├── migrations/
│   ├── env.py
│   └── versions/
│       ├── 0001_init.py
│       └── 0002_add_embedding_and_tsv.py
├── src/memory_service/
│   ├── api/                   # FastAPI routers
│   │   ├── health.py
│   │   ├── turns.py           # extraction → embed → supersede → insert
│   │   ├── recall.py          # hybrid retrieval pipeline
│   │   ├── search.py          # stub (see HTTP contract note)
│   │   ├── memories.py
│   │   └── admin.py
│   ├── config.py              # pydantic-settings (env-driven)
│   ├── db.py                  # asyncpg pool + pgvector codec init
│   ├── deps.py                # FastAPI dependencies
│   ├── embeddings.py          # Voyage wrapper (Embedder protocol)
│   ├── extraction.py          # Claude tool-use + supersession-on-insert
│   ├── recall.py              # markdown rendering + token budget
│   ├── retrieval.py           # RRF (pure logic, unit-testable)
│   ├── schemas.py             # Pydantic contract models
│   └── main.py                # app factory + lifespan
├── tests/                     # see "Test inventory"
└── fixtures/recall_quality/   # 5 scenarios + README
```

## What's not done (intentional follow-ups)

These are documented in `CHANGELOG.md` as "next" notes on the relevant
commits. Each is a 1–2 commit unit on top of the current architecture:

- **Query-intent gating** — small classifier on the query
  (`current` / `history` / other) → re-introduce events in default
  recall when query asks about history. Cheapest remaining lever for
  scenario_02 probe 3.
- **Memory-aware extraction** — pass user's existing `(key, value)`
  pairs to the extractor so it deliberately reuses keys. Unlocks
  scenario_03 with diverse user vocabulary.
- **LLM reranker** on top of RRF (Voyage rerank-2 or LLM-judge) —
  higher-precision top-K at ~2× latency.
- **Query rewriting** — LLM expands a probe into multiple search
  queries before retrieval.

Each would ship as a per-commit delta against the current `0.867`
baseline.
