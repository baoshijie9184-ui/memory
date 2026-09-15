# DesayMem_mem0 测试操作手册

> 面向同事的实操文档，分三部分：**一、现有接口清单** → **二、本地测试（调服务器上部署的记忆后端 HTTP 接口）** → **三、服务器上操作（HTTP 接口 / 纯 Python 代码两种方式）**。
> 配套：[README.md](../README.md)（接口总览）、[architecture-analysis.md](architecture-analysis.md)（架构）
> 最后更新：2026-09-08

---

## 一、现有接口清单

记忆后端（FastAPI）部署在服务器 **10.133.72.161:20144**，本地和服务器上的测试都是调这些接口。

### 1.1 全部 HTTP 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `POST` | `/v1/memories` | **写入对话并抽取记忆（add）**：自动完成 L1 抽取 → L2 episode → L3 画像信念演化 |
| `POST` | `/v1/memories/search` | **语义检索（search）**：混合检索 + rerank，返回附带 profile |
| `GET` | `/v1/users/{user_id}/memories` | 列出用户记忆（支持 vehicle_id/occupant_id/session_id/scene/source/memory_type 过滤） |
| `GET` | `/v1/users/{user_id}/profile` | 当前画像（参数：`include_superseded`、`occupant_id`、`limit`） |
| `GET` | `/v1/users/{user_id}/memories/{memory_id}/history` | 单条记忆变更历史 |
| `GET` | `/v1/users/{user_id}/memory-layers` | L1/L2/L3 三层记忆观测查询（参数：`occupant_id`、`include_inactive`、`l1/l2/l3_limit`） |
| `GET` | `/v1/users/{user_id}/memory-events` | 记忆演化审计事件（参数：`layer`、`event`、`start/end_time`、`cursor`、`limit`） |
| `DELETE` | `/v1/users/{user_id}/memories/{memory_id}` | 删除单条记忆 |
| `DELETE` | `/v1/users/{user_id}/memories?confirm=true` | **清空用户全部记忆（含审计数据），必须带 confirm=true** |

Swagger 交互文档：**http://10.133.72.161:20144/docs**（浏览器直接打开，可在线调试全部接口）

### 1.2 核心接口的请求/响应格式

**POST /v1/memories（add）** 请求：

```json
{
  "tenant_id": "test_tenant",
  "user_id": "colleague_test_001",
  "vehicle_id": "veh_test",
  "occupant_id": "primary",
  "session_id": "sess_http_001",
  "scene": "driving",
  "source": "conversation",
  "messages": [
    {"role": "user", "content": "有点热，把空调调到22度，再放点爵士乐。"},
    {"role": "assistant", "content": "好的，已将空调调到22度，并开始播放爵士乐。"}
  ],
  "metadata": {"occurred_at": "2026-09-08T10:00:00+08:00"},
  "infer": true
}
```

响应要点：`memories[]`（本次抽取的 L1 事实）、`skipped_duplicates`、`extracted`、`episode`（L2）、`beliefs_applied`（L3 变更条数）。

**POST /v1/memories/search** 请求：

```json
{
  "tenant_id": "test_tenant",
  "user_id": "colleague_test_001",
  "query": "空调温度设置多少度",
  "top_k": 5,
  "filters": {"vehicle_id": "veh_test", "occupant_id": "primary"}
}
```

> 注意：vehicle_id / occupant_id 等范围字段放 **`filters`** 里，不是顶层参数。

响应要点：`memories[]`（含 score）、`profile`（当前画像）、`query`、`top_k`。

### 1.3 add 一次会触发什么（预期行为）

```
add 请求
  → L1 ADD: 从对话抽取原子事实（不可变追加）
  → L2 UPDATE / COMPLETE+ADD: 判断续写当前 episode 还是关旧开新
  → L3 CREATE / CONFIRM / SUPERSEDE / COEXIST: 蒸馏画像信念、处理偏好变更
  → 全过程写入 memory_audit_events 审计表
```

一次 add 包含 3 次串行 LLM 调用（抽取→episode→蒸馏），**耗时 10~30 秒属正常**，测试时把 HTTP 超时设 ≥120s。详见 [llm-call-analysis.md](llm-call-analysis.md)。

### 1.4 服务器上的服务与数据分工

