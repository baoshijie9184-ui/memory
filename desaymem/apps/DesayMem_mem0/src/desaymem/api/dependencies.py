"""FastAPI dependency wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from desaymem.core.config import Settings, get_settings
from desaymem.core.logging import configure_logging, get_logger
from desaymem.core.memory import DesayMemory
from desaymem.services.memory_service import MemoryService

logger = get_logger(__name__)


class AppState:
    def __init__(self) -> None:
        self.settings: Settings | None = None
        self.memory: DesayMemory | None = None
        self.service: MemoryService | None = None


def build_memory(settings: Settings) -> DesayMemory:
    return DesayMemory.from_settings(settings)


def build_lifespan(memory: DesayMemory | None = None, settings: Settings | None = None):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved_settings = settings or get_settings()
        configure_logging(resolved_settings.log_level)
        resolved_memory = memory or build_memory(resolved_settings)
        await resolved_memory.prepare()
        app.state.settings = resolved_settings
        app.state.memory = resolved_memory
        app.state.service = MemoryService(resolved_memory)
        logger.info("DesayMem API started")
        try:
            yield
        finally:
            await resolved_memory.close()
            logger.info("DesayMem API stopped")

    return lifespan


lifespan = build_lifespan()


def get_service(request: Request) -> MemoryService:
    return request.app.state.service


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings
