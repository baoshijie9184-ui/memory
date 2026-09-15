"""DesayMem_mem0 FastAPI application."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from desaymem.api.dependencies import build_lifespan
from desaymem.api.routes.health import router as health_router
from desaymem.api.routes.memories import router as memories_router
from desaymem.core.exceptions import (
    ConfigurationError,
    DatabaseError,
    DesayMemError,
    EmbeddingError,
    LLMError,
    MemoryNotFoundError,
    ValidationError,
)
from desaymem.core.logging import get_logger

logger = get_logger(__name__)

_STATUS = {
    ValidationError: 400,
    MemoryNotFoundError: 404,
    ConfigurationError: 500,
    DatabaseError: 503,
    EmbeddingError: 502,
    LLMError: 502,
    DesayMemError: 500,
}


def create_app(memory=None, settings=None) -> FastAPI:
    app = FastAPI(
        title="DesayMem_mem0",
        description="Privately deployable cockpit long-term memory. Source-migrated from Mem0 OSS, not a mem0ai wrapper.",
        version="0.1.0",
        lifespan=build_lifespan(memory=memory, settings=settings),
    )
    app.include_router(health_router)
    app.include_router(memories_router)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(DesayMemError)
    async def _handle_desaymem_error(_request: Request, exc: DesayMemError) -> JSONResponse:
        status_code = _STATUS.get(type(exc), 500)
        logger.error("API error %s (%s)", exc.error_code, type(exc).__name__)
        return JSONResponse(status_code=status_code, content=exc.to_dict())

    @app.middleware("http")
    async def _health_status(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.rstrip("/") == "/health" and getattr(request.state, "health_failed", False):
            response.status_code = 503
        return response

    return app


app = create_app()
