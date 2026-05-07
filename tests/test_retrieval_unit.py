"""Unit tests for ``rrf_rank``.

No DB, no HTTP — pure in-memory candidates. Verifies the fusion math,
top_k truncation, floor filtering, and tiebreaker order.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from memory_service.retrieval import Candidate, rrf_rank


def _c(
    label: str,
    *,
    type_: str = "fact",
    vec: float | None = None,
    bm25: float | None = None,
    confidence: float = 0.9,
    updated_at: datetime | None = None,
) -> Candidate:
    return Candidate(
        id=uuid.uuid5(uuid.NAMESPACE_OID, label),
        type=type_,  # type: ignore[arg-type]
        key=label,
        value=label,
        confidence=confidence,
        source_session="s-test",
        source_turn=uuid.uuid4(),
        updated_at=updated_at or datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
        vec_distance=vec,
        bm25_score=bm25,
    )


def _ids(ranked: list[tuple[Candidate, float]]) -> list[str]:
    return [c.key for c, _ in ranked]


# --- baseline / degenerate cases -----------------------------------------


def test_empty_returns_empty() -> None:
    assert rrf_rank([]) == []


def test_no_signals_drops_everything() -> None:
    rows = [_c("a"), _c("b")]  # both vec_distance and bm25_score None
    assert rrf_rank(rows) == []


def test_top_k_truncates() -> None:
    rows = [_c(f"r{i}", vec=float(i)) for i in range(5)]
    ranked = rrf_rank(rows, top_k=2)
    assert len(ranked) == 2
    assert _ids(ranked) == ["r0", "r1"]  # closest distance wins


# --- single-signal modes -------------------------------------------------


def test_vector_only_orders_by_distance_asc() -> None:
    rows = [
        _c("c", vec=0.4),
        _c("a", vec=0.1),
        _c("b", vec=0.2),
    ]
    assert _ids(rrf_rank(rows)) == ["a", "b", "c"]


def test_bm25_only_orders_by_score_desc() -> None:
    rows = [
        _c("a", bm25=0.5),
        _c("b", bm25=0.9),
        _c("c", bm25=0.1),
    ]
    assert _ids(rrf_rank(rows)) == ["b", "a", "c"]


# --- fusion behaviour ----------------------------------------------------


def test_intersection_outranks_single_signal() -> None:
    """A candidate appearing in both lists gets two contributions."""
    rows = [
        _c("vec_only", vec=0.05),
        _c("both", vec=0.5, bm25=0.5),
        _c("bm25_only", bm25=0.9),
    ]
    ranked = rrf_rank(rows)
    # 'both' has rank 2 in vec_ranked and rank 2 in bm25_ranked → 2/(60+2) ≈ 0.0323
    # 'vec_only' has rank 1 in vec_ranked → 1/(60+1) ≈ 0.0164
    # 'bm25_only' has rank 1 in bm25_ranked → 1/(60+1) ≈ 0.0164
    assert ranked[0][0].key == "both"


def test_floor_drops_low_scores() -> None:
    rows = [
        _c("a", vec=0.1),
        _c("b", bm25=0.3),
    ]
    # default floor = 0.0 keeps both (each has score > 0).
    assert len(rrf_rank(rows)) == 2
    # high floor drops both.
    assert rrf_rank(rows, floor=1.0) == []


# --- tiebreakers ---------------------------------------------------------


def test_tiebreaker_type_priority_when_scores_equal() -> None:
    """Two candidates at identical RRF rank in both lists → type priority decides."""
    rows = [
        _c("ev", type_="event", vec=0.1, bm25=0.5),
        _c("ft", type_="fact", vec=0.1, bm25=0.5),
        _c("op", type_="opinion", vec=0.1, bm25=0.5),
        _c("pf", type_="preference", vec=0.1, bm25=0.5),
    ]
    # All four have identical RRF (rank-1 in both). Ordering is by type priority.
    assert _ids(rrf_rank(rows)) == ["ft", "pf", "op", "ev"]


def test_tiebreaker_recency_then_confidence() -> None:
    """Same type, same scores → newer beats older; then higher confidence."""
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 4, 1, tzinfo=UTC)
    rows = [
        _c("old", vec=0.1, bm25=0.5, updated_at=older, confidence=0.9),
        _c("new_low_conf", vec=0.1, bm25=0.5, updated_at=newer, confidence=0.6),
        _c("new_high_conf", vec=0.1, bm25=0.5, updated_at=newer, confidence=0.95),
    ]
    # newer first, then by confidence within same updated_at
    assert _ids(rrf_rank(rows)) == ["new_high_conf", "new_low_conf", "old"]


def test_deterministic_when_everything_ties() -> None:
    """Identical-by-tiebreaker candidates must come back in stable id order."""
    base = datetime(2026, 4, 1, tzinfo=UTC)
    rows = [_c(f"t{i}", vec=0.1, bm25=0.5, updated_at=base, confidence=0.9) for i in range(3)]
    out1 = _ids(rrf_rank(rows))
    out2 = _ids(rrf_rank(list(reversed(rows))))
    assert out1 == out2  # deterministic across input order