| 端口 | 服务 | 说明 |
|---|---|---|
| 20140 | vLLM（Qwen3-32B，模型名 `memory-llm`） | LLM 服务，api-key `boluoboluomi` |
| 20141 | BGE-M3 Embedding（1024 维，模型名 `bge-m3`） | 向量服务，api-key `boluoboluomi` |
| 20142 | DesayMem API 旧实例（`desaymem` 库） | 车机前端旧链路，**不要动** |
| 20143 | PostgreSQL（仅监听 127.0.0.1） | 数据库，trust 认证 |
| 20144 | DesayMem API benchmark 实例（`bench_desaymem` 库） | **本文所有测试的目标接口** |

数据库分工（20143 内）：

| 数据库 | 用途 | 注意 |
|---|---|---|
| `bench_desaymem` | 20144 实例在用，cockpit 前端也在用 | 测试可以写（见 §2.1 说明），但别 truncate |
| `bench_desaymem_test` | 隔离测试库 | 纯 Python 方式（§3.3）用它 |
| `desaymem` | 20142 旧实例在用 | **不要动** |

---

## 二、本地测试（调服务器上的记忆后端接口）

本地不需要装任何数据库、模型和代码依赖——记忆后端已部署在服务器上，**本地测试 = 从你的电脑向 `http://10.133.72.161:20144` 发 HTTP 请求**。

### 2.0 网络前提

本地机器需能访问 `10.133.72.161:20144`（服务器防火墙/安全组需放行该端口给办公网段）。验证：

```bash
# Windows PowerShell / macOS / Linux 通用
curl http://10.133.72.161:20144/health
# 预期返回: {"status":"ok","app":"DesayMem_benchmark","database":"ok",...}
```

### 2.1 最快的测试方式：Swagger 在线调试

浏览器打开 **http://10.133.72.161:20144/docs** → 点任意接口 `Try it out` → 填参数 → Execute。适合不写代码的同事快速验证。

> 注意：20144 连的是 `bench_desaymem` 库，cockpit 前端也在用。测试请**用独立的 tenant_id / user_id**（如 `test_tenant` / `你的名字_test`），测完用 DELETE 接口清掉，不影响前端数据。

### 2.2 curl / PowerShell 逐条测试

**add（写入并抽取）：**

```bash
curl -X POST http://10.133.72.161:20144/v1/memories \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test_tenant",
    "user_id": "colleague_test_001",
    "vehicle_id": "veh_test",
    "occupant_id": "primary",
    "session_id": "sess_http_001",
    "scene": "driving",
    "source": "conversation",
    "messages": [
      {"role": "user", "content": "有点热，把空调调到22度，再放点爵士乐。"},
      {"role": "assistant", "content": "好的，已将空调调到22度，并开始播放爵士乐。"}
    ],
    "metadata": {"occurred_at": "2026-09-08T10:00:00+08:00"},
    "infer": true
  }' --max-time 120
```

Windows PowerShell 版（curl 是别名，用 Invoke-RestMethod）：

```powershell
$body = @{
  tenant_id = "test_tenant"; user_id = "colleague_test_001"
  vehicle_id = "veh_test"; session_id = "sess_http_001"; scene = "driving"
  messages = @(
    @{role="user"; content="有点热，把空调调到22度，再放点爵士乐。"},
    @{role="assistant"; content="好的，已将空调调到22度，并开始播放爵士乐。"}
  )
  metadata = @{occurred_at = "2026-09-08T10:00:00+08:00"}
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "http://10.133.72.161:20144/v1/memories" `
  -Method Post -ContentType "application/json" -Body $body -TimeoutSec 120
```

**search（检索）：**

```bash
curl -X POST http://10.133.72.161:20144/v1/memories/search \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test_tenant",
    "user_id": "colleague_test_001",
    "query": "空调温度设置多少度",
    "top_k": 5,
    "filters": {"vehicle_id": "veh_test", "occupant_id": "primary"}
  }' --max-time 60
