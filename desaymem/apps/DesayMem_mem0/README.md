# DesayMem_mem0

德赛车载云端长期记忆系统（从 Mem0 OSS 源码迁移）。

基于 [Mem0 OSS](https://github.com/mem0ai/mem0) 的开源思想与核心源码迁移、重构，形成一套**可私有化部署、自主维护**的云端记忆服务。面向座舱助手的长期记忆基线，后续可演进到用户画像、多乘员、多模态、Event / Skill 与端云协同。

> 本项目**不是** Mem0 托管云 API 的调用封装，也**不是** `mem0ai` 包的运行时依赖。  
> 业务代码统一使用 `desaymem` 命名空间，可脱离 Mem0 源码仓库独立运行。

上游快照：Mem0 OSS `2.0.18`，commit `4fa483907704735ba0bec030e3c946ee1614b50e`，Apache-2.0。  
详见 [docs/upstream-mem0.md](docs/upstream-mem0.md)、[docs/source-mapping.md](docs/source-mapping.md)。

---

## 定位

```
对话输入
  → 七阶段 add（last-k → 已有记忆 → 单次 ADD-only 抽取 → 去重 → 入库+history → 实体）
  → L2 事件摘要（同表 episodic_memory）+ L3 画像（profile_beliefs）
  → 九阶段 search + 语义精排，画像按用户附带
  → 返回相关记忆与当前信念
```

第一版遵循所选 Mem0 OSS 版本的核心基线：**LLM 只做 ADD（生成新事实）**。现已新增**时间衰减打分**与**冲突记忆消解**，确保用户偏好更新后（如"22度"→"改为26度"）检索只返回最新值。

## 当前能力

- `add`：写入对话并抽取记忆事实（默认 ADD-only）
- L2 事件摘要与 L3 用户画像（无关键词路由；见 [docs/layers.md](docs/layers.md)）
- `memory_type=procedural_memory`：按 Mem0 流程记忆 Prompt 写入步骤摘要
- `infer=false`：不抽事实，直接把消息原文入库
- 最近 k 条会话消息（SQLite `messages`，默认 10 条）参与抽取
- 实体库（`memory_entities`）抽取、去重、与记忆双向链接；检索时可加权
- `search`：混合检索 + 语义精排，响应附带 `profile`
- `GET /v1/users/{user_id}/profile`：当前信念
- `history`：单条记忆的 ADD/DELETE 事件（SQLite `history`）
- `get_all`：查看某用户记忆（可按 `memory_type` 过滤）
- `delete` / `delete_all`：删除单条或清空用户记忆（同时清理实体、画像与最近消息）
- 租户 + 用户隔离（检索与删除都强制 `tenant_id + user_id`）
- 基础车载 metadata：车辆、乘员、会话、场景、来源
- FastAPI + Swagger
- Docker Compose（仅 api + postgres；history 挂在 API 数据卷）
- 单元测试与车载基线 Demo

## L2 事件记忆与 L3 用户画像

L1 是系统的事实真源；L2 和 L3 都是由 L1 派生、可重新构建的记忆层。LLM 负责根据证据提出结构化判断，Python 校验证据并执行写库，LLM 不直接修改数据库。

| 层级 | 保存内容 | 产生方式 | 主要用途 |
| --- | --- | --- | --- |
| L1 Fact | 自包含的原子事实 | 从本轮对话、最近消息、已有近邻和当前画像中抽取 | 审计真源、向量/BM25 检索、重建 L2/L3 |
| L2 Episode | 一次连续经历的事件摘要 | L1 入库后，LLM 判断新事实是续写当前事件还是开启新事件 | 召回“那次出行/那顿饭”等完整经历，并展开其 L1 证据 |
| L3 Profile | 当前有效的身份、偏好和重复习惯 | 新 L1 与历史近邻组成证据簇，LLM 提议信念，Python 执行 CREATE/CONFIRM/SUPERSEDE 等决策 | 辅助下一轮抽取、搜索精排和个性化回答 |

### L2 如何产生和使用

每次 L1 成功写入后，`EpisodeBuilder` 会读取同一 `tenant_id + user_id + occupant_id` 下当前未关闭的事件，并结合新事实、消息内容、事件绝对时间和向量相似度请求 LLM 判断：

```text
continues=true   → 续写现有 episodic_memory，合并 source_memory_ids
continues=false  → 关闭旧事件，创建新的 active episodic_memory
```

L2 摘要只描述“发生了什么”，时间由 metadata 的绝对字段表达：

```json
{
  "episode_status": "active",
  "source_memory_ids": ["l1-id-1", "l1-id-2"],
  "occurred_at": "2026-08-28T15:30:00+08:00",
  "occurred_end": "2026-08-28T16:10:00+08:00"
}
```

L2 不应保存“上周、昨天、前几天”等相对查询时间。用户查询“上周那次出行”时，搜索精排 LLM 根据当前日期和候选的 `occurred_at/occurred_end` 计算时间窗口。L2 命中后，系统会把 `source_memory_ids` 对应的同租户、同用户 L1 加入精排池：L2 用于找到完整事件，L1 用于提供事实依据。

### L3 如何产生和使用

`ProfileDistiller` 使用本轮 L1 和历史语义近邻构建证据簇。LLM 输出带真实 `evidence_ids` 的 belief 建议，Python 验证后执行：

- `CREATE`：创建新信念；
- `CONFIRM`：追加证据并提高支持数；
- `REFINE`：用更精确的信念替代旧信念；
- `COEXIST`：不同条件下的偏好同时存在；
- `SUPERSEDE`：新值成为 active，旧值保留但标记为 superseded；
- `NOOP`：证据不足，不写入。

声明为 `recurring` 的习惯必须至少具有两个不同自然日的真实证据，否则 Python 会降级为 `episode`，避免把一次经历误判为长期习惯。active beliefs 会组成 `user_profile_snapshots.narrative`，并用于：

1. 下一次 `add` 的事实抽取上下文；
2. `search` 的 LLM 语义精排；
3. `/v1/memories/search` 响应中的 `profile`；
4. `GET /v1/users/{user_id}/profile` 画像读取。

L2/L3 是 best-effort 派生层：其生成失败不会回滚已经写入的 L1。详细数据结构和时序见 [docs/layers.md](docs/layers.md)。

## 当前版本暂未实现

请勿将下列能力视为已交付（也没有空壳模块假装完成）：

- Knowledge Memory
- Skill 蒸馏
- 主动服务
- 多模态原始数据
- 图记忆
- 端云同步

---

## 架构

一句话：**座舱对话进来，经过抽取与去重，变成按租户+用户隔离的长期记忆，再用向量检索出去。**

![DesayMem_mem0 架构总图](docs/desaymem-architecture.png)

```mermaid
flowchart LR
    subgraph 车机侧
        A[座舱助手]
    end
    subgraph 云端
        B[FastAPI]
        C[DesayMemory 内核]
        D[(PostgreSQL + pgvector)]
    end
    subgraph 模型
        E[LLM 抽取]
        F[Embedding]
    end
    A -->|写入 / 检索 / 删除| B --> C
    C -->|ADD-only 一次抽取| E
    C -->|文本变向量| F
    C -->|去重入库 / 按用户检索| D
```

| 分层 | 包 | 职责 |
| --- | --- | --- |
| API | `desaymem.api` | HTTP、校验、错误映射、Swagger |
| 服务 | `desaymem.services` | 用例门面 |
| 核心 | `desaymem.core` | `DesayMemory`、配置、模型、异常 |
| 抽取 | `desaymem.extraction` | ADD-only Prompt、JSON 解析、MD5 去重、实体 |
| 检索 | `desaymem.retrieval` | 十阶段：lemma、向量、BM25、实体加权、时间衰减、冲突消解 |
| 供给 | `desaymem.providers` | OpenAI 兼容 LLM / Embedding |
| 存储 | `desaymem.stores` | pgvector 记忆、SQLite history、实体库 |

写入路径与 Mem0 OSS 2.0.18 七阶段 add 对齐：last-k → 已有记忆 → 单次 LLM 抽取 → lemma/MD5 去重 → 入库+history → 实体链接 → 保存本轮消息。`memory_type=procedural_memory` 时改为流程摘要写入。LLM **不会**在 v1 发出 UPDATE / DELETE。检索走十阶段混合打分（语义 + BM25 + 实体加权 + 时间衰减 + 冲突消解），删除始终带 `tenant_id + user_id`。

分层、时序和部署图见 [docs/architecture.md](docs/architecture.md)。

---

## 环境要求

- Python 3.10+
- Docker + Docker Compose（部署 PostgreSQL 16 + pgvector）
- OpenAI 兼容的 LLM 与 Embedding 服务（Qwen / DeepSeek / BGE-M3 HTTP 等）

本地无 Docker 时仍需 PostgreSQL + `.env` 中的 LLM/Embedding 才能跑测试与 Demo。

---

## 快速开始

### 1. 安装与真实测试

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
copy .env.example .env
# 填入 POSTGRES_DSN、LLM_*、EMBEDDING_*
python -m desaymem.cli apply
python -m pytest -s -v
python scripts/baseline_demo.py
python scripts/cockpit_simulator.py    # 车机多轮对话模拟器（Live LLM）
python scripts/compare_with_upstream.py
```

`pytest` 使用 `.env` 中的真实 LLM、Embedding 和 PostgreSQL，不再走 Fake / 内存库。

### 2. 配置

复制环境模板（**不要提交 `.env`**）：

```bash
copy .env.example .env
```

```env
APP_NAME=DesayMem_mem0
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=8000
LOG_LEVEL=INFO

POSTGRES_DSN=postgresql://desaymem:change_me@postgres:5432/desaymem

LLM_MODEL=
LLM_API_KEY=
LLM_BASE_URL=

EMBEDDING_MODEL=
EMBEDDING_API_KEY=
EMBEDDING_BASE_URL=
EMBEDDING_DIMS=1024

HISTORY_DB_PATH=history.db
```

日志会脱敏 `api_key`、数据库密码和 DSN，不会把对话原文打到 INFO。

默认 embedding 维度为 **1024**（BGE-M3 常见宽度）。若改为其他维度，必须同步修改 `migrations/001_initial.sql` 中的 `VECTOR(n)`，并在空库上重新执行迁移。**应用不会在首次请求时偷偷改表。**

### 3. Docker Compose

只启动 `api` 与 `postgres`：

```bash
copy .env.example .env
docker compose up --build -d
```

健康检查通过后：

- 健康接口：http://localhost:8000/health
- Swagger：http://localhost:8000/docs

Postgres 首次初始化会执行 `migrations/*.sql`。之后可检查或显式迁移：

```bash
python -m desaymem.cli check
python -m desaymem.cli apply
```

API 容器以非 root 用户 `desaymem`（uid 1000）运行。

---

## HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查 |
| `POST` | `/v1/memories` | 写入对话并抽取记忆 |
| `POST` | `/v1/memories/search` | 语义检索 |
| `GET` | `/v1/users/{user_id}/memories` | 列出用户记忆 |
| `GET` | `/v1/users/{user_id}/memories/{memory_id}/history` | 单条记忆 ADD/DELETE 历史 |
| `DELETE` | `/v1/users/{user_id}/memories/{memory_id}` | 删除单条（校验归属） |
| `DELETE` | `/v1/users/{user_id}/memories?confirm=true` | 清空该用户记忆 |

写入示例：

```json
{
  "tenant_id": "default",
  "user_id": "user_001",
  "vehicle_id": "vehicle_001",
  "occupant_id": "primary",
  "session_id": "session_001",
  "scene": "driving",
  "messages": [
    {"role": "user", "content": "我开车的时候喜欢把空调调到22度"}
  ],
  "infer": true,
  "memory_type": null
}
```

检索示例：

```json
{
  "tenant_id": "default",
  "user_id": "user_001",
  "query": "用户习惯的空调温度是多少",
  "top_k": 5,
  "filters": {
    "vehicle_id": "vehicle_001"
  }
}
```

检索与删除始终带上 `tenant_id + user_id`，不会只靠向量相似度。用户 A 不能删除用户 B 的记忆。完整约定见 [docs/api.md](docs/api.md)。

### 冲突消解

当用户更新偏好（如先说"喜欢22度"，后说"从现在开始改为26度"），两条记忆都会被存入（ADD-only 设计）。检索时冲突消解模块会：

1. 识别"从现在开始/改为/换成/不再/默认设置"等显式更新表达
2. 按 `(user_id, memory_key)` 分组——只有同用户同属性的记忆才比较
3. 若值不同且有显式更新，最新记忆 **SUPERSEDES** 旧记忆，旧记忆从结果中过滤
4. 普通关联（同实体不同属性）标记为 **RELATED_TO**，不误判覆盖
5. 结果 metadata 注入 `memory_key`、`effective_at`、`is_current`、`relation_type`、`supersedes`

时间衰减分（`temporal_score`）为近期记忆提供适度加分（权重 0.15，半衰期 30 天），但不会压过高度相关的旧记忆。

## Python 接口

```python
from desaymem import DesayMemory

memories = await memory.add(messages, user_id="user_001", tenant_id="default")
hits = await memory.search("空调温度", user_id="user_001", tenant_id="default", top_k=5)
events = await memory.history(memories[0]["id"])
all_rows = await memory.get_all("user_001", tenant_id="default")
await memory.delete(memory_id, user_id="user_001", tenant_id="default")
await memory.delete_all("user_001", tenant_id="default")
```

系统内部不依赖 `mem0` / `MemoryClient`。

## 车载 Metadata

每条记忆支持：

| 字段 | 含义 |
| --- | --- |
| `tenant_id` | 车企或项目隔离 |
| `user_id` | 用户 |
| `vehicle_id` | 车辆 |
| `occupant_id` | 乘员 |
| `session_id` | 会话 |
| `scene` | 驾驶场景 |
| `source` | 数据来源 |

当前检索至少按 `tenant_id + user_id` 强制隔离。

---

## 目录

```
DesayMem_mem0/
├── src/desaymem/          # 自主命名空间
│   ├── api/               # FastAPI
│   ├── core/              # DesayMemory 编排
│   ├── extraction/        # 抽取 / Prompt / 去重
│   ├── retrieval/         # 十阶段检索 / BM25 / 时间衰减 / 冲突消解
│   ├── providers/         # LLM / Embedding
│   ├── stores/            # pgvector / SQLite history / 实体库
│   └── services/          # 业务门面
├── tests/                 # 单元 / 接口 / 车载场景
├── scripts/               # Demo 与上游对比
├── docs/                  # 架构、映射、License
├── migrations/            # SQL 迁移（应用不自动改表）
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## 脚本

| 脚本 | 用途 | LLM 模式 | 依赖 |
| --- | --- | --- | --- |
| `scripts/baseline_demo.py` | 基线冒烟，验证 add → search → listed 基本链路 | Fake/Live 可切换 | InMemory |
| `scripts/live_cockpit_demo.py` | 真实 DashScope LLM 4 步验证（抽取/召回/隔离/过程性记忆） | 仅 Live | InMemory |
| `scripts/cockpit_simulator.py` | **车机多轮对话模拟器**，5 轮连续对话端到端验证 | 仅 Live | InMemory（`--http` 可切 API server） |
| `scripts/compare_with_upstream.py` | 与 Mem0 OSS 在解析/去重/隔离上做定性对比 | 仅 Fake | InMemory |
| `scripts/inspect_memories.py` | 查看 Postgres 中已落库的记忆（运维工具） | 不涉及 | PgVector |

车机模拟器快速运行：

```bash
# 直连模式（Live LLM + InMemory，无需 Postgres）
python scripts/cockpit_simulator.py

# HTTP 客户端模式（模拟真实车机客户端）
python scripts/cockpit_simulator.py --http --base-url http://localhost:8000
```

模拟器覆盖 5 轮对话场景：空调偏好 → 导航目的地 → 音乐偏好 → 座椅加热（过程性记忆）→ 跨轮上下文召回。详见 [车机模拟器测试流程](docs/cockpit-simulator.md)。

---

## 文档

- [架构说明](docs/architecture.md)
- [基线设计](docs/baseline-design.md)
- [上游 Mem0 记录](docs/upstream-mem0.md)
- [源码映射表](docs/source-mapping.md)
- [HTTP API](docs/api.md)
- [车机多轮对话模拟器测试流程](docs/cockpit-simulator.md)
- [架构演进与多轮对话实践](docs/cockpit-memory-design.md)
- [第三方声明](THIRD_PARTY_NOTICES.md)

## License

Apache License 2.0。含 Mem0 OSS 衍生代码，版权与来源见 [LICENSE](LICENSE) 与 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
