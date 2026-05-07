# Changelog

Iteration history for the memory service. Newest first. Each entry tracks
a single commit — what changed, why, what was observed, and what comes
next.

## v0.14 — feat: hybrid query-aware retrieval + canonical-key prompt (2026-05-07)

**What changed:**

- New `src/memory_service/retrieval.py` — pure-Python `Candidate`
  dataclass + `rrf_rank()` reciprocal rank fusion (k=60, type-priority
  tiebreaker) with full deterministic ordering.
- `api/recall.py` rewritten — embeds the query (Voyage; falls back to
  BM25-only on hiccup), runs ONE SQL pulling cosine distance + ts_rank
  for every active row, fuses via RRF in Python, takes top-K
  (configurable, default 10), renders.
- `recall.render_context` gains an optional `score_lookup` so
  citations carry RRF scores instead of confidence (the contract's
  `score` field is documented as "ranking score", so the semantic
  fits). Default behaviour unchanged for callers that don't pass it.
- New `recall_top_k` setting (env-tunable).
- Bundled M13 prompt cleanup in `extraction.py`: canonical-keys
  glossary with explicit "do NOT invent narrower variants" wording,
  conciseness rule (`value` is one assertion, never compound),
  rewritten example 3 demonstrating canonical `language_preference`
  with reasoning that explains *why*.
