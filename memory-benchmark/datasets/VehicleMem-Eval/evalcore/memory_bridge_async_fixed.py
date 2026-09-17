"""Isolated asynchronous Mem0 bridge for the LoCoMo runner."""

import asyncio
import os
import shutil
import sqlite3
from datetime import datetime, timezone


def _created_at(timestamp) -> str | None:
    """Convert the benchmark timestamp to Mem0 OSS-compatible metadata."""
    if timestamp is None:
        return None
    if isinstance(timestamp, (int, float)):
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    text = str(timestamp).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return text


class AsyncMem0Bridge:
    """Direct AsyncMemory wrapper; no HTTP or platform API dependency."""

    def __init__(self, model_cfg, storage_root: str, write_concurrency: int = 1):
        print("[mem0 init] importing mem0.AsyncMemory...", flush=True)
        from mem0 import AsyncMemory

        self._history_db_path = os.path.join(storage_root, "history_async_fixed.db")
        self._write_semaphore = asyncio.Semaphore(max(1, write_concurrency))
        config = {
            "history_db_path": self._history_db_path,
            "llm": {
                "provider": "openai",
                "config": {
                    "model": model_cfg.model,
                    "api_key": model_cfg.api_key,
                    "openai_base_url": model_cfg.api_base,
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": model_cfg.embedding_model,
                    "api_key": model_cfg.embedding_key,
                    "openai_base_url": model_cfg.embedding_base,
                    "embedding_dims": model_cfg.embedding_dim,
                },
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": "eval_mem0_async",
                    "path": storage_root,
                    "on_disk": True,
                    "embedding_model_dims": model_cfg.embedding_dim,
                },
            },
        }
        print("[mem0 init] creating AsyncMemory from configuration...", flush=True)
        self._memory = AsyncMemory.from_config(config)
        print("[mem0 init] AsyncMemory is ready.", flush=True)

    async def add_messages(self, messages, user_id: str, timestamp=None):
        metadata = {}
        created_at = _created_at(timestamp)
        if created_at:
            metadata["created_at"] = created_at

        async with self._write_semaphore:
            # infer is intentionally omitted. AsyncMemory defaults it to True.
            # OSS rejects its platform-only timestamp argument, so the LoCoMo
            # observation time is persisted as created_at metadata instead.
            return await self._memory.add(
                messages,
                user_id=user_id,
                metadata=metadata or None,
            )

    async def retrieve_memory(self, query: str, user_id: str, top_k: int = 5):
        result = await self._memory.search(
            query,
            filters={"user_id": user_id},
            top_k=top_k,
        )
        items = result.get("results", result) if isinstance(result, dict) else result
        lines = []
        for item in items or []:
            memory = item.get("memory", "")
            if not memory:
                continue
            created_at = item.get("created_at", "")
            prefix = f"({created_at}) " if created_at else ""
            lines.append(f"- {prefix}{memory}")
        return "\n".join(lines)

    def behavior_stats(self) -> dict:
        if not os.path.isfile(self._history_db_path):
            return {}
        with sqlite3.connect(self._history_db_path) as connection:
            events = dict(connection.execute(
                "SELECT event, COUNT(*) FROM history GROUP BY event"
            ))
        result = {"memory_events": events}
        adds = events.get("ADD", 0)
        deletes = events.get("DELETE", 0)
        if adds + deletes:
            result["delete_ratio"] = round(deletes / (adds + deletes), 4)
        return result

    def close(self):
        self._memory.close()


def build_async_mem0(model_cfg, eval_root: str, write_concurrency: int = 1):
    storage_root = os.path.join(
        eval_root,
        "results",
        "mem_data",
        f"mem0_async_fixed_{model_cfg.name}",
    )
    shutil.rmtree(storage_root, ignore_errors=True)
    os.makedirs(storage_root, exist_ok=True)
    print(f"[mem0-async-fixed] 已重建独立评测库: {storage_root}")
    return AsyncMem0Bridge(model_cfg, storage_root, write_concurrency)
