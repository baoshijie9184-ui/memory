#!/usr/bin/env python3
"""纯 Python 端到端测试，不启动HTTP服务。"""

import asyncio
import json
import os
import sys
import pdb
os.environ["POSTGRES_DSN"] = (
    "postgresql://desaymem@127.0.0.1:20143/bench_desaymem_test"
)
os.environ["HISTORY_DB_PATH"] = (
    "/data/pengshuang/memory-benchmark/data/desaymem/history_test.db"
)

sys.path.insert(
    0,
    "/data/pengshuang/memory-benchmark/systems/DesayMem_mem0/src",
)

from desaymem.core.config import Settings
from desaymem.core.memory import DesayMemory
from desaymem.core.models import MemoryScope


TENANT_ID = "test_tenant"
USER_ID = "test_user_001"
VEHICLE_ID = "test_vehicle_001"


TEST_CASES = [
    {
        "messages": [
            {
                "role": "user",
                "content": "有点热，把空调调到22度，再放点爵士乐。",
            },
            {
                "role": "assistant",
                "content": "好的，已将空调调到22度，并开始播放爵士乐。",
            },
        ],
        "session_id": "sess_test_001",
        "scene": "driving",
        "occurred_at": "2026-08-01T08:00:00+08:00",
    },
    {
        "messages": [
            {
                "role": "user",
                "content": "以后空调温度改为26度吧，22度太冷了。",
            },
            {
                "role": "assistant",
                "content": "好的，已将空调默认温度调整为26度。",
            },
        ],
        "session_id": "sess_test_002",
        "scene": "driving",
        "occurred_at": "2026-09-01T08:00:00+08:00",
    },
    {
        "messages": [
            {
                "role": "user",
                "content": "我喜欢听舒缓的古典音乐，不要爵士了。",
            },
            {
                "role": "assistant",
                "content": "好的，已切换为古典音乐。",
            },
        ],
        "session_id": "sess_test_003",
        "scene": "driving",
        "occurred_at": "2026-09-02T08:00:00+08:00",
    },
]


async def main():
    settings = Settings()

    print("APP_ENV:", settings.app_env)
    print("数据库:", settings.postgres_dsn.rsplit("@", 1)[-1])
    print("SQLite:", settings.history_db_path)

    if "bench_desaymem_test" not in settings.postgres_dsn:
        raise RuntimeError("安全检查失败：没有连接到bench_desaymem_test")

    memory = DesayMemory.from_settings(settings)

    try:
        await memory.prepare()
        print("后端信息:", json.dumps(
            memory.backend_info(),
            ensure_ascii=False,
            indent=2,
        ))

        # 保证本轮实验从干净状态开始
        deleted = await memory.delete_all(
            USER_ID,
            tenant_id=TENANT_ID,
        )
        print(f"实验前清理记忆：{deleted}条")

        print("\n=== 写入阶段 ===")

        for index, case in enumerate(TEST_CASES, 1):
            added = await memory.add(
                case["messages"],
                user_id=USER_ID,
                tenant_id=TENANT_ID,
                vehicle_id=VEHICLE_ID,
                occupant_id="primary",
                session_id=case["session_id"],
                scene=case["scene"],
                source="python_benchmark",
                metadata={
                    "occurred_at": case["occurred_at"],
                    "dataset_id": "smoke_test_v1",
                    "run_id": "run_001",
                },
                infer=True,
            )

            print(f"[{index}] 新增 {len(added)} 条记忆")
            for item in added:
                print(f"    - {item['content']}")

        print("\n=== 搜索阶段 ===")

        queries = [
            "空调温度设置多少度",
            "用户喜欢什么音乐",
            "用户现在偏好什么温度",
        ]

        for query in queries:
            hits = await memory.search(
                query,
                user_id=USER_ID,
                tenant_id=TENANT_ID,
                filters={
                    "vehicle_id": VEHICLE_ID,
                    "occupant_id": "primary",
                },
                top_k=5,
            )

            print(f"\n查询：{query}")

            for rank, hit in enumerate(hits, 1):
                score = hit.get("score") or 0
                occurred_at = (
                    hit.get("metadata") or {}
                ).get("occurred_at")

                print(
                    f"Top{rank} score={score:.4f} "
                    f"occurred_at={occurred_at} | "
                    f"{hit['content']}"
                )

        print("\n=== L1/L2/L3 状态 ===")

        scope = MemoryScope(
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            vehicle_id=VEHICLE_ID,
            occupant_id="primary",
        )

        layers = await memory.get_memory_layers(scope)

        print(json.dumps(
            layers["stats"],
            ensure_ascii=False,
            indent=2,
        ))

        for item in layers["l1"]:
            print(f"[L1] {item['content']}")

        for item in layers["l2"]:
            print(f"[L2] {item['content']}")

        for item in layers["l3"]:
            print(
                f"[L3][{item['status']}] "
                f"{item['subject']} / "
                f"{item['attribute']} = "
                f"{item['value']}"
            )

    finally:
        await memory.close()

    print("\n测试完成。")


if __name__ == "__main__":
    asyncio.run(main())
