# Changelog

Iteration history for the memory service. Newest first. Each entry tracks
a single commit — what changed, why, what was observed, and what comes
next.

## v0.10 — feat: recall surfaces user memories as structured context (2026-05-07)

**What changed:** `/recall` now actually does something. New
`src/memory_service/recall.py` renders a user's active memories into
markdown sections (Known facts / Preferences / Opinions / Significant
events), one bullet per memory, with a citation list whose ordering
matches the rendered context. Token budget approximated via chars/4
with type-priority truncation (facts → preferences → opinions →
events; recency tiebreaker within each type). Real handler in
`api/recall.py` replaces the stub. Cold sessions and unknown users
still return `{"context":"","citations":[]}`.

**Recall-quality score: 0.200 → 0.733** (measured against the live
stack with extraction enabled).

| Scenario | Score | Notes |
|---|---|---|
| 01 personal_facts | 4/4 (1.00) | Employer / role / city / pet all surfaced. |
| 02 fact_evolution | 1/3 (0.33) | Only `career_history` passes. Both employers active → forbidden_any "stripe" hits. |
| 03 preferences_corrections | 1/3 (0.33) | Same pattern: rejected TS preference still active alongside the corrected Python one. |
| 04 multi_hop | 2/2 (1.00) | Render-everything strategy accidentally solves multi-hop because both facts are in the same memory pile. |
| 05 noise_resistance | 3/3 (1.00) | No memories → empty context, no hallucination. |

**What's still failing and why:** scenarios 2 and 3 rely on
**supersession** — old "Stripe" / "TypeScript" memories are still
`active=true` and surface in the rendered context, tripping
`forbidden_any`. Fixing that is a deliberate separate commit so the
next score delta is cleanly attributable.

**MIN_SCORE bumped:** 0.683 (measured 0.733 minus 0.05 slack for LLM
jitter — locks the floor without making one-probe flakes break CI).

**Tests:** 10 new renderer unit tests in `tests/test_recall_unit.py`
cover section ordering, citation alignment, the `(key)` parenthetical
heuristic, event date prefixes, and tight-budget truncation. Full
suite: 27 passed + 1 skipped (live extraction, env-gated).

**Next:** supersession on insert — when an extracted memory shares a
key with an existing active one, mark the old as `active=false` with
`supersedes` filled in. That should kill the `forbidden_any` failures
in scenarios 2 and 3 and unlock the next chunk of recall-quality
score.

## v0.9 — fix(extraction): tighten prompt with few-shot examples to suppress noise (2026-05-07)

**What changed:** Rewrote the extraction system prompt and tool description.
Added 4 few-shot examples (strong extraction, noise rejection, in-turn
correction, fact evolution) with `<turn>` / `<extracted>` / `<reasoning>`
XML structure. Tightened the `event` definition to require *significant*
life changes, not day-to-day activity. Added explicit anchors: "if in
doubt, omit", "single mention ≠ lasting preference", "value is a concise
assertion not a sentence". Beefed up the tool description from 1 sentence
to a 6-sentence usage guide (per Anthropic's own best-practice doc:
descriptions are by far the largest lever on tool-use quality).

**Why:** v0.8 worked well on explicit facts but extracted noise from
chitchat ("sourdough day 4" → 2 memories). The brief grades on
noise-resistance; the recall-quality fixture's scenario_05 specifically
tests it. Better to fix this at extraction time than try to filter it
out at recall time.

**Probe-based eval (5 manual ingests, before / after):**

| Probe | Before | After |
|---|---|---|
| Implicit dog + city ("walking Biscuit through Tiergarten") | 3 (incl. inferred Berlin) | 2 (dropped ambiguous inference) |
| Preference correction (TS → Python for scripts) | 1 verbose | 1 clean |
| Noise (sourdough starter day 4) | **2 trivial memories** | **0 memories** ✅ |
| Fact evolution single turn (Stripe → Notion + role change) | 4 split events | 3 consolidated (current facts + one `career_change` event) |
| Stacked dietary (allergy + vegetarian + duration) | n/a | 3 clean canonical-key memories |

The "lost Berlin inference" is not really a regression — Tiergarten alone
is ambiguous (visiting vs living), and scenario_01 in the recall fixture
has an explicit "based in Berlin" from an earlier turn that will populate
the city fact directly.

**Research basis (notes for the interview):** Anthropic tool-use docs say
descriptions are the largest lever; few-shot examples are the most
reliable consistency lever for ambiguous judgments (Anthropic + the
SurePrompts few-shot guide). Synthius-Mem and Memori both stress
"filtering low-signal records" at write time — bloated memory hurts
downstream recall more than misses do. Synthius-Mem's framing: "a
persona that perfectly remembers every trivia detail but bloats to
unusable size is worse than one that captures the essential character".

