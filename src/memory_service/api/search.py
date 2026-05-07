"""POST /search — explicit memory search invoked by an agent tool call.

Different shape from `/recall`: structured `SearchHit` results
(content, score, session_id, timestamp, metadata) instead of formatted
prose. Reuses the same hybrid retrieval pipeline (`embeddings.embed_query`
→ vector + BM25 SQL → `retrieval.rrf_rank`) but ranks across all four
memory types (events included, unlike `/recall`) and filters
dynamically by `user_id` and/or `source_session`.

At least one of `user_id` or `session_id` must be provided —
unfiltered search would leak data across users in concurrent-session
deployments. Both `null` → ``{"results":[]}``.

Inactive (superseded) memories are excluded; the inspection endpoint
``/users/{user_id}/memories`` exposes the full chain.
"""

from __future__ import annotations

import logging

import asyncpg
from fastapi import APIRouter, Depends, status

from memory_service.deps import get_db, get_embedder, require_auth
from memory_service.embeddings import Embedder
from memory_service.retrieval import Candidate, rrf_rank
from memory_service.schemas import SearchHit, SearchIn, SearchOut

router = APIRouter(tags=["search"])
log = logging.getLogger(__name__)


_SEARCH_SQL = """
SELECT
    id, type, key, value, confidence, source_session, source_turn, updated_at,
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
WHERE active = TRUE
  AND ($3::text IS NULL OR user_id = $3)
  AND ($4::text IS NULL OR source_session = $4)
"""


@router.post(
    "/search",
    response_model=SearchOut,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_auth)],
)
async def post_search(
    payload: SearchIn,
    db: asyncpg.Pool = Depends(get_db),
    embedder: Embedder = Depends(get_embedder),
) -> SearchOut:
    # Require at least one filter — refuse to scan across users.
    if not payload.user_id and not payload.session_id:
        return SearchOut(results=[])

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
            _SEARCH_SQL,
            query_emb,
            payload.query or "",
            payload.user_id,
            payload.session_id,
        )

    if not rows:
        return SearchOut(results=[])

    candidates = [
        Candidate(
            id=r["id"],
            type=r["type"],
            key=r["key"],
            value=r["value"],
            confidence=r["confidence"],
            source_session=r["source_session"],
            source_turn=r["source_turn"],
            updated_at=r["updated_at"],
            vec_distance=r["vec_distance"],
            bm25_score=r["bm25_score"],
        )
        for r in rows
    ]

    ranked = rrf_rank(candidates, top_k=payload.limit)
    if not ranked:
        return SearchOut(results=[])

    results = [
        SearchHit(
            content=f"{c.key}: {c.value}",
            score=score,
            session_id=c.source_session,
            timestamp=c.updated_at,
            metadata={
                "type": c.type,
                "key": c.key,
                "confidence": c.confidence,
            },
        )
        for c, score in ranked
    ]
    return SearchOut(results=results)
