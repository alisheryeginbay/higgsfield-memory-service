"""GET /users/{user_id}/memories — inspect stored memories.

Reads from the `memories` table directly. Returns an empty list when no
extraction has run yet, which is the expected state until the extraction
pipeline lands.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends

from memory_service.deps import get_db, require_auth
from memory_service.schemas import MemoriesOut, Memory

router = APIRouter(tags=["memories"])


@router.get(
    "/users/{user_id}/memories",
    response_model=MemoriesOut,
    dependencies=[Depends(require_auth)],
)
async def list_memories(
    user_id: str,
    db: asyncpg.Pool = Depends(get_db),
) -> MemoriesOut:
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, type, key, value, confidence,
                   source_session, source_turn,
                   created_at, updated_at, supersedes, active
            FROM memories
            WHERE user_id = $1
            ORDER BY updated_at DESC
            """,
            user_id,
        )

    return MemoriesOut(
        memories=[
            Memory(
                id=str(r["id"]),
                type=r["type"],
                key=r["key"],
                value=r["value"],
                confidence=r["confidence"],
                source_session=r["source_session"],
                source_turn=str(r["source_turn"]),
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                supersedes=str(r["supersedes"]) if r["supersedes"] is not None else None,
                active=r["active"],
            )
            for r in rows
        ]
    )
