"""Live extraction integration test.

Requires an Anthropic key in BOTH the running compose service (so it
extracts) and in the local pytest process (so we know to enable this
test). Skipped otherwise.

Behaviour: ingest one high-signal turn, then read /users/{id}/memories
and assert at least one structured row came back. Lenient on which
specific memories the LLM produced — just that *something* structured
shows up.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest

KEY_PRESENT = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


@pytest.mark.skipif(
    not KEY_PRESENT,
    reason="ANTHROPIC_API_KEY not set in the test environment",
)
async def test_extraction_persists_structured_memories(client: httpx.AsyncClient) -> None:
    user_id = f"extract-live-{uuid.uuid4()}"
    session_id = f"s-{uuid.uuid4()}"
    payload = {
        "session_id": session_id,
        "user_id": user_id,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Quick context: I'm a senior PM at Notion, based in Berlin. "
                    "We have a corgi mix named Biscuit."
                ),
            },
            {"role": "assistant", "content": "Got it, thanks for the intro."},
        ],
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "metadata": {},
    }

    try:
        r = await client.post("/turns", json=payload)
        assert r.status_code == 201, r.text

        r = await client.get(f"/users/{user_id}/memories")
        assert r.status_code == 200
        memories = r.json()["memories"]

        # The minimum bar — extraction returned *something* structured.
        # Specific keys vary by model run; we don't pin them.
        assert len(memories) >= 1, f"expected ≥1 extracted memory, got {memories!r}"

        # Sanity: every memory has the contract shape.
        for m in memories:
            for f in (
                "id", "type", "key", "value", "confidence",
                "source_session", "source_turn",
                "created_at", "updated_at", "active",
            ):
                assert f in m, f"memory missing field {f!r}: {m!r}"
            assert m["type"] in {"fact", "preference", "opinion", "event"}
            assert 0.0 <= m["confidence"] <= 1.0
            assert m["source_session"] == session_id
            assert m["active"] is True

        # Lenient relevance check: at least one memory's value or key should
        # mention something from the prompt — Notion / Berlin / Biscuit /
        # PM / corgi. If none does, extraction is producing noise.
        haystack = " ".join(
            (m["key"] + " " + m["value"]).lower() for m in memories
        )
        assert any(
            t in haystack for t in ("notion", "berlin", "biscuit", "pm", "corgi", "dog")
        ), f"none of the expected terms surfaced in extracted memories: {memories!r}"
    finally:
        await client.delete(f"/users/{user_id}")
