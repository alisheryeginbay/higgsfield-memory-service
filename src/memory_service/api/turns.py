"""POST /turns — persist a turn, return its id.

Extraction is intentionally not done here yet — that lands in a later
milestone. This stub gives us a real, persisted row from day one so
restart-persistence behaviour can be exercised end-to-end.
"""

from __future__ import annotations

import json

import asyncpg
from fastapi import APIRouter, Depends, status

from memory_service.deps import get_db, require_auth
from memory_service.schemas import TurnIn, TurnOut

router = APIRouter(tags=["turns"])


@router.post(
    "/turns",
    response_model=TurnOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
)
async def post_turn(
    payload: TurnIn,
    db: asyncpg.Pool = Depends(get_db),
) -> TurnOut:
    messages_json = json.dumps([m.model_dump(exclude_none=True) for m in payload.messages])
    metadata_json = json.dumps(payload.metadata or {})

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO turns (session_id, user_id, messages, ts, metadata)
            VALUES ($1, $2, $3::jsonb, $4, $5::jsonb)
            RETURNING id
            """,
            payload.session_id,
            payload.user_id,
            messages_json,
            payload.timestamp,
            metadata_json,
        )

    return TurnOut(id=str(row["id"]))
