# DesayMem 纯 Python 评测环境搭建与测试指南

## 1. 文档目的

本文档用于在现有 `memory-benchmark` 环境中，为 `DesayMem_mem0` 搭建一套独立的纯 Python 评测环境。

该环境不启动新的 HTTP 服务，不占用正在运行的 Benchmark 后端端口 `20144`，并通过独立 PostgreSQL 数据库和独立 SQLite 历史文件，避免污染前端联调数据。

本文档对应的服务器目录与服务如下：

```text
/data/pengshuang/memory-benchmark/
├── envs/desaymem/                         # DesayMem Python虚拟环境
├── systems/DesayMem_mem0/                # 被测记忆系统源码
└── data/desaymem/
    ├── history.db                        # 现有Benchmark后端使用
    └── history_test.db                   # 纯Python测试使用
```

## 2. 隔离方案

### 2.1 PostgreSQL隔离

使用同一个 PostgreSQL 服务，但采用不同数据库：

```text
PostgreSQL：127.0.0.1:20143
├── desaymem               # 其他后端或生产环境
├── bench_desaymem         # 当前Benchmark后端和前端联调
└── bench_desaymem_test    # 纯Python脚本测试专用
```

不需要重新部署一个 PostgreSQL 实例。数据库级隔离已经可以防止纯 Python 测试数据写入 `bench_desaymem`。

### 2.2 SQLite隔离

DesayMem 除 PostgreSQL/pgvector 外，还使用 SQLite 保存历史记录和最近消息，因此必须同时隔离 SQLite 文件：

```text
当前Benchmark后端：
/data/pengshuang/memory-benchmark/data/desaymem/history.db

纯Python测试：
/data/pengshuang/memory-benchmark/data/desaymem/history_test.db
```

只更换 PostgreSQL 数据库而不更换 `HISTORY_DB_PATH`，仍可能造成测试会话和在线后端会话混用。

### 2.3 服务端口关系

| 资源 | 当前Benchmark后端 | 纯Python测试 |
| --- | --- | --- |
| HTTP API | `20144` | 不启动HTTP服务 |
| PostgreSQL | `127.0.0.1:20143/bench_desaymem` | `127.0.0.1:20143/bench_desaymem_test` |
| SQLite | `history.db` | `history_test.db` |
| LLM | `127.0.0.1:20140/v1` | 共用 |
| Embedding | `127.0.0.1:20141/v1` | 共用 |

LLM和Embedding是无状态推理接口，可以共用，不会造成记忆数据串库。不过同时大量请求可能造成推理排队、延迟上升或并发限制，因此性能评测应尽量避开在线高峰。

## 3. 已验证环境

当前环境已经验证：

```text
PostgreSQL数据库：bench_desaymem_test
SQLite文件：/data/pengshuang/memory-benchmark/data/desaymem/history_test.db
LLM模型：memory-llm
LLM地址：http://127.0.0.1:20140/v1
Embedding模型：bge-m3
Embedding地址：http://127.0.0.1:20141/v1
Embedding维度：1024
```

数据库已经成功执行：

```text
001_initial.sql
002_session_entities.sql
003_bm25.sql
004_layers.sql
005_episode_integrity.sql
006_memory_observability.sql
```

并通过：

```text
Schema check passed
```

## 4. 创建测试数据库

### 4.1 为什么不直接使用psql或docker

当前登录环境中没有安装 `psql` 和 `docker` 命令，但项目虚拟环境已经安装 `psycopg`，因此可以直接用 Python 创建数据库。

激活环境：

```bash
source /data/pengshuang/memory-benchmark/envs/desaymem/bin/activate
```

确认 `psycopg`：

```bash
python -c "import psycopg; print(psycopg.__version__)"
```

### 4.2 创建bench_desaymem_test

```bash
python - <<'PY'
import psycopg

admin_dsn = "postgresql://desaymem@127.0.0.1:20143/postgres"
database_name = "bench_desaymem_test"

with psycopg.connect(admin_dsn, autocommit=True) as conn:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (database_name,),
        )
        if cursor.fetchone():
            print(f"数据库已经存在：{database_name}")
        else:
            cursor.execute(
                'CREATE DATABASE "bench_desaymem_test" OWNER desaymem'
            )
            print(f"数据库创建成功：{database_name}")
PY
```

若 PostgreSQL 配置了密码，将DSN改为：

```text
postgresql://desaymem:密码@127.0.0.1:20143/postgres
```

## 5. 执行数据库迁移

进入项目目录：

```bash
cd /data/pengshuang/memory-benchmark/systems/DesayMem_mem0
```

执行全部迁移：

```bash
POSTGRES_DSN=postgresql://desaymem@127.0.0.1:20143/bench_desaymem_test \
desaymem-migrate apply
```

检查数据库结构：