```

**观测 L1/L2/L3 演化结果（GET，浏览器直接开）：**

```
http://10.133.72.161:20144/v1/users/colleague_test_001/memory-layers?tenant_id=test_tenant&include_inactive=true
http://10.133.72.161:20144/v1/users/colleague_test_001/memory-events?tenant_id=test_tenant&limit=50
http://10.133.72.161:20144/v1/users/colleague_test_001/profile?tenant_id=test_tenant
```

**清理测试数据：**

```bash
curl -X DELETE "http://10.133.72.161:20144/v1/users/colleague_test_001/memories?tenant_id=test_tenant&confirm=true"
```

### 2.3 用 Python 写测试脚本（本地只需标准库）

不需要装 desaymem 包，用 requests/httpx 或纯标准库都行：

```python
"""本地测试脚本：调服务器 20144 记忆后端接口"""
import json, urllib.request

BASE = "http://10.133.72.161:20144"
TENANT, USER = "test_tenant", "colleague_test_001"

def post(path, payload, timeout=120):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read())

# 1. add：一条对话写入，触发 L1/L2/L3 演化（耗时 10~30s 正常）
r = post("/v1/memories", {
    "tenant_id": TENANT, "user_id": USER,
    "vehicle_id": "veh_test", "session_id": "s1", "scene": "driving",
    "messages": [
        {"role": "user", "content": "有点热，把空调调到22度，再放点爵士乐。"},
        {"role": "assistant", "content": "好的，已将空调调到22度，并开始播放爵士乐。"},
    ],
})
print("add 结果:", [m.get("memory") or m.get("content") for m in r["memories"]])
print("episode:", r.get("episode", {}).get("memory", "")[:50])
print("beliefs_applied:", r.get("beliefs_applied"))

# 2. 偏好变更（触发 L3 SUPERSEDE）
post("/v1/memories", {
    "tenant_id": TENANT, "user_id": USER, "session_id": "s2", "scene": "driving",
    "messages": [
        {"role": "user", "content": "以后空调温度改为26度吧，22度太冷了。"},
        {"role": "assistant", "content": "好的，已将空调默认温度调整为26度。"},
    ],
})

# 3. search
r = post("/v1/memories/search", {
    "tenant_id": TENANT, "user_id": USER,
    "query": "空调温度设置多少度", "top_k": 5,
    "filters": {"vehicle_id": "veh_test"},
}, timeout=60)
for i, m in enumerate(r["memories"], 1):
    print(f"Top{i} {m.get('score', 0):.4f} | {m.get('memory') or m.get('content')}")

# 4. 观测三层
layers = get(f"/v1/users/{USER}/memory-layers?tenant_id={TENANT}&include_inactive=true")
print("L1:", len(layers["l1"]), "L2:", len(layers["l2"]), "L3:", len(layers["l3"]))
for b in layers["l3"]:
    print(f"[L3][{b['status']}] {b['subject']} / {b['attribute']} = {b['value']}")

# 5. 清理
req = urllib.request.Request(
    f"{BASE}/v1/users/{USER}/memories?tenant_id={TENANT}&confirm=true", method="DELETE")
urllib.request.urlopen(req, timeout=60)
print("已清理")
```

### 2.4 用前端页面测试（可选）

服务器上还部署了 cockpit 调试前端（`cockpit-frontend`），支持对话式 add/search + 三层观测面板，找部署同事要访问地址即可，交互逻辑与上述接口一致。

---

## 三、服务器上操作

先登录服务器：

```bash
ssh <你的账号>@10.133.72.161
```

服务器上操作分两种：**方式 A 调 HTTP 接口**（与本地测试同一套，只是地址换成 127.0.0.1）；**方式 B 纯 Python 代码直连**（不走 HTTP，直接调 DesayMemory 类，可指定隔离测试库）。

### 3.1 方式 A：HTTP 接口测试

接口清单、请求格式与 §1、§2 完全一致，只是地址用 `http://127.0.0.1:20144`（服务器本机）。服务器上 curl 不可用时可参考 §2.3 的 Python 写法。

```bash
# 健康检查
python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:20144/health',timeout=5).read().decode())"

# Swagger
http://127.0.0.1:20144/docs
```

**服务器上特有的操作——独立测试实例（推荐）：**

不想和 cockpit 前端共用 `bench_desaymem` 库时，用隔离库 `bench_desaymem_test` 另起一个端口 20145：

