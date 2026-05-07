"""Live integration tests for ``/search``.

Skipped when ``ANTHROPIC_API_KEY`` isn't set — extraction needs to run
for the test to have memories to search over. Asserts shape +
filtering behaviour, lenient on which specific memories the LLM
extracted.
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


@pytest.mark.skipif(
    not KEY_PRESENT, reason="ANTHROPIC_API_KEY not set in the test environment"
)
async def test_search_returns_structured_hits_for_user(
    client: httpx.AsyncClient,
) -> None:
    user_id = f"search-{uuid.uuid4()}"
    session_id = f"s-{uuid.uuid4()}"
    try:
        await _ingest(
            client, user_id, session_id,
            "Quick context — I'm a senior PM at Notion, based in Berlin. "
            "We have a corgi mix named Biscuit.",
        )

        r = await client.post(
            "/search",
            json={"query": "where does the user work", "user_id": user_id, "limit": 5},
        )
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) >= 1, f"expected ≥1 hit, got {results!r}"

        for hit in results:
            for f in ("content", "score", "session_id", "timestamp", "metadata"):
                assert f in hit, f"hit missing field {f!r}: {hit!r}"
            assert isinstance(hit["score"], (int, float))
            assert hit["session_id"] == session_id
            assert isinstance(hit["metadata"], dict)
            assert "type" in hit["metadata"]
            assert hit["metadata"]["type"] in {"fact", "preference", "opinion", "event"}

        # At least one hit should mention something from the prompt.
        haystack = " ".join(h["content"].lower() for h in results)
        assert any(
            t in haystack for t in ("notion", "berlin", "biscuit", "pm", "corgi", "dog")
        ), f"none of the expected terms surfaced: {results!r}"
    finally:
        await client.delete(f"/users/{user_id}")


@pytest.mark.skipif(
    not KEY_PRESENT, reason="ANTHROPIC_API_KEY not set in the test environment"
)
async def test_search_with_no_filters_returns_empty(
    client: httpx.AsyncClient,
) -> None:
    """Both user_id and session_id null → must not leak data across users."""
    other_user = f"search-other-{uuid.uuid4()}"
    try:
        await _ingest(
            client, other_user, "s",
            "I'm Dana, lead engineer at Acme.",
        )

        r = await client.post(
            "/search", json={"query": "engineer", "limit": 5}
        )
        assert r.status_code == 200
        assert r.json() == {"results": []}
    finally:
        await client.delete(f"/users/{other_user}")


@pytest.mark.skipif(
    not KEY_PRESENT, reason="ANTHROPIC_API_KEY not set in the test environment"
)
async def test_search_limit_truncates(client: httpx.AsyncClient) -> None:
    user_id = f"search-limit-{uuid.uuid4()}"
    try:
        await _ingest(
            client, user_id, "s1",
            "I work at Notion as a PM in Berlin. We have a corgi named Biscuit. "
            "I prefer Python for scripts. My favourite IDE is Cursor.",
        )

        r = await client.post(
            "/search",
            json={"query": "user information", "user_id": user_id, "limit": 1},
        )
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) <= 1
    finally:
        await client.delete(f"/users/{user_id}")


@pytest.mark.skipif(
    not KEY_PRESENT, reason="ANTHROPIC_API_KEY not set in the test environment"
)
async def test_search_filter_by_session_id(client: httpx.AsyncClient) -> None:
    user_id = f"search-sess-{uuid.uuid4()}"
    s1 = f"s1-{uuid.uuid4()}"
    s2 = f"s2-{uuid.uuid4()}"
    try:
        await _ingest(client, user_id, s1, "I work at Notion as a PM.")
        await _ingest(client, user_id, s2, "I have a corgi named Biscuit.")

        r = await client.post(
            "/search",
            json={"query": "info", "user_id": user_id, "session_id": s1, "limit": 10},
        )
        assert r.status_code == 200
        results = r.json()["results"]
        # Every result should be from session s1 only.
        for hit in results:
            assert hit["session_id"] == s1, f"leaked session: {hit!r}"
    finally:
        await client.delete(f"/users/{user_id}")
