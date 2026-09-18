"""Run-isolated AsyncMemory bridge for phased LoCoMo evaluation."""

import os
import re
import shutil

from evalcore.memory_bridge_async_fixed import AsyncMem0Bridge, _created_at


class PhasedAsyncMem0Bridge(AsyncMem0Bridge):
    """AsyncMemory bridge that preserves its store across process phases."""

    async def add_messages(self, messages, user_id: str, timestamp=None):
        created_at = _created_at(timestamp)
        metadata = {}
        if created_at:
            metadata["created_at"] = created_at
            metadata["observation_date"] = created_at

        prompt = None
        if created_at:
            prompt = (
                f"The observation date for these messages is {created_at}. "
                "Resolve relative dates against this observation date. "
                "Do not use the current system date as the conversation date."
            )

        async with self._write_semaphore:
            return await self._memory.add(
                messages,
                user_id=user_id,
                metadata=metadata or None,
                prompt=prompt,
            )


def phased_storage_root(eval_root: str, model_name: str, run_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id):
        raise ValueError("run_id may contain only letters, numbers, dot, underscore, and hyphen")
    return os.path.join(
        eval_root,
        "results",
        "mem_data",
        f"mem0_async_phased_{model_name}_{run_id}",
    )


def build_phased_async_mem0(
    model_cfg,
    eval_root: str,
    run_id: str,
    write_concurrency: int = 1,
    *,
    fresh: bool = False,
    require_existing: bool = False,
):
    storage_root = phased_storage_root(eval_root, model_cfg.name, run_id)
    checkpoint_path = os.path.join(storage_root, "ingest_checkpoint.json")
    existing_files = os.path.isdir(storage_root) and bool(os.listdir(storage_root))

    if fresh:
        shutil.rmtree(storage_root, ignore_errors=True)

    if existing_files and not fresh and not require_existing and not os.path.isfile(checkpoint_path):
        raise RuntimeError(
            f"Store exists but its ingest checkpoint is missing: {storage_root}. "
            "Use a new run_id, restore the checkpoint, or restart explicitly with --fresh."
        )

    if require_existing and not os.path.isdir(storage_root):
        raise FileNotFoundError(
            f"Memory store does not exist for run_id={run_id}: {storage_root}"
        )

    os.makedirs(storage_root, exist_ok=True)
    print(f"[mem0-async-phased] storage: {storage_root}", flush=True)
    return PhasedAsyncMem0Bridge(model_cfg, storage_root, write_concurrency), storage_root
