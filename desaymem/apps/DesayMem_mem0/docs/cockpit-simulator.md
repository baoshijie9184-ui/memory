# 车机多轮对话模拟器测试流程

## 概述

`scripts/cockpit_simulator.py` 模拟司机上车后的一次完整驾驶会话，通过 5 轮连续对话验证 DesayMem 的记忆抽取、存储、检索和跨轮上下文召回能力。

脚本使用真实 DashScope LLM/Embedding，记忆写入 PostgreSQL，history/last-k 写入 SQLite。

---

## 运行方式

### 前置条件

`.env` 中需配置以下凭据：

```env
LLM_MODEL=qwen3.8-flash
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

EMBEDDING_MODEL=qwen3.7-text-embedding-flash
EMBEDDING_API_KEY=sk-xxx
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_DIMS=1024
```

### 启动命令

```bash
# 直连模式（默认）—— 无需启动 API server
python scripts/cockpit_simulator.py

# HTTP 客户端模式 —— 需先启动 API server
python scripts/cockpit_simulator.py --http
python scripts/cockpit_simulator.py --http --base-url http://192.168.0.166:8000

# 自定义租户/用户/车辆/会话
python scripts/cockpit_simulator.py --tenant oem_chery --driver user_driver --vehicle vehicle_001 --session session_drive_001
```

### 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--http` | 关 | 使用 HTTP 客户端模式调 API server |
| `--base-url` | `http://127.0.0.1:8000` | API server 地址（仅 `--http` 有效） |
| `--tenant` | `oem_chery` | 租户 ID |
| `--driver` | `user_driver` | 司机用户 ID |
| `--vehicle` | `vehicle_001` | 车辆 ID |
| `--session` | `session_drive_001` | 会话 ID |

---

## 测试场景设计

### 场景：司机单次驾驶会话（5 轮）

模拟司机上车后依次说出空调偏好、导航目的地、音乐偏好、座椅加热操作，最后询问自己之前说的空调温度，验证系统是否能从已有记忆中召回。

### 5 轮对话流程

```
司机上车 (session_drive_001 开始)
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ 轮次 1: 空调偏好                                      │
│ 司机: 我开车的时候喜欢把空调调到22度                    │
│ 车机: 好的，已为您设置空调温度22度                      │
│ 动作: add(infer=True) → LLM 抽取记忆                   │
│ 验证: search("用户习惯的空调温度是多少") 命中 "22"      │
└───────────────────────┬─────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────┐
│ 轮次 2: 导航目的地                                    │
│ 司机: 导航去上海迪士尼                                 │
│ 车机: 正在为您规划前往上海迪士尼的路线                  │
│ 动作: add(infer=False) → 原文存储，不走 LLM 抽取       │
│ 验证: search("上海迪士尼") 命中 "上海迪士尼"            │
└───────────────────────┬─────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────┐
│ 轮次 3: 音乐偏好                                      │
│ 司机: 开车时帮我放周杰伦的歌                           │
│ 车机: 已为您播放周杰伦的歌单                           │
│ 动作: add(infer=True) → LLM 抽取记忆                   │
│ 验证: search("司机喜欢听什么音乐") 命中 "周杰伦"       │
└───────────────────────┬─────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────┐
│ 轮次 4: 座椅加热（过程性记忆）                         │
│ 司机: 帮我把座椅加热打开到3挡                          │
│ 车机: 座椅加热已调至3挡，请注意温度                     │
│ 动作: add(memory_type=procedural_memory)              │
│       → LLM 生成过程性摘要                             │
│ 验证: 记忆类型 == procedural_memory                    │
│       search("座椅加热") 命中 "座椅"                    │
└───────────────────────┬─────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────┐
│ 轮次 5: 跨轮上下文召回                                │
│ 司机: 我之前说的空调温度是多少度？                      │
│ 车机: 您之前说喜欢把空调调到22度                       │
│ 动作: 不写入新记忆，仅检索已有记忆                      │
│ 验证: search("用户习惯的空调温度是多少") 命中 "22"      │
│       → 证明轮次 1 的偏好已持久化并可跨轮召回           │
└───────────────────────┬─────────────────────────────┘
                        ▼
                  会话结束，输出汇总
```

---

## 验证维度

每轮对话结束后立即执行 `search` 检索，验证记忆是否正确存储并可召回。

| 轮次 | 验证维度 | 检索关键词 | 期望命中 | 说明 |
|------|----------|------------|----------|------|
| 1 | 语义抽取 | 用户习惯的空调温度是多少 | `22` | LLM 从对话中抽取偏好事实 |
| 2 | 原文存储 | 上海迪士尼 | `上海迪士尼` | `infer=False` 跳过 LLM，直接存原文 |
| 3 | 语义抽取 | 司机喜欢听什么音乐 | `周杰伦` | LLM 抽取音乐偏好 |
| 4 | 过程性记忆 | 座椅加热 | `座椅` | `memory_type=procedural_memory`，验证类型保留 |
| 5 | 跨轮召回 | 用户习惯的空调温度是多少 | `22` | 不写入新记忆，验证轮次 1 的记忆仍可召回 |

