"""Run the cockpit scenario with live DashScope + persistent Mem0-style stores.

Memories and entities go to PostgreSQL (pgvector). History and last-k messages
go to a SQLite file. In-memory stores are not used.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from desaymem.asyncio_compat import ensure_compatible_event_loop
from desaymem.core.config import Settings
from desaymem.core.enums import MemoryType
from desaymem.core.memory import DesayMemory

TENANT = "oem_chery"
DRIVER = "user_driver"
PASSENGER = "user_passenger"
SESSION = "session_drive_001"


def _print(step: str, detail: str = "") -> None:
    suffix = f"  {detail}" if detail else ""
    print(f"  {step}{suffix}")


def _print_backends(memory: DesayMemory) -> None:
    print("stores (Mem0-style persistent):")
    for name, detail in memory.backend_info().items():
        print(f"  {name}: {detail}")


async def _build_memory(settings: Settings) -> DesayMemory:
    settings.require_runtime_secrets()
    settings.search_threshold = 0.0
    memory = DesayMemory.from_settings(settings)
    await memory.prepare()
    return memory


async def run_live_cockpit(*, keep: bool = False) -> int:
    settings = Settings()
    memory = await _build_memory(settings)
    print("\n=== DesayMem live cockpit (DashScope, persistent stores) ===")
    print(f"LLM={settings.llm_model}  EMB={settings.embedding_model}")
    _print_backends(memory)

    if keep:
        print("\n[--keep] skipping delete_all; reading existing persisted rows")
    else:
        await memory.delete_all(DRIVER, tenant_id=TENANT)
        await memory.delete_all(PASSENGER, tenant_id=TENANT)

    print("\n[1] Driver climate preference (infer=true)")
    climate = await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        user_id=DRIVER,
        tenant_id=TENANT,
        vehicle_id="vehicle_001",
        occupant_id="primary",
        session_id=SESSION,
        scene="driving",
    )
    assert climate, "climate preference should be extracted"
    _print("ADD climate", climate[0]["content"])

    print("\n[2] Search driver AC preference")
    driver_ac = await memory.search("用户习惯的空调温度是多少", user_id=DRIVER, tenant_id=TENANT, top_k=5)
    assert driver_ac, "search should return at least one memory"
    _print("driver AC hits", ", ".join(row["content"] for row in driver_ac))

    print("\n[3] Passenger isolation")
    passenger = await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到25度"}],
        user_id=PASSENGER,
        tenant_id=TENANT,
        session_id="session_ride_002",
    )
    assert passenger
    _print("ADD passenger climate", passenger[0]["content"])

    passenger_ac = await memory.search("空调温度", user_id=PASSENGER, tenant_id=TENANT, top_k=5)
    assert passenger_ac
    _print("passenger AC hits", ", ".join(row["content"] for row in passenger_ac))

    print("\n[4] Procedural memory type")
    procedure = await memory.add(
        [
            {"role": "user", "content": "请按步骤打开座椅加热"},
            {"role": "assistant", "content": "1. 打开座椅菜单 2. 选择加热 3. 确认"},
        ],
        user_id=DRIVER,
        tenant_id=TENANT,
        memory_type=MemoryType.PROCEDURAL.value,
    )
    assert procedure[0]["memory_type"] == MemoryType.PROCEDURAL.value
    _print("procedural", procedure[0]["content"][:80])

    await memory.close()
    print("\n=== live cockpit passed (rows remain in Postgres + history.db) ===\n")
    print("Re-run with --keep to search without wiping. Inspect with:")
    print(f"  python scripts/inspect_memories.py --tenant-id {TENANT} --user-id {DRIVER}")
    return 0


def main() -> None:
    ensure_compatible_event_loop()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--keep",
        action="store_true",
        help="do not delete_all before the scenario (verify persistence across runs)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_live_cockpit(keep=args.keep)))


if __name__ == "__main__":
    main()
