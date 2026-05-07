"""FastAPI dependencies: db pool, extractor, optional bearer-token auth."""

from typing import TYPE_CHECKING

from fastapi import Header, HTTPException, Request, status

from memory_service.config import Settings, get_settings

if TYPE_CHECKING:
    import asyncpg

    from memory_service.embeddings import Embedder
    from memory_service.extraction import Extractor


def get_db(request: Request) -> "asyncpg.Pool":
    """Return the shared asyncpg pool attached to app.state in lifespan."""
    pool = getattr(request.app.state, "db", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database not initialised",
        )
    return pool


def get_extractor(request: Request) -> "Extractor":
    """Return the shared extractor (Claude or Noop) attached in lifespan."""
    extractor = getattr(request.app.state, "extractor", None)
    if extractor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="extractor not initialised",
        )
    return extractor


def get_embedder(request: Request) -> "Embedder":
    """Return the shared embedder (Voyage or Noop) attached in lifespan."""
    embedder = getattr(request.app.state, "embedder", None)
    if embedder is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="embedder not initialised",
        )
    return embedder


async def require_auth(
    authorization: str | None = Header(default=None),
) -> None:
    """Optional bearer-token auth.

    If `MEMORY_AUTH_TOKEN` is set in the environment, every protected
    endpoint requires `Authorization: Bearer <token>`. If unset, the header
    is ignored.
    """
    settings: Settings = get_settings()
    expected = settings.memory_auth_token
    if not expected:
        return  # auth disabled

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
