# DesayMem_mem0 项目总览

> 德赛西威座舱助手云端长期记忆服务，从 Mem0 OSS (v2.0.18) 源码迁移重构，使用 `desaymem` 命名空间独立运行。

---

## 1. 项目定位

| 项 | 说明 |
|---|---|
| **是什么** | 可私有化部署的云端长期记忆服务 |
| **给谁用** | 德赛西威座舱助手（车机对话 → 云端记忆 → 检索回放） |
| **上游来源** | Mem0 OSS `2.0.18`，commit `4fa4839`，Apache-2.0 |
| **不是什么** | 不是 Mem0 托管云 API 的调用封装，不是 `mem0ai` 包的运行时依赖 |

一句话：**座舱对话进来，经过抽取与去重，变成按租户+用户隔离的长期记忆，再用向量检索出去。**

---

## 2. 技术栈

| 层面 | 技术 |
|---|---|
| 语言 | Python 3.10+ |
| Web 框架 | FastAPI + uvicorn |
| 数据验证 | Pydantic v2、pydantic-settings |
| 向量数据库 | PostgreSQL 16 + pgvector |
| 历史存储 | SQLite（history.db） |
| 数据库驱动 | psycopg3（binary + pool） |
| LLM/Embedding | OpenAI 兼容 HTTP 接口（可接 Qwen / DeepSeek / BGE-M3） |
| HTTP 客户端 | httpx |
| 容器化 | Docker + Docker Compose（api + postgres 两服务） |
| 测试 | pytest + pytest-asyncio，真实 LLM/Embedding + PostgreSQL |
| License | Apache-2.0 |

---

## 3. 架构

### 三层分层

```
API 层 (FastAPI HTTP)  →  服务层 (业务门面)  →  核心层 (DesayMemory 编排)
                                              ↕
                            供给层 (LLM/Embedding)  +  存储层 (pgvector/SQLite)
```

依赖方向：`api → services → core → {providers, stores}`，纯单体架构。

### 核心数据流

```
对话输入
  → 七阶段 add（last-k → 已有记忆 → 单次 ADD-only 抽取 → 去重 → 入库+history → 实体）
  → 三种库：pgvector 记忆 / SQLite history+last-k / 实体库
  → 九阶段 search（lemma → 向量 → 语义 → BM25 → 实体加权 → 排序）
  → 返回相关记忆
```

第一版遵循 Mem0 OSS 基线：**LLM 只做 ADD（生成新事实）**，暂不实现 UPDATE、偏好冲突替代与时间演化。

### 架构图

```mermaid
flowchart LR
    subgraph 车机侧
        A[座舱助手<br/>对话消息]
    end
    subgraph 云端 DesayMem
        B[FastAPI<br/>/v1/memories]
        C[DesayMemory<br/>编排内核]
        D[抽取层<br/>ADD-only]
        E[检索层<br/>九阶段混合]
        F[(PostgreSQL<br/>+ pgvector)]
        I[(SQLite<br/>history.db)]
    end
    subgraph 模型服务
        G[LLM<br/>Qwen / DeepSeek]
        H[Embedding<br/>BGE-M3]
    end
    A -->|写入 / 检索 / 删除| B --> C
    C --> D
    C --> E
    D -->|单次抽取事实| G
    E -->|文本变向量| H
    C -->|去重后入库<br/>按用户检索| F
    C -->|实体抽取与链接| F
    C -->|history + last-k| I
```

---

## 4. 目录结构

