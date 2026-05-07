"""Hybrid retrieval scoring — pure logic, no DB / HTTP.

Reciprocal rank fusion (RRF) over a vector list and a BM25 list. The
candidates come pre-scored from one SQL query; this module only ranks.
Kept separate from `api/recall.py` so the algorithm is unit-testable
without a live stack.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

MemoryType = Literal["fact", "preference", "opinion", "event"]


@dataclass(frozen=True)
class Candidate:
    """One retrieval candidate with both signal scores attached.

    `vec_distance` is cosine distance from pgvector — lower = closer. It
    is `None` when either the row has no embedding or the query has no
    embedding (degraded BM25-only mode).

    `bm25_score` is `ts_rank_cd` from Postgres — higher = better. It is
    `None` when the row didn't match the parsed query (instead of zero,
    which would still be 'matched, just barely').
    """

    id: uuid.UUID
    type: MemoryType
    key: str
    value: str
    confidence: float
    source_turn: uuid.UUID
    updated_at: datetime
    vec_distance: float | None
    bm25_score: float | None


_TYPE_PRIORITY: dict[str, int] = {
    "fact": 1,
    "preference": 2,
    "opinion": 3,
    "event": 4,
}


def rrf_rank(
    candidates: list[Candidate],
    *,
    k: int = 60,
    top_k: int = 10,
    floor: float = 0.0,
) -> list[tuple[Candidate, float]]:
    """Rank candidates by reciprocal rank fusion.

    score = 1/(k + vec_rank)  +  1/(k + bm25_rank)

    Each term contributes 0 if the candidate isn't in that list. The
    constant ``k=60`` is the literature default. Drop any candidate with
    score ≤ ``floor`` (default 0 — i.e., didn't match either signal).
    Tiebreaker: type-priority asc, then ``updated_at`` desc, then
    ``confidence`` desc, then ``id`` for full determinism.
    """
    if not candidates:
        return []

    # Tiebreak vec/bm25 ordering consistently so a candidate with identical
    # signals always lands at the same rank regardless of input order.
    def _tiebreak(c: Candidate) -> tuple:
        return (
            _TYPE_PRIORITY.get(c.type, 99),
            -c.updated_at.timestamp(),
            -c.confidence,
            str(c.id),
        )

    vec_ranked = sorted(
        (c for c in candidates if c.vec_distance is not None),
        key=lambda c: (c.vec_distance, *_tiebreak(c)),  # type: ignore[arg-type]
    )
    bm25_ranked = sorted(
        (c for c in candidates if c.bm25_score is not None),
        key=lambda c: (-c.bm25_score, *_tiebreak(c)),  # type: ignore[operator]
    )

    vec_rank: dict[uuid.UUID, int] = {c.id: i + 1 for i, c in enumerate(vec_ranked)}
    bm25_rank: dict[uuid.UUID, int] = {c.id: i + 1 for i, c in enumerate(bm25_ranked)}

    def score(c: Candidate) -> float:
        s = 0.0
        if c.id in vec_rank:
            s += 1.0 / (k + vec_rank[c.id])
        if c.id in bm25_rank:
            s += 1.0 / (k + bm25_rank[c.id])
        return s

    scored = [(c, score(c)) for c in candidates]
    scored = [(c, s) for c, s in scored if s > floor]

    scored.sort(
        key=lambda pair: (
            -pair[1],                              # RRF desc
            _TYPE_PRIORITY.get(pair[0].type, 99),  # type-priority asc
            -pair[0].updated_at.timestamp(),       # recency desc
            -pair[0].confidence,                   # confidence desc
            str(pair[0].id),                       # determinism
        )
    )

    return scored[:top_k]
