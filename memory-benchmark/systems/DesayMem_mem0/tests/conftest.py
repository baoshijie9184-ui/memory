from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest
import httpx

from desaymem.api.main import create_app
from desaymem.core.config import Settings
from desaymem.core.memory import DesayMemory
from desaymem.services.memory_service import MemoryService

LIVE_TENANT = "pytest_live"
CLEAN_USERS = (
    "user_001",
    "user_a",
    "user_b",
    "user_driver",
    "user_passenger",
    "driver",
    "passenger",
    "user_hist",
    "someone_else",
)
CLEAN_TENANTS = (LIVE_TENANT, "pytest_oem_a", "pytest_oem_b")
HISTORY_PATH = str(Path(__file__).resolve().parent / ".live_history.db")
API_HISTORY_PATH = str(Path(__file__).resolve().parent / ".live_api_history.db")


async def _wipe(memory: DesayMemory) -> None:
    for tenant_id in CLEAN_TENANTS:
        for user_id in CLEAN_USERS:
            await memory.delete_all(user_id, tenant_id=tenant_id)


@pytest.fixture(scope="session")
def live_settings() -> Settings:
    settings = Settings()
    settings.require_runtime_secrets()
    settings.search_threshold = 0.0
    settings.history_db_path = HISTORY_PATH
    return settings


@pytest.fixture(scope="session")
async def session_memory(live_settings: Settings) -> DesayMemory:
    memory = DesayMemory.from_settings(live_settings)
    await memory.prepare()
    print("\nlive backends:", memory.backend_info())
    try:
        yield memory
    finally:
        await _wipe(memory)
        await memory.close()


@pytest.fixture
async def memory(session_memory: DesayMemory) -> DesayMemory:
    await _wipe(session_memory)
    yield session_memory
    await _wipe(session_memory)


@pytest.fixture
async def api_client(live_settings: Settings):
    settings = live_settings.model_copy(update={"history_db_path": API_HISTORY_PATH})
    memory = DesayMemory.from_settings(settings)
    await memory.prepare()
    await _wipe(memory)
    app = create_app(memory=memory, settings=settings)
    app.state.settings = settings
    app.state.memory = memory
    app.state.service = MemoryService(memory)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        await _wipe(memory)
        await memory.close()