```bash
POSTGRES_DSN=postgresql://desaymem@127.0.0.1:20143/bench_desaymem_test \
desaymem-migrate check
```

预期结果：

```text
Schema check passed
```

注意，命令必须包含 `apply` 或 `check`。单独运行 `desaymem-migrate` 会因为缺少操作参数而失败。

## 6. 创建独立SQLite目录

```bash
mkdir -p /data/pengshuang/memory-benchmark/data/desaymem
```

不必手动创建 `history_test.db`，测试程序首次连接时会自动创建。

## 7. 配置加载机制

`Settings` 使用 `pydantic-settings`：

```python
settings = Settings()
```

配置优先级中，进程环境变量高于项目 `.env`，所以不需要修改正在运行的后端使用的 `.env`。

需要覆盖：

```text
POSTGRES_DSN
HISTORY_DB_PATH
```

其他配置继续从项目 `.env` 读取：

```text
LLM_MODEL
LLM_API_KEY
LLM_BASE_URL
EMBEDDING_MODEL
EMBEDDING_API_KEY
EMBEDDING_BASE_URL
EMBEDDING_DIMS
```

环境变量必须在 `Settings()` 实例化之前设置。如果使用带 `@lru_cache` 的 `get_settings()`，还要保证它此前没有缓存旧配置。本测试直接实例化 `Settings()`，因此更加明确。

## 8. 配置安全检查

运行测试前执行：

```bash
POSTGRES_DSN=postgresql://desaymem@127.0.0.1:20143/bench_desaymem_test \
HISTORY_DB_PATH=/data/pengshuang/memory-benchmark/data/desaymem/history_test.db \
python - <<'PY'
from desaymem.core.config import Settings

settings = Settings()

print("数据库：", settings.postgres_dsn)
print("SQLite：", settings.history_db_path)
print("LLM：", settings.llm_model)
print("LLM地址：", settings.llm_base_url)
print("Embedding：", settings.embedding_model)
print("Embedding地址：", settings.embedding_base_url)
print("Embedding维度：", settings.embedding_dims)
PY
```

必须确认：

```text
数据库名称是 bench_desaymem_test
SQLite名称是 history_test.db
```

如果仍显示 `bench_desaymem` 或 `history.db`，不要运行写入测试。

## 9. 纯Python端到端冒烟脚本

在项目中创建：

```text
scripts/test_direct_memory.py
```

脚本内容：

```python
#!/usr/bin/env python3
"""纯 Python 端到端测试，不启动HTTP服务。"""

import asyncio
import json
import os
import sys

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
            {"role": "user", "content": "有点热，把空调调到22度，再放点爵士乐。"},
            {"role": "assistant", "content": "好的，已将空调调到22度，并开始播放爵士乐。"},
        ],
        "session_id": "sess_test_001",
        "scene": "driving",
        "occurred_at": "2026-08-01T08:00:00+08:00",
    },
    {
        "messages": [
            {"role": "user", "content": "以后空调温度改为26度吧，22度太冷了。"},
            {"role": "assistant", "content": "好的，已将空调默认温度调整为26度。"},
        ],
        "session_id": "sess_test_002",
        "scene": "driving",
        "occurred_at": "2026-09-01T08:00:00+08:00",
    },
    {
        "messages": [
            {"role": "user", "content": "我喜欢听舒缓的古典音乐，不要爵士了。"},
            {"role": "assistant", "content": "好的，已切换为古典音乐。"},
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
        print("后端信息:", json.dumps(memory.backend_info(), ensure_ascii=False, indent=2))

        # 只清理指定测试用户，不删除数据库，不影响其他用户。
        deleted = await memory.delete_all(USER_ID, tenant_id=TENANT_ID)
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
                vehicle_id=VEHICLE_ID,
                occupant_id="primary",
                top_k=5,
            )

            print(f"\n查询：{query}")
            for rank, hit in enumerate(hits, 1):
                score = hit.get("score") or 0
                occurred_at = (hit.get("metadata") or {}).get("occurred_at")
                print(
                    f"Top{rank} score={score:.4f} "
                    f"occurred_at={occurred_at} | {hit['content']}"
                )

        print("\n=== L1/L2/L3 状态 ===")
        scope = MemoryScope(
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            vehicle_id=VEHICLE_ID,
            occupant_id="primary",
        )
        layers = await memory.get_memory_layers(scope)

        print(json.dumps(layers["stats"], ensure_ascii=False, indent=2))
        for item in layers["l1"]:
            print(f"[L1] {item['content']}")
        for item in layers["l2"]:
            print(f"[L2] {item['content']}")
        for item in layers["l3"]:
            print(
                f"[L3][{item['status']}] "
                f"{item['subject']} / {item['attribute']} = {item['value']}"
            )
    finally:
        await memory.close()

    print("\n测试完成。")


if __name__ == "__main__":
    asyncio.run(main())
```

