"""POST /recall — return formatted context for the next agent turn.

Fetches the user's active memories, renders them via
``memory_service.recall.render_context`` (pure logic — see that module
for ordering, format, and budget rules). Cold sessions / unknown users
return ``{"context":"","citations":[]}`` per the contract.

Query-aware ranking, supersession, and embedding-based retrieval are
deliberately *not* here; this is the no-ranking baseline.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, status

from memory_service.deps import get_db, require_auth
from memory_service.recall import MemoryRow, render_context
from memory_service.schemas import RecallIn, RecallOut

router = APIRouter(tags=["recall"])


# Events are intentionally excluded from default recall context. Their value
# strings are narrative ("Left Stripe, started at Notion as PM") and routinely
# contain superseded entities, which tripped `forbidden_any` checks for
# probes asking about *current* state. Events still persist and surface via
# `/users/{user_id}/memories`; later commits may opt them in for queries
# that explicitly want history.
_RECALL_SQL = """
SELECT id, type, key, value, confidence, source_turn, updated_at
FROM memories
WHERE user_id = $1
  AND active = TRUE
  AND type IN ('fact', 'preference', 'opinion')
ORDER BY
    CASE type
        WHEN 'fact'       THEN 1
        WHEN 'preference' THEN 2
        WHEN 'opinion'    THEN 3
        ELSE 4
    END,
    updated_at DESC,
    confidence DESC
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
) -> RecallOut:
    if not payload.user_id:
        # Without a user we can't load memories — cold-session contract.
        return RecallOut(context="", citations=[])

    async with db.acquire() as conn:
        rows = await conn.fetch(_RECALL_SQL, payload.user_id)

    memories = [
        MemoryRow(
            id=r["id"],
            type=r["type"],
            key=r["key"],
            value=r["value"],
            confidence=r["confidence"],
            source_turn=r["source_turn"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]

    context, citations = render_context(memories, payload.max_tokens)
    return RecallOut(context=context, citations=citations)
