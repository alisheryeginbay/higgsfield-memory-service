"""FastAPI dependencies: auth (db pool wired up in the next milestone)."""

from fastapi import Header, HTTPException, status

from memory_service.config import Settings, get_settings


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