```
DesayMem_mem0/
├── src/desaymem/                  # 主源码包（自主命名空间）
│   ├── api/                       # FastAPI 路由、中间件、错误处理
│   │   ├── routes/
│   │   │   ├── health.py           # 健康检查
│   │   │   └── memories.py        # 记忆 CRUD 路由
│   │   ├── dependencies.py         # 依赖注入
│   │   └── main.py                 # create_app() 入口
│   ├── core/                      # 编排核心
│   │   ├── memory.py              # DesayMemory 总编排类
│   │   ├── config.py              # Settings 配置
│   │   ├── models.py              # 数据模型
│   │   ├── enums.py                # 枚举（memory_type 等）
│   │   ├── exceptions.py           # 异常定义
│   │   ├── result.py              # 返回结果封装
│   │   ├── session.py              # 会话管理
│   │   └── logging.py             # 日志（脱敏 api_key / DSN）
│   ├── layers/                    # L2 事件 / L3 画像 / 精排
│   │   ├── episodes.py
│   │   ├── distiller.py
│   │   ├── reranker.py
│   │   └── prompts.py
│   ├── extraction/                # LLM 抽取
│   │   ├── extractor.py           # 抽取器
│   │   ├── prompts.py             # ADD-only Prompt
│   │   ├── parser.py               # JSON 解析
│   │   ├── deduplicator.py         # MD5 去重
│   │   ├── entities.py             # 实体抽取
│   │   └── entity_linker.py        # 实体链接
│   ├── retrieval/                 # 九阶段混合检索
│   │   ├── retriever.py            # 检索器
│   │   ├── lemmatization.py        # 词形归一
│   │   └── scoring.py              # 打分排序
│   ├── providers/                 # LLM / Embedding 提供商
│   │   ├── llm/
│   │   │   ├── base.py
│   │   │   └── openai_compatible.py
│   │   └── embedding/
│   │       ├── base.py
│   │       └── openai_compatible.py
│   ├── stores/                    # 存储层
│   │   ├── base.py                # 抽象接口
│   │   ├── pgvector.py             # PostgreSQL+pgvector 记忆
│   │   ├── sqlite_history.py      # SQLite history+last-k
│   │   ├── messages.py            # 最近 k 条消息
│   │   ├── entities.py             # 实体库
│   │   ├── profile.py              # L3 画像表
│   │   └── memory.py               # 记忆存储
│   ├── services/
│   │   └── memory_service.py      # 业务用例门面
│   ├── sql/                       # 内嵌 SQL
│   ├── cli.py                     # 迁移 CLI (desaymem-migrate)
├── tests/                         # 测试
│   ├── unit/                      # 单元测试
│   ├── integration/               # 集成测试
│   ├── scenarios/                 # 车载场景测试
│   └── conftest.py
├── scripts/                       # 脚本
│   ├── baseline_demo.py           # 车载基线演示
│   ├── compare_with_upstream.py   # 与上游 Mem0 对比
│   └── inspect_memories.py        # 记忆巡检
├── docs/                          # 文档
│   ├── architecture.md            # 架构说明
│   ├── baseline-design.md         # 基线设计
│   ├── api.md                     # HTTP API 文档
│   ├── upstream-mem0.md           # 上游 Mem0 记录
│   ├── source-mapping.md          # 源码映射表
│   └── desaymem-architecture.png  # 架构总图
├── migrations/                    # SQL 迁移脚本
│   ├── 001_initial.sql            # 初始表结构
│   ├── 002_session_entities.sql   # 会话+实体
│   └── 003_bm25.sql               # BM25
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── pyrightconfig.json
├── .env.example
└── README.md
```

---

## 5. 核心模块职责

| 模块 | 关键文件 | 职责 |
|---|---|---|
| **API 层** | `api/main.py`、`api/routes/memories.py` | HTTP 路由、参数校验、Swagger、错误映射 |
| **服务层** | `services/memory_service.py` | 用例门面，连接 API 与核心层 |
| **核心层** | `core/memory.py` | `DesayMemory` 总编排，实现 add/search/get_all/delete/history |
| **抽取层** | `extraction/extractor.py` | LLM 从对话抽取记忆事实（ADD-only），MD5 去重 |
| **分层记忆** | `layers/episodes.py`、`layers/distiller.py` | L2 事件摘要、L3 画像蒸馏、检索精排 |
| **检索层** | `retrieval/retriever.py` | 九阶段：lemma → 向量 → 语义 → BM25 → 实体加权 → 排序 |
| **供给层** | `providers/llm/`、`providers/embedding/` | OpenAI 兼容 LLM + Embedding 调用 |
| **存储层** | `stores/pgvector.py`、`stores/profile.py`、`stores/sqlite_history.py` | 事实+事件向量 / 画像 / SQLite history |
| **CLI** | `cli.py` | 数据库迁移工具（check / apply） |

---

## 6. 入口点

| 入口 | 路径 | 说明 |
|---|---|---|
| HTTP 服务 | `src/desaymem/api/main.py` → `create_app()` | Dockerfile CMD 通过 uvicorn 启动 |
| CLI 工具 | `src/desaymem/cli.py` → `desaymem-migrate` | `desaymem-migrate check` / `desaymem-migrate apply` |
| Python 库 | `src/desaymem/__init__.py` | 导出 `DesayMemory`、`MemoryItem`、`Settings` 等 |

---

## 7. HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 健康检查 |
| `POST` | `/v1/memories` | 写入对话并抽取记忆 |
| `POST` | `/v1/memories/search` | 语义检索 |
| `GET` | `/v1/users/{user_id}/memories` | 列出用户记忆 |
| `GET` | `/v1/users/{user_id}/profile` | 当前画像信念 |
| `GET` | `/v1/users/{user_id}/memories/{memory_id}/history` | 单条记忆 ADD/DELETE 历史 |
| `DELETE` | `/v1/users/{user_id}/memories/{memory_id}` | 删除单条（校验归属） |
| `DELETE` | `/v1/users/{user_id}/memories?confirm=true` | 清空该用户记忆 |

检索与删除始终带 `tenant_id + user_id`，用户 A 不能删除用户 B 的记忆。

---