**Recall-quality score:** still **0.200** — `/recall` is still a stub.
Move comes in M9.

**Verified:** unit tests still pass (mocked transport unaffected by
prompt changes). Live probes show the noise issue is fixed and the other
categories improved or held.

**Next:** wire `/recall` to the memories table — fetch the user's active
memories, render a "Known facts about this user" block, return as
context. First commit where the score actually moves.

## v0.8 — feat: extraction pipeline (Claude tool-use → typed memories) (2026-05-07)

**What changed:** New `src/memory_service/extraction.py` —
`ClaudeExtractor` calls `claude-haiku-4-5` with a single tool
(`record_memories`) and `tool_choice` forcing structured output. Each
returned memory has `type ∈ {fact, preference, opinion, event}`, a
snake_case `key`, a concise `value`, and a `confidence ∈ [0,1]`.
Confidence below `extraction_confidence_floor` (default 0.4) is dropped
on the way in. `NoopExtractor` is wired in when `ANTHROPIC_API_KEY` is
unset, so the service still boots cleanly without keys.

`/turns` now extracts synchronously after the turn insert, on the same
connection but in a *separate* transaction. Extraction or persistence
failures are logged and swallowed — the turn always lands. New
`extraction_confidence_floor` and `extraction_max_tokens` settings.

**Tests:** 8 unit tests with `respx`-mocked Anthropic transport cover
the happy path, confidence filtering, malformed-item skipping,
empty-message short-circuit, API errors, and missing-tool-block
responses. One live integration test (`test_extraction_live.py`)
ingests a high-signal turn and asserts ≥1 structured row appears in
`/users/{id}/memories` — gated on `ANTHROPIC_API_KEY` being set.

**Recall-quality score:** still **0.200** — `/recall` is still a stub.
That's the M9 commit. Extraction quality can be inspected directly via
`/users/{id}/memories` once a key is configured.

**Why ship this without a recall delta:** keeping extraction and recall
surfacing in separate commits gives the changelog two cleanly-attributed
deltas later (M8 = "memories now appear in inspection endpoint"; M9 =
"recall score moved from 0.200 to X"), instead of one fat commit where
it's unclear which change caused which improvement.

**Next:** wire `/recall` to the memories table — fetch the user's
active memories, render a "Known facts" block, return as context. First
commit where the recall-quality number actually moves.

## v0.7 — test: recall-quality fixture + harness (5 scenarios) (2026-05-07)

**What changed:** Five hand-written scenarios in `fixtures/recall_quality/`
covering personal facts (incl. implicit), fact evolution, preferences /
corrections, multi-hop, and noise resistance.
`tests/recall_quality_runner.py` loads + runs scenarios against the live
service via `httpx`, computing per-probe scores via case-insensitive
substring match (with optional `forbidden_any` blocklist).
`tests/test_recall_quality.py` is the pytest entry — prints a per-scenario
report and asserts `aggregate >= MIN_SCORE`. Each scenario uses a unique
namespaced `user_id` and cleans up after itself in a `finally`.

**Baseline measured:** aggregate **0.200** (3/15 probes pass). Only the
noise-resistance scenario scores 1.0 — it passes by construction because
the stub `/recall` always returns an empty context, which is exactly what
that scenario asks for. Scenarios 1–4 all score 0.0 against the stub.
This is the zero we will move.

**Why this comes before extraction:** every subsequent commit that touches
extraction, retrieval, or context assembly will ship with a delta number
in this entry. Building the harness first turns the changelog into a
quantitative engineering narrative instead of vibes.

**Next:** Extraction pipeline — derive structured memories from raw turns
via Claude tool-use. First feature whose effect we can measure here.

## v0.6 — test: contract smoke against live compose stack (8 tests) (2026-05-07)

**What changed:** `tests/conftest.py` with a session-scoped `base_url`
fixture that probes `/health` and skips the suite if the service isn't
reachable (no silent fail). Async `httpx.AsyncClient` fixture per test.
`tests/test_contract_smoke.py` with 8 cases: `/health`, full lifecycle
(turn → recall → search → memories → both DELETEs), recall on cold
session, malformed-turn → 422, unicode payload accepted, concurrent
sessions don't bleed, idempotent DELETEs for unknown ids. Bodies are
validated against the same Pydantic schemas the service exposes, so
contract drift between handler and schema fails the suite.

**Result:** 8/8 pass in 0.35s against the live compose stack.

**Next:** Extraction pipeline — derive structured memories from raw turns
via Claude tool-use, plus produce embeddings to feed the recall pipeline.

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
