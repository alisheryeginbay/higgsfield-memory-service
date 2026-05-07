"""FastAPI app factory + lifespan."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from memory_service.api.health import router as health_router
from memory_service.config import get_settings
from memory_service.db import create_pool
from memory_service.schemas import ErrorOut

log = logging.getLogger("memory_service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log.info("memory-service starting up")
    app.state.db = await create_pool(settings.database_url)
    try:
        yield
    finally:
        log.info("memory-service shutting down")
        await app.state.db.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="memory-service",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(health_router)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorOut(error="internal_error").model_dump(exclude_none=True),
        )

    return app


app = create_app()