## 10. 运行冒烟测试

```bash
source /data/pengshuang/memory-benchmark/envs/desaymem/bin/activate
cd /data/pengshuang/memory-benchmark/systems/DesayMem_mem0
python scripts/test_direct_memory.py
```

该脚本直接调用：

```text
Settings
  → DesayMemory.from_settings
  → memory.prepare
  → memory.add
  → memory.search
  → memory.get_memory_layers
  → memory.close
```

不会启动 Uvicorn，也不会占用 `20144`。

## 11. 预期结果与判断标准

### 11.1 配置检查

应显示：

```text
数据库：127.0.0.1:20143/bench_desaymem_test
SQLite：.../history_test.db
```

### 11.2 写入结果

三轮对话应完成记忆提取并写入。由于 `infer=True`，实际新增记忆数由LLM提取结果决定，不一定与消息条数相同。

### 11.3 检索结果

重点检查：

- 查询当前温度偏好时，应优先返回26度；
- 不应将旧的22度作为当前有效偏好排在首位；
- 查询音乐偏好时，应优先返回古典音乐；
- 不应将爵士乐作为当前有效偏好；
- 返回结果中应保留对应 `occurred_at`。

### 11.4 分层状态

`get_memory_layers()` 返回：

- `l1`：原子语义记忆；
- `l2`：情景记忆；
- `l3`：画像信念，包括active和superseded状态；
- `stats`：各或其他分层数量统计。

只有3轮输入时，L2或L3为空不一定表示程序错误。它们是否形成取决于代码触发条件、LLM判断和证据数量。本脚本的首要目标是验证 `add → 提取 → 存储 → search` 链路。

## 12. 数据清理机制

### 12.1 当前脚本的行为

脚本开始后执行：

```python
await memory.delete_all(USER_ID, tenant_id=TENANT_ID)
```

它只删除：

```text
tenant_id = test_tenant
user_id = test_user_001
```

对应的数据，包括：

- L1原子记忆；
- L2情景记忆；
- L3画像信念；
- 实体关联；
- SQLite中的该用户消息；
- 该用户的审计事件。

它不会：

- 删除 `bench_desaymem_test` 数据库；
- 删除数据库表；
- 删除其他测试用户；
- 删除 `bench_desaymem` 数据；
- 删除 `history_test.db` 文件。

### 12.2 执行结束后的行为

脚本结束时只执行：

```python
await memory.close()
```

该操作仅关闭连接，不删除本轮新写入的数据。因此当前测试流程是：

```text
运行脚本
  → 清理该测试用户的上轮数据
  → 写入本轮数据
  → 检索和查看分层结果
  → 关闭连接
  → 保留本轮数据
```

推荐保持“执行前清理、执行后保留”，便于检查数据库和继续执行检索。

### 12.3 执行后自动清理（可选）

如果明确不需要保留结果，可以改为：

```python
finally:
    deleted = await memory.delete_all(USER_ID, tenant_id=TENANT_ID)
    print(f"实验结束清理：删除 {deleted} 条记忆")
    await memory.close()
```

## 13. 使用现有全年数据集脚本

项目已有：

```text
scripts/run_yearlong_memory_test.py
```

### 13.1 数据集格式校验

```bash
POSTGRES_DSN=postgresql://desaymem@127.0.0.1:20143/bench_desaymem_test \
HISTORY_DB_PATH=/data/pengshuang/memory-benchmark/data/desaymem/history_test.db \
python scripts/run_yearlong_memory_test.py --mode dry-run --profile core
```

`dry-run` 不连接模型和数据库，不写入任何数据。

### 13.2 少量冒烟测试

```bash
POSTGRES_DSN=postgresql://desaymem@127.0.0.1:20143/bench_desaymem_test \
HISTORY_DB_PATH=/data/pengshuang/memory-benchmark/data/desaymem/history_test.db \
python scripts/run_yearlong_memory_test.py \
  --mode all \
  --profile core \
  --limit 2 \
  --reset \
  --top-k 10
```

### 13.3 核心模糊记忆场景

```bash
POSTGRES_DSN=postgresql://desaymem@127.0.0.1:20143/bench_desaymem_test \
HISTORY_DB_PATH=/data/pengshuang/memory-benchmark/data/desaymem/history_test.db \
python scripts/run_yearlong_memory_test.py \
  --mode all \
  --profile core \
  --reset \
  --top-k 10
```

### 13.4 全年数据集

```bash
POSTGRES_DSN=postgresql://desaymem@127.0.0.1:20143/bench_desaymem_test \
HISTORY_DB_PATH=/data/pengshuang/memory-benchmark/data/desaymem/history_test.db \
python scripts/run_yearlong_memory_test.py \
  --mode all \
  --profile full \
  --reset \
  --top-k 10
```

## 14. 彻底重建测试数据库

只有以下情况建议彻底重建：

- 数据库Schema发生变化；
- migration执行异常；
- 需要验证全新空库初始化；
- 怀疑存在无法通过 `delete_all` 清除的残留数据。

由于当前环境没有 `psql`，可以使用 Python：

```bash
python - <<'PY'
import psycopg

admin_dsn = "postgresql://desaymem@127.0.0.1:20143/postgres"

with psycopg.connect(admin_dsn, autocommit=True) as conn:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = 'bench_desaymem_test'
              AND pid <> pg_backend_pid()
            """
        )
        cursor.execute('DROP DATABASE IF EXISTS "bench_desaymem_test"')
        cursor.execute('CREATE DATABASE "bench_desaymem_test" OWNER desaymem')

print("bench_desaymem_test重建完成")
PY
```

重新执行迁移：

```bash
POSTGRES_DSN=postgresql://desaymem@127.0.0.1:20143/bench_desaymem_test \
desaymem-migrate apply
```

清理独立SQLite文件：

```bash
rm -f /data/pengshuang/memory-benchmark/data/desaymem/history_test.db
```

删除数据库和SQLite文件是破坏性操作。执行前必须再次确认目标名称是 `bench_desaymem_test` 和 `history_test.db`，并确保测试脚本已经退出。

## 15. 常见问题

### 15.1 `psql: command not found`

当前环境未安装PostgreSQL客户端。使用本文的 `psycopg` Python方式创建或管理数据库即可。

### 15.2 `docker: command not found`

当前登录环境位于业务容器内，没有Docker CLI。无需使用 `docker exec`，直接连接 `127.0.0.1:20143`。

### 15.3 `Failed to connect to PostgreSQL`

检查：

- `127.0.0.1:20143` 是否可达；
- 数据库名称是否正确；
- 用户名和密码是否正确；
- PostgreSQL是否允许当前来源连接。

### 15.4 `memory_items table is missing`

表示创建了数据库但没有执行migration：

```bash
POSTGRES_DSN=postgresql://desaymem@127.0.0.1:20143/bench_desaymem_test \
desaymem-migrate apply
```

### 15.5 Embedding维度不一致

数据库向量字段与模型输出必须一致。当前配置为：

```text
EMBEDDING_DIMS=1024
```

更换Embedding模型时，应确认模型输出维度，并根据需要重新建立测试库及迁移。

### 15.6 L2或L3没有数据

少量冒烟数据可能不足以触发情景聚合或画像蒸馏。先确认：

- `ENABLE_EPISODES=true`；
- `ENABLE_PROFILE=true`；
- LLM返回格式正常；
- 输入证据数量满足触发条件；
- 使用的是 `get_memory_layers()`，而不是仅用 `get_all()` 查看分层。

### 15.7 搜索仍返回旧偏好

检查：

- 数据是否显式携带绝对时间 `occurred_at`；
- 新偏好是否使用清晰的更新表达，如“以后改为26度”；
- L3中旧信念是否变为 `superseded`；
- 检索是否优先返回最新active偏好；
- 是否误连了 `bench_desaymem` 或旧SQLite文件。

## 16. 后续正式评测建议

冒烟测试通过后，正式评测数据应至少包含：

- `dataset_id`：数据集版本；
- `run_id`：本轮实验编号；
- `tenant_id`：评测租户；
- `user_id`：模拟用户；
- `session_id`：会话编号；
- `occurred_at`：事件真实发生时间；
- `expected_memory_ids`：应召回的证据；
- `expected_answer`：期望答案；
- `test_type`：事实、偏好、时序、冲突、模糊指令或用户隔离。

建议不同算法或模型使用不同的 `user_id/run_id`，避免必须反复覆盖已有实验：

```text
test_vehicle_v1_qwen32b_run001
test_vehicle_v1_qwen72b_run001
test_vehicle_v1_reranker_v2_run001
```

输出结果应记录：

- 写入成功率；
- 实际新增记忆数；
- Top-K原始结果；
- Recall@K；
- MRR；
- Top-1准确率；
- 时序选择准确率；
- 冲突更新准确率；
- 模糊指令成功率；
- 用户隔离准确率；
- 写入和检索时延；
- 模型及参数配置。

## 17. 最终结论

当前测试环境采用：

```text
PostgreSQL：bench_desaymem_test
SQLite：history_test.db
调用方式：纯Python直接调用DesayMemory
HTTP端口：无需新增
LLM/Embedding：与现有服务共享
清理策略：执行前清理指定测试用户，执行后保留本轮结果
```

该方案能够避免污染 `bench_desaymem` 中已有的前端联调数据，同时复用当前模型和PostgreSQL服务，适合作为后续车载记忆数据集、模糊指令、时序冲突和L1/L2/L3分层评测的基础环境。
