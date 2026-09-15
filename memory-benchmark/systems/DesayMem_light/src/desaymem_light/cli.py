"""Cloud process entrypoints; migrations are intentionally separate from services."""

from __future__ import annotations

import asyncio
import shutil
import socket
import sqlite3
from pathlib import Path

import psycopg
import uvicorn

from desaymem_light.adapters.postgres.migrations import apply_postgres_migrations
from desaymem_light.adapters.sqlite.migrations import apply_sqlite_migrations
from desaymem_light.bootstrap.runtime import build_runtime
from desaymem_light.bootstrap.settings import RuntimeSettings
from desaymem_light.contracts.providers import ChatRequest
from desaymem_light.schema import LATEST_POSTGRES_SCHEMA_VERSION
from desaymem_light.workers.memory_worker import MemoryJobDispatcher, MemoryWorker
from desaymem_light.workers.mirror_worker import MirrorWorker


def api_main() -> None:
    settings = RuntimeSettings()
    uvicorn.run(
        "desaymem_light.api.main:app", host=settings.desaymem_http_host,
        port=settings.desaymem_http_port, log_level=settings.desaymem_log_level.lower(),
    )


def migrate_main() -> None:
    settings = RuntimeSettings()
    settings.validate_cloud_runtime()
    root = Path(__file__).resolve().parents[2]
    with psycopg.connect(settings.postgres_dsn) as connection:
        applied = apply_postgres_migrations(connection, root / "migrations" / "postgres")
    sqlite_path = Path(settings.sqlite_path)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(sqlite_path) as connection:
        sqlite_applied = apply_sqlite_migrations(connection, root / "migrations" / "sqlite")
    print(f"postgres migrations applied={len(applied)}; sqlite migrations applied={len(sqlite_applied)}")


async def _worker() -> None:
    runtime = build_runtime()
    await runtime.pool.open()
    dispatcher = MemoryJobDispatcher(
        session_pipeline=runtime.session, memory_pipeline=runtime.memory,
        insight_pipeline=runtime.insight, profile_pipeline=runtime.profile,
    )
    worker = MemoryWorker(
        runtime.uow, dispatcher, worker_id=f"memory-{socket.gethostname()}",
        lock_timeout_seconds=runtime.config.jobs.lock_timeout_seconds,
    )
    try:
        while True:
            count = await worker.run_once()
            if not count:
                await asyncio.sleep(runtime.config.jobs.poll_interval_ms / 1000)
    finally:
        await runtime.close()


def worker_main() -> None:
    asyncio.run(_worker())


async def _mirror_worker() -> None:
    runtime = build_runtime()
    await runtime.pool.open()
    options = runtime.config.options_for(runtime.config.modules.mirror)
    worker = MirrorWorker(
        runtime.pool.connection, runtime.plugins.mirror,
        worker_id=f"mirror-{socket.gethostname()}",
        batch_size=int(options.get("batch_size", 100)),
        lock_timeout_seconds=runtime.config.jobs.lock_timeout_seconds,
    )
    try:
        while True:
            count = await worker.run_once()
            if not count:
                await asyncio.sleep(runtime.config.jobs.poll_interval_ms / 1000)
    finally:
        await runtime.close()


def mirror_worker_main() -> None:
    asyncio.run(_mirror_worker())


async def _preflight() -> None:
    runtime = build_runtime()
    mirror_root = Path(runtime.settings.json_mirror_root)
    mirror_root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(mirror_root).free
    if free < runtime.settings.json_mirror_min_free_bytes:
        raise RuntimeError("JSON mirror volume has insufficient free space")
    await runtime.pool.open()
    try:
        async with runtime.pool.connection() as connection:
            version = (await (await connection.execute(
                "SELECT max(version) AS version FROM desaymem_light.schema_migrations"
            )).fetchone())["version"]
            vector = (await (await connection.execute(
                "SELECT extversion FROM pg_extension WHERE extname='vector'"
            )).fetchone())
            triggers = (await (await connection.execute(
                "SELECT count(*) AS count FROM desaymem_light.json_mirror_registry WHERE enabled"
            )).fetchone())["count"]
        if version != LATEST_POSTGRES_SCHEMA_VERSION or not vector or triggers < 1:
            raise RuntimeError("database migration, pgvector, or mirror registry check failed")
        result = await runtime.plugins.embedding.embed(["DesayMem cloud preflight"], "search")
        if len(result.vectors[0]) != 1024:
            raise RuntimeError("BGE-M3 dimension check failed")
        llm_result = await runtime.plugins.llm.generate_json(ChatRequest(
            messages=[{"role": "user", "content": "Return JSON: {\"ok\": true}"}],
            response_schema={"type": "object", "properties": {"ok": {"type": "boolean"}},
                             "required": ["ok"]},
            max_output_tokens=16, temperature=0,
        ))
        if llm_result.data.get("ok") is not True:
            raise RuntimeError("Qwen JSON response check failed")
        print(f"preflight ok: schema={version}, mirror_tables={triggers}, embedding_dims=1024")
    finally:
        await runtime.close()


def preflight_main() -> None:
    asyncio.run(_preflight())
