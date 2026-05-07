"""POST /recall — query-aware hybrid retrieval over the memory store.

Pipeline:
1. Embed query (Voyage; falls back to BM25-only if Voyage hiccups).
2. ONE SQL query returning every active memory for the user with both
   signal scores attached: cosine distance vs query embedding, BM25
   rank vs the query text. Either side can be NULL (degraded mode).
3. ``retrieval.rrf_rank`` fuses the two lists via reciprocal rank.
4. Top-K candidates render through ``recall.render_context`` with
   citations carrying the RRF score.

Cold sessions / unknown users / nothing-matched all return
``{"context":"","citations":[]}`` per the contract.
"""

from __future__ import annotations

import logging

import asyncpg
from fastapi import APIRouter, Depends, status

from memory_service.config import get_settings
from memory_service.deps import get_db, get_embedder, require_auth
from memory_service.embeddings import Embedder
from memory_service.recall import MemoryRow, render_context
from memory_service.retrieval import Candidate, rrf_rank
from memory_service.schemas import RecallIn, RecallOut

router = APIRouter(tags=["recall"])
log = logging.getLogger(__name__)


_RECALL_SQL = """
SELECT
    id, type, key, value, confidence, source_turn, updated_at,
    CASE
        WHEN $1::vector IS NOT NULL AND embedding IS NOT NULL
        THEN embedding <=> $1::vector
        ELSE NULL
    END AS vec_distance,
    CASE
        WHEN $2::text <> '' AND tsv @@ plainto_tsquery('english', $2)
        THEN ts_rank_cd(tsv, plainto_tsquery('english', $2))
        ELSE NULL
    END AS bm25_score
FROM memories
WHERE user_id = $3 AND active = TRUE
  -- Events stay out of default recall (their narrative values often
  -- contain superseded entities, which trip substring forbidden_any
  -- checks for "current" queries). Reintroducing them needs query-
  -- intent classification — deferred to a later commit.
  AND type IN ('fact', 'preference', 'opinion')
"""


@router.post(
    "/recall",
    response_model=RecallOut,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_auth)],
)
async def post_recall(
    payload: RecallIn,
    db: asyncpg.Pool = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
) -> RecallOut:
    if not payload.user_id:
        return RecallOut(context="", citations=[])

    # Best-effort query embed. Voyage hiccups → BM25-only retrieval.
    query_emb: list[float] | None = None
    if payload.query.strip():
        try:
            query_emb = await embedder.embed_query(payload.query)
        except Exception:
            log.warning(
                "embed_query failed; falling back to BM25-only", exc_info=True
            )
            query_emb = None

    async with db.acquire() as conn:
        rows = await conn.fetch(
            _RECALL_SQL,
            query_emb,
            payload.query or "",
            payload.user_id,
        )

    if not rows:
        return RecallOut(context="", citations=[])

    candidates = [
        Candidate(
            id=r["id"],
            type=r["type"],
            key=r["key"],
            value=r["value"],
            confidence=r["confidence"],
            source_turn=r["source_turn"],
            updated_at=r["updated_at"],
            vec_distance=r["vec_distance"],
            bm25_score=r["bm25_score"],
        )
        for r in rows
    ]

    settings = get_settings()
    ranked = rrf_rank(candidates, top_k=settings.recall_top_k)
    if not ranked:
        return RecallOut(context="", citations=[])

    memories = [
        MemoryRow(
            id=c.id,
            type=c.type,
            key=c.key,
            value=c.value,
            confidence=c.confidence,
            source_turn=c.source_turn,
            updated_at=c.updated_at,
        )
        for c, _ in ranked
    ]
    score_lookup = {c.id: s for c, s in ranked}

    context, citations = render_context(
        memories, payload.max_tokens, score_lookup=score_lookup
    )
    return RecallOut(context=context, citations=citations)
