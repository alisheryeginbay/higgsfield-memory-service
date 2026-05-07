"""asyncpg connection pool with pgvector codec registration.

The pool is opened in the FastAPI lifespan and exposed via `app.state.db`.
Pgvector type codecs are registered on every fresh connection so vector
columns added in later migrations Just Work.
"""

from __future__ import annotations

import asyncpg
from pgvector.asyncpg import register_vector


async def _init_connection(conn: asyncpg.Connection) -> None:
    # Required so asyncpg knows how to encode/decode `vector(...)` columns.
    # Safe to call even before any vector columns exist — it only registers
    # the codec.
    try:
        await register_vector(conn)
    except Exception:
        # CREATE EXTENSION vector hasn't run yet, or the type isn't in the
        # search_path. Don't crash the pool — vector queries will fail
        # explicitly later, which is the correct, loud failure mode.
        pass


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=dsn,
        min_size=min_size,
        max_size=max_size,
        init=_init_connection,
    )