- Events stay filtered out of default recall (M12's policy preserved).
  Re-introducing them needs query-intent classification to avoid
  leaking superseded entities into "currently …" queries — deferred
  to a later commit.

**Recall-quality score: 0.867 → 0.867** (no aggregate move; still 13/15).

| Scenario | Probe distribution change vs v0.13 |
|---|---|
| 01 personal_facts | 4/4 unchanged |
| 02 fact_evolution | 2/3 unchanged (probes 1+2 pass, probe 3 still fails: needs old "Stripe" surfaced for history queries — would re-leak it to "currently" queries without query-intent gating) |
| 03 preferences_corrections | 2/3 — DIFFERENT probes pass: canonical keys + conciseness rule swapped probe-1 (now passes: only Python in active language_preference) and probe-2 (now fails: TS-for-big half dropped). Probe 3 still passes. |
| 04 multi_hop | 2/2 unchanged |
| 05 noise_resistance | 3/3 unchanged |

**Why ship this without an aggregate move?** The fixture has hit a
structural ceiling — every remaining failure is a substring-matching
artifact where the same memory store can't simultaneously satisfy
contradictory probe pairs (current vs history; Python-only vs
TS-also). Solving them needs query-intent gating or LLM rerank — both
sit on top of this commit. Ranking now happens in Python over a
hybrid (vector + BM25) candidate pool, which is the foundation those
features need. Citations carrying meaningful relevance scores is
also a contract win.

**Variance:** measured 5 runs, all returned `0.867` exactly — the
canonical-key prompt eliminated the LLM jitter that was previously
flipping 0.800 ↔ 0.867 across runs. **`MIN_SCORE` bumped:**
0.815 → 0.850 (measured 0.867 deterministic, smaller slack).

**Tests:** 10 new unit tests in `test_retrieval_unit.py` cover RRF
fusion math, top-K truncation, single-signal modes, intersection
behavior, floor filtering, and the full tiebreaker hierarchy
(type-priority → recency → confidence → id). Existing renderer unit
tests untouched (the `score_lookup` parameter is opt-in). Full
suite: 52 passed (up from 42 in v0.13 — 10 retrieval unit tests).

**Latency cost:** each `/recall` adds one Voyage `embed_query` call
(~50 input tokens, sub-cent at voyage-4-lite pricing,
typical < 200ms).

**Next:** the cheapest remaining levers are
(a) **query-intent gating** (a tiny LLM classifier on the query —
"current" vs "history" vs other — to gate event inclusion and
unlock scenario_02 probe 3) and
(b) **memory-aware extraction** (extractor sees existing user
keys+values to deliberately reuse them, which would help with the
canonical-key follow-through across more diverse user vocabulary).

## v0.13 — feat: embeddings infrastructure (Voyage + pgvector + tsvector) (2026-05-07)

**What changed:** Plumbing-only commit that adds the storage and
generation paths for hybrid retrieval. No `/recall` behavior change yet
— that's the next commit (v0.14), which is when the score moves.

- Migration `0002`: `embedding vector(1024)` column on `memories`,
  generated `tsv tsvector` column with key-component splitting
  (`pet:dog:name` → searchable tokens), HNSW index on `embedding` with
  `vector_cosine_ops`, GIN index on `tsv`.
- New `src/memory_service/embeddings.py` — `Embedder` protocol,
  `VoyageEmbedder` (asymmetric `input_type="document"` for stored
  memories, `"query"` for recall), `NoopEmbedder` for the no-key
  degraded path. Wraps SDK errors as `EmbeddingError`, validates
  dim + length on every response.
- `extraction.persist_memories` now batches one Voyage call per turn
  (regardless of memory count) and writes `embedding` alongside the
  existing columns. Voyage failures degrade to NULL embedding rather
  than blocking insert — same "extraction is enrichment" discipline
  as v0.8.
- Lifespan instantiates the embedder once at startup based on
  `VOYAGE_API_KEY`; `get_embedder` dependency wires it into `/turns`.

**Recall-quality score: 0.867 (unchanged).** Verified:
`/recall` SQL untouched, fixture aggregate stays exactly where v0.12
left it. The point of this commit is the foundation, not movement.

**Tests:** 12 new unit tests in `test_embeddings_unit.py` mock the
Voyage SDK at the client boundary (voyageai uses aiohttp, not httpx,
so respx doesn't help; `AsyncMock` on `client.embed` does the job).
Coverage: happy paths for documents/queries, empty-batch short-circuit,
SDK error wrapping, length mismatch, dimension mismatch.
Full suite: 42 passed (with `ANTHROPIC_API_KEY` set; existing live
extraction + supersession tests now also exercise the embedder
implicitly because every `/turns` call writes an embedding).

**Manual verification:** ingested a turn, inspected raw rows via
`psql` — every memory has `vector_dims(embedding) = 1024` and a
non-empty `tsv` column.

**Why ship this without a recall delta:** keeping the embeddings
plumbing isolated lets the next commit attribute its score move
cleanly to the retrieval-pipeline switch (and the bundled M13 prompt
cleanup) rather than mixing it with infrastructure work.

**Next (v0.14):** flip `/recall` to hybrid retrieval — embed query,
joint vector + BM25 search, RRF combine, top-K render. Re-introduce
events into the retrieval pool. Bundle the deferred M13 prompt
cleanup (canonical-keys glossary + conciseness rule).

## v0.12 — feat: exclude events from default /recall context (2026-05-07)

**What changed:** One-line SQL filter in `api/recall.py` —
`/recall` now only surfaces `fact` / `preference` / `opinion` rows.
Events still extract, persist, and appear in
`/users/{user_id}/memories`; they just don't pollute the default
recall context. The renderer in `recall.py` is unchanged (still
handles all four types if given them) — preserving the option for an
"explicit history mode" later.

**Recall-quality score: 0.800 → 0.867** (12/15 → 13/15).

| Scenario | v0.11 | v0.12 | Notes |
|---|---|---|---|
| 01 personal_facts | 4/4 | 4/4 | unchanged |
| 02 fact_evolution | 2/3 | **2/3** | the `forbidden_any: ["stripe"]` probes now pass (career_change event no longer surfaces). The remaining fail is the `["stripe","notion"] match=all` "career history" probe — both employers needed in context, but past employer is superseded → only Notion present. Honest trade-off; the right fix is query-aware retrieval (M13+). |
| 03 preferences_corrections | 1/3 | **2/3** | unexpected win: the LLM was extracting "switched from VS Code" as an event, surfacing "VS Code" in recall, tripping the IDE probe's forbidden_any. Filtering events removed it. |
| 04 multi_hop | 2/2 | 2/2 | extraction produced both `pet:dog:breed: corgi` and `city: Lisbon` as facts, so multi-hop still passes |
| 05 noise_resistance | 3/3 | 3/3 | unchanged |

**The trade-off this commit accepts:** "Tell me about the user's
career history" can't find past employers anymore — they're in the
supersession chain (`active=false`), but recall doesn't surface them.
Substring-based forbidden_any can't tell "currently works at X" from
"previously worked at X", so a "Previously..." section would
re-trip the cleared probes. The structural fix is query-aware
retrieval (different queries → different memory subsets) and lives in
its own commit.

**MIN_SCORE bumped:** 0.750 → 0.815 (measured 0.867 minus 0.05 slack).

**Next:** scenario_03's third probe is the easiest remaining win —
the LLM uses inconsistent keys across turns (`language_preference` vs
`script_language_preference`), so per-key supersession can't link
them. Tighten the extractor system prompt to prefer canonical keys
already present in the user's memory. After that, query-aware recall
(M14ish) — embeddings + reranking — to start closing the trade-off
this commit accepted.

## v0.11 — feat: per-key supersession for facts / preferences / opinions (2026-05-07)

**What changed:** `persist_memories` now applies per-key supersession
inside the `/turns` transaction. For `fact` / `preference` / `opinion`
memories, an incoming row with the same `(user_id, key)` as an existing
active one marks all matching active rows `active=false`, then inserts
the new row with `supersedes` pointing back at the most recent old id.
Idempotent re-statement (same key + same value) is a no-op — the
existing row stays, no churn. `event` rows skip supersession entirely:
events are inherently temporal, multiple `career_change` rows over
time are valid.

**Recall-quality score: 0.733 → 0.800** (5/15 → 7/15 probes pass).

| Scenario | v0.10 | v0.11 | Notes |
|---|---|---|---|
| 01 personal_facts | 4/4 | 4/4 | unchanged |
| 02 fact_evolution | 1/3 | 2/3 | active employer flipped to Notion; the residual fail is the `forbidden_any: ["stripe"]` probe hitting the still-active `career_change` event whose value mentions Stripe |
| 03 preferences_corrections | 1/3 | 1/3 | unchanged — the LLM picked different keys (`language_preference` vs `script_language_preference`) across turns, so per-key supersession can't link them |
| 04 multi_hop | 2/2 | 2/2 | unchanged |
| 05 noise_resistance | 3/3 | 3/3 | unchanged |

**What's still leaving points on the table — and the next two commits:**

- Scenario_02's third probe — fixed by **excluding `event` from default
  recall rendering** (events are temporal context, not "facts about the
  user"). Render-layer change, not write-layer.
- Scenario_03 — fixed by either **canonical-key prompting** (force the
  LLM to reuse `language_preference` instead of inventing
  `script_language_preference`) or **memory-aware extraction** (the
  LLM sees current memories before producing new ones). Prompting first;
  memory-aware extraction is a bigger lever for later.

Each lands as its own commit so the per-scenario delta stays
attributable.

**Tests:** 2 new live integration tests in `tests/test_supersession_live.py`
cover (a) supersession marks old `active=false` and links via
`supersedes`, and (b) idempotent re-statement leaves the existing row
untouched. Full suite: 30/30 with `ANTHROPIC_API_KEY` set.

**MIN_SCORE bumped:** 0.683 → 0.750 (measured 0.800 minus 0.05 slack).

**Next:** exclude events from default `/recall` rendering — the cheapest
remaining lever, should clear scenario_02 fully.

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