```bash
cd /data/pengshuang/memory-benchmark/systems/DesayMem_mem0

POSTGRES_DSN="postgresql://desaymem@127.0.0.1:20143/bench_desaymem_test" \
HISTORY_DB_PATH="/data/pengshuang/memory-benchmark/data/desaymem/history_test.db" \
/data/pengshuang/memory-benchmark/envs/desaymem/bin/python -m uvicorn desaymem.api.main:app \
  --host 0.0.0.0 --port 20145 > /tmp/desaymem_20145.log 2>&1 &

# 验证后，所有测试请求打 http://127.0.0.1:20145
python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:20145/health',timeout=5).read().decode())"
```

之后 §1 / §2 的命令把 20144 换成 20145 即可。用完 `kill %1`。

### 3.2 服务器环境速查

| 项 | 路径 |
|---|---|
| 代码与脚本 | `/data/pengshuang/memory-benchmark/systems/DesayMem_mem0` |
| Python 环境 | `/data/pengshuang/memory-benchmark/envs/desaymem/bin/python` |
| 20144 API 日志 | `/tmp/desaymem_20144.log` |
| benchmark .env | `/data/pengshuang/memory-benchmark/systems/DesayMem_mem0/.env` |

### 3.3 方式 B：纯 Python 代码直连（不走 HTTP）

直接调 `DesayMemory` 类做 add/search，可完全控制用哪个数据库。**现成脚本：**

```bash
cd /data/pengshuang/memory-benchmark/systems/DesayMem_mem0

# 端到端测试：连隔离库 bench_desaymem_test（脚本内置安全检查，连错库直接退出）
/data/pengshuang/memory-benchmark/envs/desaymem/bin/python scripts/test_direct_memory.py
```

该脚本内置 3 组测试对话（22度→26度→音乐偏好变更），依次输出写入阶段（L1 事实）、搜索阶段（3 条查询 Top-5）、L1/L2/L3 最终状态（可看到 L3 SUPERSEDE 效果）。脚本开头强制设置了测试库 DSN 和独立 SQLite，不受 .env 影响。

**自己写 Python 用例时照这个模板：**

```python
#!/usr/bin/env python3
"""服务器上的自定义测试：纯 Python 直连记忆后端"""
import asyncio, json, os, sys

# ① 关键：环境变量必须在 import desaymem 之前设置
#    （get_settings() 有 @lru_cache，先 import 就读不到覆盖值）
os.environ["POSTGRES_DSN"] = "postgresql://desaymem@127.0.0.1:20143/bench_desaymem_test"
os.environ["HISTORY_DB_PATH"] = "/data/pengshuang/memory-benchmark/data/desaymem/history_test.db"

sys.path.insert(0, "/data/pengshuang/memory-benchmark/systems/DesayMem_mem0/src")

from desaymem.core.config import Settings
from desaymem.core.memory import DesayMemory

TENANT, USER = "test_tenant", "my_test_001"

async def main():
    memory = DesayMemory.from_settings(Settings())
    await memory.prepare()
    print("数据库:", settings.postgres_dsn.rsplit("@", 1)[-1])  # 确认没连错库

    # add：三层自动演化
    added = await memory.add(
        [
            {"role": "user", "content": "以后空调默认26度，22度太冷了。"},
            {"role": "assistant", "content": "好的，已将默认温度调整为26度。"},
        ],
        user_id=USER, tenant_id=TENANT,
        session_id="s1", scene="driving", source="python_benchmark",
    )
    print([m["content"] for m in added])

    # search：vehicle_id/occupant_id 等放 filters，不是顶层参数
    hits = await memory.search(
        "空调温度多少度", user_id=USER, tenant_id=TENANT,
        filters={"occupant_id": "primary"}, top_k=5,
    )
    for h in hits:
        print(h["content"], h.get("score"))

    # 用完清掉
    await memory.delete_all(USER, tenant_id=TENANT)
    await memory.close()

asyncio.run(main())
```

运行：`/data/pengshuang/memory-benchmark/envs/desaymem/bin/python my_test.py`

### 3.4 服务器上直接看数据库

