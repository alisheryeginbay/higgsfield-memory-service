# Recall-quality fixtures

Hand-written conversations + probe queries that drive `tests/test_recall_quality.py`. The pytest entry ingests the conversations through `POST /turns`, runs the probes through `POST /recall`, and scores each probe against `expected_any` / `forbidden_any` substrings in the returned context.

This directory is the iteration loop. The aggregate score it produces is what shows up in the `CHANGELOG.md` after each commit that touches extraction, retrieval, or context assembly.

## File layout

One `scenario_NN_name.json` per scenario. The `id` field inside the file should equal the filename stem.

## Schema

```json
{
  "id": "scenario_01_personal_facts",
  "description": "One-line human description, printed in the report.",
  "turns": [
    {
      "session_id": "s1",
      "messages": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."}
      ],
      "timestamp": "2026-04-01T10:00:00Z"
    }
  ],
  "probes": [
    {
      "query": "Where does the user live?",
      "session_id": "s_probe",
      "expected_any": ["berlin"],
      "forbidden_any": [],
      "match": "any",
      "max_tokens": 512
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `id` | ✓ | Stable identifier; printed in the run report. |
| `description` | ✓ | One-liner, also printed. |
| `turns[]` | ✓ | Posted in order via `POST /turns`. |
| `turns[].session_id` | ✓ | Logical session within the scenario. The runner namespaces it to a globally-unique compose-side id, so two scenarios using `"s1"` don't collide. |
| `turns[].messages` | ✓ | Same shape as the contract `Message` model: `{role, content}` pairs (`role ∈ {user, assistant, tool, system}`). |
| `turns[].timestamp` | ✓ | ISO-8601. |
| `probes[]` | ✓ | Posted in order via `POST /recall` after all turns are ingested. |
| `probes[].query` | ✓ | The recall query. |
| `probes[].session_id` | ✓ | Use a *different* value from the ingestion sessions to test cross-session recall (the realistic agent case). |
| `probes[].expected_any` | ✓ | List of case-insensitive substrings. With `match="any"` (default), the probe scores 1 if at least one substring is found in the recall context. With `match="all"`, every substring must be found. **Empty list** means: probe is a noise-resistance check — it scores 1 if the recall context is empty (stripped). |
| `probes[].forbidden_any` |  | Optional. If any substring (case-insensitive) appears in the recall context, the probe scores 0 regardless of `expected_any`. |
| `probes[].match` |  | `"any"` (default) or `"all"`. |
| `probes[].max_tokens` |  | Defaults to 512. |

## Adding a scenario

1. Pick the lowest free `NN`.
2. Copy an existing file as a template; edit `id`, `description`, `turns`, `probes`.
3. Run `uv run pytest tests/test_recall_quality.py -v -s` and check the per-scenario breakdown in the report — especially that your `expected_any` strings are a fair bar (not too easy, not impossible).
4. Land the scenario in its own commit with a `CHANGELOG.md` entry that notes the new aggregate.

## Categories the current set covers

| Scenario | Category from the eval rubric |
|---|---|
| 01_personal_facts | Personal facts (incl. implicit) — employer, location, pet name. |
| 02_fact_evolution | Fact evolution — current employer must beat stale one. |
| 03_preferences_corrections | Corrections inside a single conversation. |
| 04_multi_hop | Multi-hop — joining two facts to answer one question. |
| 05_noise_resistance | Noise resistance — probe a topic never discussed; recall must stay empty. |
