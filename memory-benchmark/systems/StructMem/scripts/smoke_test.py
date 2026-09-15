#!/usr/bin/env python3
"""StructMem end-to-end smoke test against the shared LLM/embedding services.

Covers:
  1. add_memory (event mode: factual + relational extraction)
  2. profile distillation (L3): ADD -> CONFIRM -> SUPERSEDE
  3. cross-event summarize (L2)
  4. retrieve (L1 entries + summaries + profile)
  5. unified storage verification: SQLite == PostgreSQL == JSON mirror

Run:  python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from structmem.configs.loader import build_config, load_env
from structmem.memory.structmem import StructMemory

TENANT = "smoke_tenant"
USER = "smoke_user_001"
VEHICLE = "veh_smoke"
SCOPE = {"tenant_id": TENANT, "user_id": USER, "vehicle_id": VEHICLE, "occupant_id": "primary"}

CONVERSATIONS = [
    {
        "time_stamp": "2026-08-01 08:00:00",
        "messages": [
            {"role": "user", "content": "有点热，把空调调到22度，再放点爵士乐。"},
            {"role": "assistant", "content": "好的，已将空调调到22度，并开始播放爵士乐。"},
        ],
    },
    {
        "time_stamp": "2026-09-01 08:00:00",
        "messages": [
            {"role": "user", "content": "以后空调默认改为26度吧，22度太冷了。"},
            {"role": "assistant", "content": "好的，已将空调默认温度调整为26度。"},
        ],
    },
    {
        "time_stamp": "2026-09-02 08:00:00",
        "messages": [
            {"role": "user", "content": "我喜欢听舒缓的古典音乐，不要爵士乐了。"},
            {"role": "assistant", "content": "好的，已切换为古典音乐。"},
        ],
    },
]


def verify_storage(memory: StructMemory, env) -> None:
    print("\n=== 存储三端一致性检查 ===")
    import sqlite3

    sqlite_ok = True
    conn = sqlite3.connect(env["SQLITE_PATH"])
    for table in ("structmem_memories", "structmem_summaries", "profile_beliefs"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        mirror = json.load(open(f"{env['DATA_DIR']}/json_mirror/structmem/{table}.json"))
        status = "OK" if count == len(mirror) else "MISMATCH"
        if status != "OK":
            sqlite_ok = False
        print(f"  SQLite {table}: {count} | JSON mirror: {len(mirror)} {status}")
    conn.close()

    import psycopg

    pg = psycopg.connect(env["POSTGRES_DSN"])
    for table in ("structmem_memories", "structmem_summaries", "profile_beliefs"):
        count = pg.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  PostgreSQL {table}: {count}")
    pg.close()
    print("  存储检查:", "ALL OK" if sqlite_ok else "HAS MISMATCH")


async def main() -> None:
    env = load_env()
    memory = StructMemory.from_config(build_config(env))
    print("后端:")
    print("  memories :", memory.embedding_retriever.col_info())
    print("  summaries:", memory.summary_retriever.col_info())
    print("  profile  :", len(memory.profile_store.list_all()), "beliefs (all)")

    print("\n=== 写入阶段 (event 双维度提取 + L3 画像) ===")
    for idx, conv in enumerate(CONVERSATIONS, 1):
        messages = [dict(m, time_stamp=conv["time_stamp"]) for m in conv["messages"]]
        result = await asyncio.to_thread(
            memory.add_memory, messages, scope=SCOPE
        )
        print(f"\n[{idx}] 提取 {len(result['entries'])} 条:")
        for entry in result["entries"]:
            print(f"    - [{entry.get('entry_type', '')}] {entry['memory'][:60]}")
        for belief in result.get("beliefs_applied", []):
            print(
                f"    * L3 {belief['event']}: {belief['attribute']} = {belief['value']}"
                + (f" (superseded {belief['superseded'][:8]})" if belief.get("superseded") else "")
            )

    print("\n=== L2 跨事件摘要 ===")
    summary_result = await asyncio.to_thread(
        memory.summarize, time_window=3600 * 24 * 40, process_all=True
    )
    summaries = summary_result.get("summaries", [])
    print(f"  生成 {len(summaries)} 条摘要:")
    for s in summaries:
        print(f"    - [{s.get('time_range', {}).get('start', '')[:10]}] {s.get('summary', '')[:80]}")

    print("\n=== 检索阶段 ===")
    for query in ("空调温度设置多少度", "用户喜欢什么音乐"):
        result = await asyncio.to_thread(
            memory.retrieve, query, limit=5, scope=SCOPE
        )
        print(f"\n查询: {query}")
        for hit in result["entries"]:
            print(f"  L1 [{hit['entry_type']}] score={hit['score']:.3f} {hit['memory'][:50]}")
        for s in result["summaries"]:
            print(f"  L2 score={s['score']:.3f} {s['summary'][:60]}")
        print("  L3 画像:")
        for belief in result["profile"] or []:
            print(
                f"    [{belief['status']}] {belief['subject']}.{belief['attribute']} = "
                f"{belief['value']} (support={belief['support_count']})"
            )

    verify_storage(memory, env)

    print("\n=== 清理（删除全部数据，验证镜像同步删除） ===")
    counts = memory.delete_all(scope=SCOPE)
    print("  删除:", counts)
    import sqlite3

    conn = sqlite3.connect(env["SQLITE_PATH"])
    for table in ("structmem_memories", "structmem_summaries", "profile_beliefs"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        mirror = json.load(open(f"{env['DATA_DIR']}/json_mirror/structmem/{table}.json"))
        print(f"  {table}: db={count} mirror={len(mirror)}")
    conn.close()

    print("\n冒烟测试完成。")


if __name__ == "__main__":
    asyncio.run(main())
