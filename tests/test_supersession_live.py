"""Live supersession tests against the running compose stack.

Skipped when ``ANTHROPIC_API_KEY`` isn't set in the test process — same
gating pattern as test_extraction_live.py. Asserts the v0.11 contract:

- A `fact`-type memory with the same `(user_id, key)` as an existing active
  one supersedes it: the old row goes `active=false`, the new row points
  back via `supersedes`.
- An idempotent re-statement (same key + same value) does NOT create a
  second row and does NOT mark the existing one inactive.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest

KEY_PRESENT = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


async def _ingest(
    client: httpx.AsyncClient, user_id: str, session_id: str, content: str
) -> None:
    r = await client.post(
        "/turns",
        json={
            "session_id": session_id,
            "user_id": user_id,
            "messages": [{"role": "user", "content": content}],
            "timestamp": _now_iso(),
            "metadata": {},
        },
    )
    assert r.status_code == 201, r.text


async def _memories(client: httpx.AsyncClient, user_id: str) -> list[dict]:
    r = await client.get(f"/users/{user_id}/memories")
    assert r.status_code == 200
    return r.json()["memories"]


@pytest.mark.skipif(
    not KEY_PRESENT, reason="ANTHROPIC_API_KEY not set in the test environment"
)
async def test_supersession_marks_old_inactive_and_links(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"super-{uuid.uuid4()}"
    try:
        await _ingest(
            client, user_id, "s-old",
            "Quick context — I work at Stripe as a backend engineer.",
        )
        await _ingest(
            client, user_id, "s-new",
            "Update — I just left Stripe and joined Notion as a PM.",
        )

        mems = await _memories(client, user_id)
        employer = [m for m in mems if m["key"] == "employer"]
        assert len(employer) >= 2, (
            f"expected at least 2 employer rows (old + new), got {employer!r}"
        )

        active = [m for m in employer if m["active"]]
        inactive = [m for m in employer if not m["active"]]

        assert len(active) == 1, (
            f"expected exactly 1 active employer, got {active!r}"
        )
        assert "notion" in active[0]["value"].lower(), (
            f"current employer should be Notion, got {active[0]['value']!r}"
        )

        assert len(inactive) >= 1
        assert any("stripe" in m["value"].lower() for m in inactive), (
            f"old Stripe memory should be archived, got {inactive!r}"
        )

        # The new active row should link back to one of the inactive ones.
        assert active[0]["supersedes"] is not None
        assert active[0]["supersedes"] in {m["id"] for m in inactive}
    finally:
        await client.delete(f"/users/{user_id}")


@pytest.mark.skipif(
    not KEY_PRESENT, reason="ANTHROPIC_API_KEY not set in the test environment"
)
async def test_supersession_is_idempotent_on_restate(
    client: httpx.AsyncClient,
) -> None:
    """Re-stating the same fact verbatim should not duplicate or invalidate."""
    user_id = f"super-idem-{uuid.uuid4()}"
    try:
        msg = "Just confirming — I'm a senior PM at Notion."
        await _ingest(client, user_id, "s1", msg)
        first = await _memories(client, user_id)
        first_employer_active = [
            m for m in first
            if m["key"] == "employer" and m["active"]
        ]
        assert len(first_employer_active) == 1

        # Re-state the same fact — should be idempotent.
        await _ingest(client, user_id, "s2", msg)
        second = await _memories(client, user_id)
        second_employer_active = [
            m for m in second
            if m["key"] == "employer" and m["active"]
        ]
        # Still exactly one active employer — and crucially, no inactive
        # rows for the same key (we didn't churn).
        assert len(second_employer_active) == 1
        assert second_employer_active[0]["id"] == first_employer_active[0]["id"], (
            "idempotent re-statement should not have replaced the row"
        )
        assert all(
            m["active"]
            for m in second
            if m["key"] == "employer"
        ), "no employer row should have been marked inactive"
    finally:
        await client.delete(f"/users/{user_id}")