---

## 输出示例

```
模式: 直连 DesayMemory | LLM=qwen3.8-flash EMB=qwen3.7-text-embedding-flash

============================================================
  DesayMem 车机多轮对话模拟器
============================================================
  已清空用户 user_driver 的历史记忆

--- [轮次 1/5] 空调偏好 ---
  司机: 我开车的时候喜欢把空调调到22度
  车机: 好的，已为您设置空调温度22度
  记忆存储: User喜欢开车时将空调温度设置为22度
  召回命中: User喜欢开车时将空调温度设置为22度
  召回验证: ✓ 期望「空调温度22度」已确认

--- [轮次 2/5] 导航目的地 ---
  司机: 导航去上海迪士尼
  车机: 正在为您规划前往上海迪士尼的路线，预计30分钟到达
  记忆存储: 导航去上海迪士尼
  召回命中: 导航去上海迪士尼
  召回验证: ✓ 期望「导航目的地上海迪士尼」已确认

--- [轮次 3/5] 音乐偏好 ---
  司机: 开车时帮我放周杰伦的歌
  车机: 已为您播放周杰伦的歌单
  记忆存储: User喜欢在开车时听周杰伦的歌
  召回命中: User喜欢在开车时听周杰伦的歌
  召回验证: ✓ 期望「周杰伦音乐偏好」已确认

--- [轮次 4/5] 座椅加热（过程性记忆） ---
  司机: 帮我把座椅加热打开到3挡
  车机: 座椅加热已调至3挡，请注意温度
  记忆存储 [procedural_memory]: ## Summary of the agent's execution history...
  类型验证: ✓ 过程性记忆类型保留 (procedural_memory)
  召回命中: ## Summary of the agent's execution history...
  召回验证: ✓ 期望「座椅加热操作步骤」已确认

--- [轮次 5/5] 跨轮上下文召回 ---
  司机: 我之前说的空调温度是多少度？
  车机: 您之前说喜欢把空调调到22度
  (跨轮召回模式: 不写入新记忆，仅检索已有记忆)
  召回命中: User喜欢开车的时候把空调调到22度
  召回验证: ✓ 期望「从已有记忆中召回22度偏好」已确认

============================================================
  模拟结果汇总
============================================================
  通过: 5/5
  全部轮次验证通过 ✓
```

退出码 `0` 表示全部通过，`1` 表示有失败项。

---

## 两种后端模式

### 直连模式（默认）

```
cockpit_simulator.py
    │
    ├─ import DesayMemory (src/desaymem)
    ├─ OpenAICompatibleLLM (DashScope)
    ├─ OpenAICompatibleEmbedding (DashScope)
    └─ PostgreSQL (memory_items + memory_entities) + SQLite history.db
```

- 无需启动任何服务，直接 import 调用
- 使用 PostgreSQL + SQLite，与 live API 同一套持久化存储
- 适合本地开发验证 LLM 抽取质量

### HTTP 客户端模式（`--http`）

```
cockpit_simulator.py --http
    │
    └─ httpx.AsyncClient → API server (uvicorn)
                              │
                              ├─ /v1/memories (POST)
                              ├─ /v1/memories/search (POST)
                              └─ /v1/users/{id}/memories (DELETE)
```

- 模拟真实车机客户端通过 HTTP 调用云端记忆服务
- 需先启动 API server：`uvicorn desaymem.api.main:app --host 0.0.0.0 --port 8000`
- API server 可部署在远端，脚本通过 `--base-url` 指定地址
- 适合验证端到端集成链路

---

## 技术细节

### 记忆隔离

所有操作以 `tenant_id + user_id` 为隔离边界。脚本启动时先 `delete_all` 清空目标用户的历史记忆，确保干净起点。

### 存储配置

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `search_threshold` | `0.0` | 关闭相似度门槛，保证演示必有召回 |
| `history_db_path` | `:memory:` | SQLite 内存库，无持久化 |
| `store` | `PgVectorStore` | PostgreSQL + pgvector 记忆与实体 |

### DesayMem API 调用

| 轮次 | API | 关键参数 |
|------|-----|----------|
| 1, 3 | `add()` | `infer=True`（LLM 抽取） |
| 2 | `add()` | `infer=False`（原文存储） |
| 4 | `add()` | `memory_type=procedural_memory` |
| 5 | `search()` | 仅检索，不写入 |
| 每轮 | `search()` | `top_k=5` 验证召回 |

---

## 与其他脚本的关系

| 脚本 | 定位 | LLM 模式 | 场景复杂度 |
|------|------|----------|------------|
| `baseline_demo.py` | 冒烟测试 | Fake/Live 可切换 | 1 轮 |
| `live_cockpit_demo.py` | 真实 LLM 验证 | 仅 Live | 4 步 |
| `compare_with_upstream.py` | 与 Mem0 OSS 对比 | 仅 Fake | 2 用户对比 |
| `inspect_memories.py` | Postgres 数据查看 | 不涉及 LLM | 运维工具 |
| **`cockpit_simulator.py`** | **车机多轮模拟** | **仅 Live** | **5 轮连续对话** |
