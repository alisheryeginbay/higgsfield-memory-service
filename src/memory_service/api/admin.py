"""DELETE /sessions/{session_id} and DELETE /users/{user_id}.

Both are idempotent — return 204 even when nothing matches.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Response, status

from memory_service.deps import get_db, require_auth

router = APIRouter(tags=["admin"])


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[Depends(require_auth)],
)
async def delete_session(
    session_id: str,
    db: asyncpg.Pool = Depends(get_db),
) -> Response:
    async with db.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM memories WHERE source_session = $1", session_id)
        await conn.execute("DELETE FROM turns WHERE session_id = $1", session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[Depends(require_auth)],
)
async def delete_user(
    user_id: str,
    db: asyncpg.Pool = Depends(get_db),
) -> Response:
    async with db.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM memories WHERE user_id = $1", user_id)
        await conn.execute("DELETE FROM turns WHERE user_id = $1", user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
