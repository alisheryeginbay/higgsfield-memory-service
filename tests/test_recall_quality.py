"""Pytest entry for the recall-quality fixture.

Wires the async runner in ``recall_quality_runner.py`` to the live service
via the existing ``client`` fixture from ``conftest.py``. Prints a
per-scenario report and asserts the aggregate score is ≥ ``MIN_SCORE``.

``MIN_SCORE`` is the ratchet: bump it whenever a commit improves recall
and you want to lock the gain in. Regressions then fail this test with a
clear message.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests.recall_quality_runner import run_all

# Repo root from this file's location, so the test works regardless of CWD.
REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "fixtures" / "recall_quality"

# v0.7 baseline. Ratchet up in subsequent commits.
MIN_SCORE = 0.0


pytestmark = pytest.mark.asyncio


async def test_recall_quality(client: httpx.AsyncClient) -> None:
    report = await run_all(client, FIXTURES_DIR)
    print("\n" + report.render())  # visible with `pytest -s`
    assert report.aggregate >= MIN_SCORE, (
        f"recall quality regressed: {report.aggregate:.3f} < {MIN_SCORE:.3f}"
    )
