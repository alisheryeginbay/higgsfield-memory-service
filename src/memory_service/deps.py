"""FastAPI dependencies: db pool, optional bearer-token auth."""

from typing import TYPE_CHECKING

from fastapi import Header, HTTPException, Request, status

from memory_service.config import Settings, get_settings

if TYPE_CHECKING:
    import asyncpg


def get_db(request: Request) -> "asyncpg.Pool":
    """Return the shared asyncpg pool attached to app.state in lifespan."""
    pool = getattr(request.app.state, "db", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database not initialised",
        )
    return pool


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