## 8. 车载 Metadata

每条记忆支持：

| 字段 | 含义 |
|---|---|
| `tenant_id` | 车企或项目隔离 |
| `user_id` | 用户 |
| `vehicle_id` | 车辆 |
| `occupant_id` | 乘员 |
| `session_id` | 会话 |
| `scene` | 驾驶场景 |
| `source` | 数据来源 |

当前检索至少按 `tenant_id + user_id` 强制隔离。

---

## 9. 关键配置

`.env.example` 主要环境变量：

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

默认 embedding 维度 **1024**（BGE-M3 常见宽度）。若改为其他维度，必须同步修改 `migrations/001_initial.sql` 中的 `VECTOR(n)`，并在空库上重新执行迁移。**应用不会在首次请求时偷偷改表。**

---

## 10. 数据库迁移

| 文件 | 内容 |
|---|---|
| `migrations/001_initial.sql` | 初始表结构（记忆表 + 向量列） |
| `migrations/002_session_entities.sql` | 会话消息表 + 实体表 |
| `migrations/003_bm25.sql` | BM25 检索支持 |
| `migrations/004_layers.sql` | L3 `profile_beliefs` + 画像快照 |

```bash
# 检查迁移状态
python -m desaymem.cli check

# 执行迁移
python -m desaymem.cli apply
```

Postgres 容器首次初始化会自动执行 `migrations/*.sql`。

---

## 11. 快速开始

### 本地开发（真实 LLM + PostgreSQL）

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
copy .env.example .env
python -m desaymem.cli apply
python -m pytest -s -v
python scripts/baseline_demo.py
python scripts/compare_with_upstream.py
```

`pytest` 使用 `.env` 中的真实 LLM、Embedding 和 PostgreSQL。

### Docker Compose 部署

```bash
copy .env.example .env
docker compose up --build -d
```

启动后：

- 健康接口：http://localhost:8000/health
- Swagger：http://localhost:8000/docs

API 容器以非 root 用户 `desaymem`（uid 1000）运行。

---

## 12. Python 接口示例

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

---

## 13. 测试

| 目录 | 内容 |
|---|---|
| `tests/unit/` | 单元测试：抽取、去重、打分、解析、迁移 SQL、无 Mem0 依赖验证 |
| `tests/integration/` | 集成测试：API 全流程（真实 LLM + PostgreSQL） |
| `tests/scenarios/` | 车载场景测试：座舱基线流程 |

测试使用 `.env` 中的真实 LLM / Embedding 和 PostgreSQL，不再使用 Fake 或内存库。

---

## 14. 当前能力与未实现

### 已实现

- `add`：写入对话并抽取记忆事实（默认 ADD-only）
- L2 事件摘要（`episodic_memory`）与 L3 用户画像（`profile_beliefs`）
- `memory_type=procedural_memory`：流程记忆 Prompt 写入步骤摘要
- `infer=false`：不抽事实，直接把消息原文入库
- 最近 k 条会话消息参与抽取（默认 10 条）
- 实体库抽取、去重、双向链接；检索时可加权
- `search`：九阶段混合检索 + 语义精排，响应附带画像
- `GET /v1/users/{user_id}/profile`：按用户读取当前信念
- `history`：单条记忆 ADD/DELETE 事件
- `get_all` / `delete` / `delete_all`
- 租户 + 用户隔离
- 基础车载 metadata
- FastAPI + Swagger + Docker Compose

### 暂未实现

- Knowledge Memory
- Skill 蒸馏
- 主动服务
- 多模态原始数据
- 图记忆
- 端云同步

---

## 15. 文档索引

| 文档 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 架构说明、分层时序图 |
| [docs/layers.md](docs/layers.md) | L2 事件摘要与 L3 用户画像 |
| [docs/baseline-design.md](docs/baseline-design.md) | 基线设计 |
| [docs/api.md](docs/api.md) | HTTP API 完整约定 |
| [docs/upstream-mem0.md](docs/upstream-mem0.md) | 上游 Mem0 记录 |
| [docs/source-mapping.md](docs/source-mapping.md) | 源码映射表 |
| [LICENSE](LICENSE) | Apache-2.0 |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | 第三方声明 |

---

## 16. 依赖清单（pyproject.toml）

**运行时依赖**：
- `fastapi>=0.115.0`
- `uvicorn[standard]>=0.32.0`
- `pydantic>=2.7.3`
- `pydantic-settings>=2.4.0`
- `openai>=1.40.0`
- `httpx>=0.27.0`
- `psycopg[binary,pool]>=3.2.0`
- `pgvector>=0.3.6`

**开发依赖**：
- `pytest>=8.2.2`
- `pytest-asyncio>=0.24.0`
- `pytest-cov>=5.0.0`

**CLI 入口**：`desaymem-migrate = "desaymem.cli:main"`