```bash
# 方式一：inspect 脚本（按 tenant/user 查已存记忆）
/data/pengshuang/memory-benchmark/envs/desaymem/bin/python scripts/inspect_memories.py \
  --tenant_id test_tenant --user_id colleague_test_001

# 方式二：psycopg 直查（注意切换库名）
python3 - <<'EOF'
import psycopg
conn = psycopg.connect("postgresql://desaymem@127.0.0.1:20143/bench_desaymem_test")
cur = conn.cursor()
cur.execute("SELECT memory_type, content, created_at FROM memory_items ORDER BY created_at DESC LIMIT 10")
for r in cur.fetchall(): print(r)
conn.close()
EOF
```

---

## 四、数据隔离与清理

**清理测试数据：**

| 方式 | 命令 | 效果 |
|---|---|---|
| HTTP 清单用户 | `DELETE /v1/users/{uid}/memories?tenant_id=xxx&confirm=true` | 删 L1/L2/L3 + 审计 + 实体，**推荐** |
| 脚本内置 | `test_direct_memory.py` 开头自动 `delete_all` | 每轮从干净状态开始 |
| SQL truncate | `TRUNCATE memory_items, profile_beliefs, user_profile_snapshots, memory_audit_events, memory_entities CASCADE` | 只整库重置时用 |

**新建全新测试库（各自独立、互不影响）：**

```bash
python3 - <<'EOF'
import psycopg
conn = psycopg.connect("postgresql://desaymem@127.0.0.1:20143/postgres")
conn.autocommit = True
conn.execute("CREATE DATABASE bench_desaymem_test_v2")   # 换成你的名字
conn.close()
EOF

# 指向新库跑迁移（001~006 全部 IF NOT EXISTS，可重复执行）
POSTGRES_DSN="postgresql://desaymem@127.0.0.1:20143/bench_desaymem_test_v2" \
/data/pengshuang/memory-benchmark/envs/desaymem/bin/python -m desaymem.cli apply
```

**红线**：
- 不要对 `bench_desaymem` 和 `desaymem` 库 truncate——20144 实例/前端和 20142 旧实例在用
- 走 20144 测试时用独立的 tenant_id/user_id（如 `test_tenant`/`你的名字_test`），测完 DELETE 清理
- 纯 Python 方式优先连 `bench_desaymem_test`

---

## 五、常见问题（FAQ）

| 现象 | 原因与解决 |
|---|---|
| 本地连不上 10.133.72.161:20144 | 防火墙/安全组没放行该端口给办公网段，找服务器管理员开通 |
| add 超时 | add 含 3 次串行 LLM 调用，10~30s 正常，HTTP 客户端超时设 ≥120s |
| `search() got an unexpected keyword argument 'vehicle_id'`（纯 Python 方式） | scope 类参数（vehicle_id/occupant_id/session_id/scene/source）放 `filters` dict，不是函数参数 |
| LLM/embedding 401 | api-key 都是 `boluoboluomi` |
| 写入成功但 search 搜不到 | 检查 search 的 tenant_id/user_id 与 add 时一致；语义阈值 `SEARCH_THRESHOLD=0.1` 可在 .env 调 |
| 环境变量改了不生效（纯 Python 方式） | `get_settings()` 带 `@lru_cache`，必须在 import desaymem **之前** `os.environ[...]` |
| `relation "memory_items" does not exist` | 该库没跑过迁移：`python -m desaymem.cli apply` |
| 20144 接口测试后想撤销 | `DELETE /v1/users/{uid}/memories?tenant_id=xxx&confirm=true`，或起 20145 独立实例 |
| embedding 维度报错 | 必须 `EMBEDDING_DIMS=1024`（BGE-M3），改维度还要同步改 migration 001 的 `VECTOR(n)` |

---

## 六、一页速查

```
接口清单:   http://10.133.72.161:20144/docs        ← Swagger，先看这个

本地测试:   curl http://10.133.72.161:20144/health
           （POST add/search → GET memory-layers/memory-events → DELETE 清理）
           本地零依赖，只需网络可达 20144

服务器测试:
  HTTP 方式:  地址换成 http://127.0.0.1:20144
             想隔离数据 → 起 20145 独立实例（连 bench_desaymem_test）
  Python 方式: /data/pengshuang/memory-benchmark/envs/desaymem/bin/python \
             scripts/test_direct_memory.py
             （直连 bench_desaymem_test，不走 HTTP）

不碰的库: bench_desaymem / desaymem
```
