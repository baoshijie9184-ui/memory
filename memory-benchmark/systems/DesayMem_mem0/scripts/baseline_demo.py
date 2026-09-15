"""Cockpit baseline demo for DesayMem_mem0.

Requires live LLM, embedding, and PostgreSQL from `.env`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from desaymem.asyncio_compat import ensure_compatible_event_loop
from desaymem.core.config import Settings
from desaymem.core.logging import configure_logging, get_logger
from desaymem.core.memory import DesayMemory

logger = get_logger(__name__)


async def run_demo() -> int:
    settings = Settings()
    configure_logging(settings.log_level)
    settings.require_runtime_secrets()
    memory = DesayMemory.from_settings(settings)
    await memory.prepare()
    tenant = "demo"
    user = "user_001"
    await memory.delete_all(user, tenant_id=tenant)
    added = await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        user_id=user,
        tenant_id=tenant,
        vehicle_id="vehicle_001",
        occupant_id="primary",
        session_id="session_001",
        scene="driving",
        source="conversation",
    )
    hits = await memory.search(
        "用户习惯的空调温度是多少",
        user_id=user,
        tenant_id=tenant,
        filters={"vehicle_id": "vehicle_001"},
        top_k=5,
    )
    listed = await memory.get_all(user, tenant_id=tenant)
    print(json.dumps({"added": added, "search": hits, "listed": listed}, ensure_ascii=False, indent=2))
    ok = bool(added) and any("22" in item["content"] for item in hits)
    await memory.close()
    if not ok:
        logger.error("Baseline demo did not recover the 22-degree preference")
        return 1
    print("baseline demo passed")
    return 0


def main() -> None:
    ensure_compatible_event_loop()
    raise SystemExit(asyncio.run(run_demo()))


if __name__ == "__main__":
    main()
