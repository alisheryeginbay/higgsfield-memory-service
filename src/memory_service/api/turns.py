"""POST /turns — persist a turn, run extraction, persist memories.

The turn insert and the memory inserts use *separate* transactions on the
same connection. If extraction or memory persistence fails, the turn
still lands and we log the failure — extraction is derived enrichment,
the turn itself is the user's data and must not be lost over an LLM
hiccup or a transient API error.
"""

from __future__ import annotations

import json
import logging

import asyncpg
from fastapi import APIRouter, Depends, status

from memory_service.deps import get_db, get_extractor, require_auth
from memory_service.extraction import Extractor, persist_memories
from memory_service.schemas import TurnIn, TurnOut

router = APIRouter(tags=["turns"])
log = logging.getLogger(__name__)


@router.post(
    "/turns",
    response_model=TurnOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
)
async def post_turn(
    payload: TurnIn,
    db: asyncpg.Pool = Depends(get_db),
    extractor: Extractor = Depends(get_extractor),
) -> TurnOut:
    messages_json = json.dumps([m.model_dump(exclude_none=True) for m in payload.messages])
    metadata_json = json.dumps(payload.metadata or {})

    async with db.acquire() as conn:
        async with conn.transaction():
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
        turn_id = row["id"]

        # Extraction is best-effort. We catch broadly so the turn always
        # lands regardless of LLM availability or response shape.
        if payload.user_id:
            try:
                memories = await extractor.extract(payload.messages)
            except Exception:
                log.warning("extraction failed for turn %s", turn_id, exc_info=True)
                memories = []

            if memories:
                try:
                    async with conn.transaction():
                        await persist_memories(
                            conn,
                            user_id=payload.user_id,
                            source_session=payload.session_id,
                            source_turn=turn_id,
                            memories=memories,
                        )
                except Exception:
                    log.warning(
                        "memory persistence failed for turn %s",
                        turn_id,
                        exc_info=True,
                    )

    return TurnOut(id=str(turn_id))
