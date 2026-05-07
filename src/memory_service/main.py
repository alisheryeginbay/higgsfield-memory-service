"""FastAPI app factory + lifespan."""

import logging
from contextlib import asynccontextmanager

import anthropic
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from memory_service.api.admin import router as admin_router
from memory_service.api.health import router as health_router
from memory_service.api.memories import router as memories_router
from memory_service.api.recall import router as recall_router
from memory_service.api.search import router as search_router
from memory_service.api.turns import router as turns_router
from memory_service.config import get_settings
from memory_service.db import create_pool
from memory_service.extraction import ClaudeExtractor, NoopExtractor
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

    if settings.anthropic_api_key:
        anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        app.state.extractor = ClaudeExtractor(
            client=anthropic_client,
            model=settings.anthropic_model_fast,
            confidence_floor=settings.extraction_confidence_floor,
            max_tokens=settings.extraction_max_tokens,
        )
        log.info(
            "extraction enabled — model=%s confidence_floor=%.2f",
            settings.anthropic_model_fast,
            settings.extraction_confidence_floor,
        )
    else:
        anthropic_client = None
        app.state.extractor = NoopExtractor()
        log.warning("ANTHROPIC_API_KEY not set — extraction disabled (NoopExtractor)")

    try:
        yield
    finally:
        log.info("memory-service shutting down")
        await app.state.db.close()
        if anthropic_client is not None:
            await anthropic_client.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="memory-service",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(health_router)
    app.include_router(turns_router)
    app.include_router(recall_router)
    app.include_router(search_router)
    app.include_router(memories_router)
    app.include_router(admin_router)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorOut(error="internal_error").model_dump(exclude_none=True),
        )

    return app


app = create_app()
