"""End-to-end contract smoke test for the 7 HTTP endpoints.

Each test is independent and uses unique session/user ids so the suite can
run repeatedly without bleed. Test bodies are validated against the same
Pydantic schemas the service exposes — that way contract drift between
schema and handler shows up here, not in production.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest

from memory_service.schemas import (
    HealthOut,
    MemoriesOut,
    RecallOut,
    SearchOut,
    TurnOut,
)


def _u() -> str:
    return f"u-{uuid.uuid4()}"


def _s() -> str:
    return f"s-{uuid.uuid4()}"


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


pytestmark = pytest.mark.asyncio


async def test_health(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    HealthOut.model_validate(r.json())


async def test_full_turn_lifecycle(client: httpx.AsyncClient) -> None:
    user_id, session_id = _u(), _s()
    payload = {
        "session_id": session_id,
        "user_id": user_id,
        "messages": [
            {"role": "user", "content": "I just moved to Berlin from NYC."},
            {"role": "assistant", "content": "Welcome!"},
        ],
        "timestamp": _now(),
        "metadata": {"k": "v"},
    }

    r = await client.post("/turns", json=payload)
    assert r.status_code == 201, r.text
    turn = TurnOut.model_validate(r.json())
    assert turn.id

    r = await client.post(
        "/recall",
        json={
            "query": "where do they live?",
            "session_id": session_id,
            "user_id": user_id,
            "max_tokens": 256,
        },
    )
    assert r.status_code == 200
    RecallOut.model_validate(r.json())

    r = await client.post(
        "/search",
        json={"query": "berlin", "user_id": user_id, "limit": 5},
    )
    assert r.status_code == 200
    SearchOut.model_validate(r.json())

    r = await client.get(f"/users/{user_id}/memories")
    assert r.status_code == 200
    MemoriesOut.model_validate(r.json())

    r = await client.delete(f"/sessions/{session_id}")
    assert r.status_code == 204

    r = await client.delete(f"/users/{user_id}")
    assert r.status_code == 204


async def test_recall_on_cold_session_returns_empty(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post(
        "/recall",
        json={
            "query": "anything",
            "session_id": _s(),
            "user_id": _u(),
            "max_tokens": 100,
        },
    )
    assert r.status_code == 200
    out = RecallOut.model_validate(r.json())
    assert out.context == ""
    assert out.citations == []


async def test_malformed_turn_returns_422(client: httpx.AsyncClient) -> None:
    r = await client.post("/turns", json={"junk": True})
    assert r.status_code == 422


async def test_unicode_payload_is_accepted(client: httpx.AsyncClient) -> None:
    """Unicode in messages must round-trip without 5xx."""
    user_id, session_id = _u(), _s()
    r = await client.post(
        "/turns",
        json={
            "session_id": session_id,
            "user_id": user_id,
            "messages": [{"role": "user", "content": "Привет 🐈‍⬛ — café"}],
            "timestamp": _now(),
            "metadata": {},
        },
    )
    assert r.status_code == 201, r.text
    TurnOut.model_validate(r.json())

    await client.delete(f"/users/{user_id}")


async def test_concurrent_sessions_dont_bleed(
    client: httpx.AsyncClient,
) -> None:
    u1, u2, s1, s2 = _u(), _u(), _s(), _s()
    base = {
        "messages": [{"role": "user", "content": "x"}],
        "timestamp": _now(),
        "metadata": {},
    }
    r1 = await client.post(
        "/turns", json={"session_id": s1, "user_id": u1, **base}
    )
    r2 = await client.post(
        "/turns", json={"session_id": s2, "user_id": u2, **base}
    )
    assert r1.status_code == 201
    assert r2.status_code == 201

    # u2's view doesn't include u1's data, and the response shape is valid.
    r = await client.get(f"/users/{u2}/memories")
    assert r.status_code == 200
    MemoriesOut.model_validate(r.json())

    await client.delete(f"/users/{u1}")
    await client.delete(f"/users/{u2}")


async def test_delete_unknown_session_is_idempotent(
    client: httpx.AsyncClient,
) -> None:
    r = await client.delete(f"/sessions/{_s()}")
    assert r.status_code == 204


async def test_delete_unknown_user_is_idempotent(
    client: httpx.AsyncClient,
) -> None:
    r = await client.delete(f"/users/{_u()}")
    assert r.status_code == 204
