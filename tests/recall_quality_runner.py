"""Loader + async runner for the recall-quality fixtures.

Pure async, no pytest import — so the same code can be invoked from a
script, a notebook, or the pytest entry in ``test_recall_quality.py``.
Fixtures are JSON files described in ``fixtures/recall_quality/README.md``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean

import httpx

from memory_service.schemas import RecallOut


# --- match logic ----------------------------------------------------------


def probe_matches(
    ctx: str,
    expected_any: list[str],
    forbidden_any: list[str],
    match: str = "any",
) -> bool:
    """Score one probe.

    - If any forbidden substring appears in the context → fail.
    - If ``expected_any`` is empty → pass iff the context is effectively empty
      (noise-resistance probe).
    - Otherwise check ``any`` (default) or ``all`` of ``expected_any`` against
      the lower-cased context.
    """
    ctx_l = ctx.lower()
    if any(f.lower() in ctx_l for f in forbidden_any):
        return False
    if not expected_any:
        return ctx.strip() == ""
    if match == "all":
        return all(s.lower() in ctx_l for s in expected_any)
    return any(s.lower() in ctx_l for s in expected_any)


# --- result types ---------------------------------------------------------


@dataclass
class ProbeResult:
    query: str
    expected_any: list[str]
    forbidden_any: list[str]
    matched: bool
    context_excerpt: str  # first ~200 chars of the recall context, for debugging


@dataclass
class ScenarioResult:
    scenario_id: str
    description: str
    probes: list[ProbeResult] = field(default_factory=list)
    error: str | None = None  # set if the scenario aborted before probing

    @property
    def score(self) -> float:
        if not self.probes:
            return 0.0
        return mean(1.0 if p.matched else 0.0 for p in self.probes)


@dataclass
class RunReport:
    scenarios: list[ScenarioResult]

    @property
    def aggregate(self) -> float:
        if not self.scenarios:
            return 0.0
        return mean(s.score for s in self.scenarios)

    def render(self) -> str:
        lines: list[str] = []
        lines.append("=" * 78)
        lines.append("Recall-quality report")
        lines.append("=" * 78)
        for s in self.scenarios:
            lines.append("")
            lines.append(f"## {s.scenario_id}  —  score {s.score:.2f}")
            lines.append(f"   {s.description}")
            if s.error:
                lines.append(f"   ERROR: {s.error}")
                continue
            for p in s.probes:
                tick = "PASS" if p.matched else "FAIL"
                exp = ", ".join(p.expected_any) if p.expected_any else "<empty>"
                forb = (
                    f"  forbidden=[{', '.join(p.forbidden_any)}]"
                    if p.forbidden_any
                    else ""
                )
                lines.append(f"   [{tick}]  {p.query}")
                lines.append(f"          expected_any=[{exp}]{forb}")
                if not p.matched:
                    excerpt = p.context_excerpt.replace("\n", " ⏎ ")
                    lines.append(f"          ctx: {excerpt!r}")
        lines.append("")
        lines.append("-" * 78)
        lines.append(
            f"Aggregate: {self.aggregate:.3f}  "
            f"({len(self.scenarios)} scenarios, "
            f"{sum(len(s.probes) for s in self.scenarios)} probes)"
        )
        lines.append("=" * 78)
        return "\n".join(lines)


# --- fixture loading ------------------------------------------------------


def load_scenarios(fixtures_dir: Path) -> list[dict]:
    """Read every ``scenario_*.json`` in ``fixtures_dir``, sorted by filename."""
    paths = sorted(fixtures_dir.glob("scenario_*.json"))
    out: list[dict] = []
    for p in paths:
        data = json.loads(p.read_text())
        if data.get("id") != p.stem:
            raise ValueError(
                f"{p}: 'id' ({data.get('id')!r}) does not match filename stem ({p.stem!r})"
            )
        out.append(data)
    return out


# --- runner ---------------------------------------------------------------


def _ns(scenario_id: str, user_id: str, raw: str) -> str:
    """Namespace a scenario-local session id to a globally-unique one."""
    return f"{scenario_id}:{user_id}:{raw}"


async def run_scenario(
    client: httpx.AsyncClient, scenario: dict
) -> ScenarioResult:
    scenario_id = scenario["id"]
    description = scenario.get("description", "")
    user_id = f"recall-eval:{scenario_id}:{uuid.uuid4()}"

    result = ScenarioResult(scenario_id=scenario_id, description=description)
    try:
        for turn in scenario["turns"]:
            payload = {
                "session_id": _ns(scenario_id, user_id, turn["session_id"]),
                "user_id": user_id,
                "messages": turn["messages"],
                "timestamp": turn["timestamp"],
                "metadata": turn.get("metadata", {}),
            }
            r = await client.post("/turns", json=payload)
            if r.status_code != 201:
                result.error = (
                    f"POST /turns returned {r.status_code}: {r.text[:200]}"
                )
                return result

        for probe in scenario["probes"]:
            body = {
                "query": probe["query"],
                "session_id": _ns(scenario_id, user_id, probe["session_id"]),
                "user_id": user_id,
                "max_tokens": probe.get("max_tokens", 512),
            }
            r = await client.post("/recall", json=body)
            if r.status_code != 200:
                result.error = (
                    f"POST /recall returned {r.status_code}: {r.text[:200]}"
                )
                return result
            recall = RecallOut.model_validate(r.json())
            ctx = recall.context

            expected_any = list(probe.get("expected_any", []))
            forbidden_any = list(probe.get("forbidden_any", []))
            match_mode = probe.get("match", "any")
            matched = probe_matches(ctx, expected_any, forbidden_any, match_mode)

            result.probes.append(
                ProbeResult(
                    query=probe["query"],
                    expected_any=expected_any,
                    forbidden_any=forbidden_any,
                    matched=matched,
                    context_excerpt=ctx[:200],
                )
            )
        return result
    finally:
        # Cleanup runs even if the scenario errored mid-way.
        try:
            await client.delete(f"/users/{user_id}")
        except httpx.HTTPError:
            pass  # don't mask scenario errors with cleanup errors


async def run_all(
    client: httpx.AsyncClient, fixtures_dir: Path
) -> RunReport:
    scenarios = load_scenarios(fixtures_dir)
    return await run_iter(client, scenarios)


async def run_iter(
    client: httpx.AsyncClient, scenarios: Iterable[dict]
) -> RunReport:
    results = [await run_scenario(client, s) for s in scenarios]
    return RunReport(scenarios=results)
