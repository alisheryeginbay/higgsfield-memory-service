"""Shared fixtures for the contract smoke test.

Tests run against a live `docker compose up` stack — the service must be
reachable at `MEMORY_SERVICE_URL` (default `http://localhost:8080`). If
nothing is running there the suite is skipped with a clear message.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

BASE_URL = os.environ.get("MEMORY_SERVICE_URL", "http://localhost:8080")


@pytest.fixture(scope="session")
def base_url() -> str:
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=2.0)
    except httpx.HTTPError as e:
        pytest.skip(f"memory service not reachable at {BASE_URL}: {e}")
    if r.status_code != 200:
        pytest.skip(f"memory service unhealthy at {BASE_URL} (HTTP {r.status_code})")
    return BASE_URL


@pytest_asyncio.fixture
async def client(base_url: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as c:
        yield c
