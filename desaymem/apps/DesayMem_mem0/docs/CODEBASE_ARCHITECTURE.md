# DesayMem_mem0 代码架构全解

> 项目路径：`/data/pengshuang/desaymem/apps/DesayMem_mem0`
> 本文基于对源码的完整阅读整理，涵盖：项目定位、目录结构、每个文件的作用与函数职责、以及「记忆添加」和「记忆检索」两条端到端流程。

---

## 目录

1. [项目定位与总体架构](#1-项目定位与总体架构)
2. [目录结构总览](#2-目录结构总览)
3. [core/ — 基础层（配置、模型、核心编排）](#3-core--基础层配置模型核心编排)
4. [stores/ — 存储层（pgvector + SQLite）](#4-stores--存储层pgvector--sqlite)
5. [extraction/ — 抽取层（写入路径的 LLM 提取）](#5-extraction--抽取层写入路径的-llm-提取)
6. [retrieval/ — 检索层（读取路径的打分与融合）](#6-retrieval--检索层读取路径的打分与融合)
7. [layers/ — 分层记忆（L1/L2/L3 晋升）](#7-layers--分层记忆l1l2l3-晋升)
8. [api/ + services/ + providers/ — API 与外部服务](#8-api--services--providers--api-与外部服务)
9. [migrations/ — 数据库迁移](#9-migrations--数据库迁移)
10. [tests/ + scripts/ — 测试与工具脚本](#10-tests--scripts--测试与工具脚本)
11. [流程一：记忆添加（Add）端到端详解](#11-流程一记忆添加add端到端详解)
12. [流程二：记忆检索（Search）端到端详解](#12-流程二记忆检索search端到端详解)
13. [附录：请求/响应模型与 HTTP 端点速查](#13-附录请求响应模型与-http-端点速查)

---

## 1. 项目定位与总体架构

**DesayMem_mem0** 是德赛西威座舱助手（车机对话）的云端长期记忆服务。它从 Mem0 OSS v2.0.18（commit `4fa4839`）源码迁移、重构而来，使用独立命名空间 `desaymem`，运行时**完全不依赖 `mem0ai` 包**（有专门测试 `test_no_mem0_dependency.py` 用正则扫描保证）。

一句话概括数据流：**座舱对话进来，经过 LLM 抽取与去重，变成按「租户+用户」隔离的分层长期记忆；检索时经向量+关键词+实体+时间多路融合召回，再经冲突消解与 LLM 精排后返回。**

### 三层记忆模型

| 层级 | 名称 | memory_type / 表 | 内容 | 查询方式 |
|------|------|------------------|------|----------|
| **L1** | 事实层 | `semantic_memory` / `procedural_memory` → `memory_items` 表 | 自包含的原子事实片段，带 embedding | 向量 + BM25 + 实体 + 时间混合检索 |
| **L2** | 情节层 | `episodic_memory` → `memory_items` 表（以 memory_type 区分） | 一次连贯经历的摘要，指向 L1 证据（`source_memory_ids`） | 与 L1 一起向量召回，命中后展开其 L1 证据 |
| **L3** | 画像层 | `profile_beliefs` + `user_profile_snapshots` 表 | 当前有效的长期信念（Subject-Attribute-Value），带 stability 与 valid_from/valid_to | 按用户直接加载，不参与向量竞争 |

核心设计原则：
- **L1 是事实真源**，L2/L3 均可从 L1 重建；
- **LLM 只做 ADD-only 抽取**，不做 UPDATE/DELETE —— 偏好变更通过检索期的冲突消解过滤旧值；
- **LLM 只输出结构化 JSON 判断**，Python 校验证据 ID 后执行写库；
- 数据隔离统一为 `tenant_id + user_id`（可选 `vehicle_id / occupant_id / session_id / scene / source`）。

### 部署拓扑（docker-compose）

仅两个容器，无 Redis/Neo4j/ES：

```
车机/调用方 → FastAPI (desaymem-api, 127.0.0.1:8766→8000, 非 root)
                 │
                 ├── LLM / Embedding（外部 OpenAI 兼容服务：Qwen/DeepSeek/vLLM 等）
                 ├── PostgreSQL 16 + pgvector (desaymem-postgres, 自动执行 migrations/*.sql)
                 └── SQLite history.db（API 进程本地文件卷，历史审计 + last-k 消息）
```

---

## 2. 目录结构总览

```
DesayMem_mem0/
├── src/desaymem/               # 主源码
│   ├── __init__.py             # 公共 API 重导出（DesayMemory, Settings, 模型, 异常）
│   ├── cli.py                  # 数据库迁移 CLI（apply/check）
│   ├── asyncio_compat.py       # Windows event loop 兼容
│   ├── core/                   # 基础层：配置/模型/枚举/异常/日志/结果信封/核心编排
│   │   ├── config.py           # Settings（pydantic-settings，全部可配项）
│   │   ├── models.py           # MemoryScope/MemoryItem/ChatMessage/BeliefItem/ProfileView/SearchFilters
│   │   ├── memory.py           # ★ DesayMemory 核心编排类（add/search/delete/...）
│   │   ├── session.py          # session_scope 键构建与转义
│   │   ├── enums.py            # MemoryType/BeliefStability/BeliefStatus/BeliefDecision
│   │   ├── logging.py          # 日志配置 + 敏感信息脱敏
│   │   ├── exceptions.py       # 7 类结构化异常（带 error_code/suggestion）
│   │   └── result.py           # AddResult/SearchResult/... 统一结果信封
│   ├── stores/                 # 存储层：Protocol 接口 + PG/SQLite/内存实现
│   │   ├── base.py             # VectorStore/EntityStore/ProfileStore/SessionMessageStore 协议
│   │   │                       #   + StoredMemory/StoredEntity/StoredBelief 数据类
│   │   ├── pgvector.py         # ★ PgVectorStore（memory_items 表，连接池提供者）
│   │   ├── entities.py         # PgEntityStore（memory_entities 表）
│   │   ├── messages.py         # PgMessageStore（session_messages 表）
│   │   ├── profile.py          # PgProfileStore/InMemoryProfileStore（profile_beliefs 等）
│   │   ├── sqlite_history.py   # SQLiteHistoryStore（history + messages 表）
│   │   └── memory.py           # InMemory* 三件套（仅测试/离线 demo 用）
│   ├── extraction/             # 抽取层（写入路径）
│   │   ├── extractor.py        # ★ MemoryExtractor：ADD-only LLM 抽取编排
│   │   ├── parser.py           # 消息展平、LLM 响应 JSON 清洗/提取/解析
│   │   ├── prompts.py          # ADDITIVE_EXTRACTION_PROMPT 等 4 个 prompt 模板
│   │   ├── entities.py         # 无 NER 依赖的正则实体提取（+可选 spaCy）
│   │   ├── entity_linker.py    # EntityLinker：实体消歧与链接、检索期实体加权
│   │   └── deduplicator.py     # MD5 哈希去重
│   ├── retrieval/              # 检索层（读取路径）
│   │   ├── retriever.py        # ★ MemoryRetriever：九步检索管线
│   │   ├── scoring.py          # 余弦/BM25 归一化/时间衰减/多路融合打分
│   │   ├── lemmatization.py   # 中英文词形还原+分词（BM25 前处理）
│   │   └── conflict_resolution.py # 冲突消解：superseded 旧值过滤
│   ├── layers/                 # 分层记忆（L2/L3）
│   │   ├── episodes.py         # EpisodeBuilder：episode 边界 LLM 判断
│   │   ├── distiller.py        # ProfileDistiller：L3 信念蒸馏与生命周期
│   │   ├── reranker.py         # SemanticReranker：LLM 语义重排
│   │   ├── prompts.py          # EPISODE/DISTILL/RERANK 三个 system prompt
│   │   └── parser.py           # L2/L3 LLM 输出轻量解析（fake ID→UUID 映射）
│   ├── api/                    # FastAPI 层
│   │   ├── main.py             # create_app 工厂 + 异常映射 + CORS + health 中间件
│   │   ├── dependencies.py     # lifespan：构建 DesayMemory/MemoryService 单例
│   │   └── routes/             # health.py + memories.py（8 个端点）
│   ├── services/
│   │   └── memory_service.py   # MemoryService（DesayMemory 的薄 Facade）
│   └── providers/              # 外部服务接口
│       ├── llm/                # LLMProvider 协议 + OpenAICompatibleLLM
│       └── embedding/          # EmbeddingProvider 协议 + OpenAICompatibleEmbedding
├── migrations/                 # 5 个 SQL 迁移（001~005）
├── tests/                      # unit + integration + scenarios
├── scripts/                    # demo/评测/模拟器脚本
├── docs/                       # architecture.md 等已有文档
└── docker-compose.yml          # api + postgres 两容器
```

（★ 标记为最核心的文件）

---

## 3. core/ — 基础层（配置、模型、核心编排）

### `__init__.py`
包入口，把 `Settings`、`DesayMemory`、4 个模型、`MemoryType` 枚举、7 个异常重导出为 `desaymem` 顶层符号。无逻辑。

### `cli.py` — 数据库迁移 CLI
| 函数 | 作用 |
|------|------|
| `default_migrations_dir()` / `default_migration_path()` | 在 cwd 或源码旁定位 `migrations/` 目录和 `001_initial.sql` |
| `_statements(sql)` | 按分号拆 SQL，剔除空语句与纯注释行（提交 a2f94b0 修复点） |
| `apply_migration(dsn, sql_path)` | psycopg autocommit 逐条执行迁移语句 |
| `apply_all_migrations(dsn, dir)` | 按文件名顺序应用全部 `.sql` |
| `check_ready(dsn, dims)` | 实例化 PgVectorStore 并调 `check_schema` 校验表/维度/扩展就绪 |
| `main()` | argparse 入口：`apply` / `check` 两个子命令 |

### `asyncio_compat.py`
`ensure_compatible_event_loop()`：仅 Windows 下切换 `WindowsSelectorEventLoopPolicy`，解决 psycopg 异步兼容。

### `core/config.py` — `Settings(BaseSettings)`
全部配置项（支持 .env），核心分组：

| 组 | 关键字段（默认值） |
|----|-------------------|
| 应用 | `app_name`、`app_env`、`app_host`、`app_port=8000`、`log_level=INFO` |
| 数据库 | `postgres_dsn`、`postgres_pool_min=1`、`postgres_pool_max=10`、`migration_file`、`history_db_path="history.db"` |
| LLM | `llm_model`、`llm_api_key`、`llm_base_url`、`llm_temperature=0.1`、`llm_max_tokens=2000`、`llm_top_p=0.1` |
| 嵌入 | `embedding_model`、`embedding_api_key`、`embedding_base_url`、`embedding_dims=1024` |
| 检索 | `search_threshold=0.1`、`search_existing_top_k=10`、`default_top_k=5`、`get_all_limit=100`、`rerank_candidate_limit=32` |
| 上下文 | `last_k_messages=10`、`custom_instructions`、`use_input_language=True` |
| 实体 | `entity_match_threshold=0.95`、`entity_boost_min_similarity=0.5`、`enable_entity_store=True` |
| 分层开关 | `enable_episodes=True`、`enable_profile=True`、`enable_rerank=True` |
| 画像 | `profile_match_threshold=0.82`、`profile_neighbor_top_k=12` |

方法：`require_runtime_secrets()` 校验运行时必需密钥（缺失抛 `ConfigurationError`）；`migration_path()` 定位迁移文件；`get_settings()` 为 `@lru_cache` 单例工厂。

### `core/models.py` — 公共数据模型
| 模型 | 字段要点 | 用途 |
|------|----------|------|
| `MemoryScope` | `tenant_id`+`user_id` 必填；`vehicle_id`/`occupant_id`/`session_id`/`scene`/`source` 可选 | 一次请求的作用域（多租户隔离） |
| `MemoryItem` | id、content、作用域字段、`memory_type`、`content_hash`、`text_lemmatized`、`score`、`score_details`、`metadata`、时间戳、embedding 信息 | 对外暴露的单条记忆 |
| `ChatMessage` | role(user/assistant/system)、content、name | API 请求中的对话消息 |
| `BeliefItem` | subject、attribute、value、conditions、stability、status、confidence、support_count、evidence_* | L3 画像信念 |
| `ProfileView` | narrative + beliefs 列表 | 用户画像视图 |
| `SearchFilters` | vehicle/occupant/session/scene/source/memory_type 全可选；`as_store_filters()` 丢弃空值 | 检索过滤 |

### `core/memory.py` — `DesayMemory`（★ 全系统核心编排）
`__init__` 注入 LLM/Embedding/向量存储等，并组装 6 个子引擎：
`MemoryExtractor`（提取）、`EntityLinker`（实体链接）、`MemoryRetriever`（检索）、`EpisodeBuilder`（L2）、`ProfileDistiller`（L3）、`SemanticReranker`（重排）。

私有工具函数：`_normalize_messages`（消息规范化）、`_scope_from_kwargs`（合并 scope）、`_absolute_observation_time`（时间→绝对 UTC ISO）、`_episode_time_bounds`（episode 时间边界）、`_validate_memory_type`（只允许 procedural）、`_default_entity_store/_default_profile_store`（Pg 或内存实现自动选择）、`_redact_dsn`（DSN 脱敏）。

公开方法：
| 方法 | 作用 |
|------|------|
| `from_settings(settings)` | 工厂：自动构造 OpenAI 兼容 LLM/Embedding + PgVectorStore + SQLite 历史 |
| `add_result(...)` → `AddResult` | ★ 记忆添加主流程（见 §11） |
| `search_result(...)` → `SearchResult` | ★ 记忆检索主流程（见 §12） |
| `get_all_result(...)` | 列出用户全部记忆（过滤+limit） |
| `delete_result(...)` | 删除单条：先解绑实体链接 → store 删除 → 记录 history |
| `delete_all_result(...)` | 清空用户数据（实体/消息/画像/向量四类 store） |
| `get_profile(...)` → `ProfileView` | 获取 L3 用户画像 |
| `history_result(...)` | 查记忆变更历史（带 scope 权限校验） |
| `_evolve_layers(...)` | 写入后联动：L2 episode 判断 + L3 蒸馏（每次 add 后触发） |
| `_upsert_episode(...)` | L2 episode 续接/闭合/新建的落地逻辑 |
| `_expand_episode_evidence(...)` | 检索时展开 L2 命中记忆的 L1 证据 |
| `healthcheck/prepare/close` | 健康检查 / 建表 / 关闭连接 |

三条 add 分支：`infer=True`（默认，七阶段 LLM 提取）、`memory_type=procedural_memory`（LLM 过程摘要）、`infer=False`（原文直存）。

### `core/session.py`
- `escape_scope_value(value)`：转义 `% & =`，防 scope 键解析歧义；
- `build_session_scope(scope)`：按 tenant→user→occupant→session 顺序拼 `key=value&...`，作为 last-k 消息存储的隔离键。

### `core/enums.py`
- `MemoryType`：`semantic_memory` / `episodic_memory` / `procedural_memory`
- `BeliefStability`：`episode`（单次）/ `recurring`（多次出现）/ `identity`（身份级）
- `BeliefStatus`：`active` / `superseded`
- `BeliefDecision`：`CREATE/CONFIRM/REFINE/COEXIST/SUPERSEDE/NOOP`（LLM 对信念的六种决策）

### `core/logging.py`
`redact_value/redact_text/redact_mapping` + `RedactingFilter`：自动脱敏日志中的 API key、密码、Bearer token、DSN 密码；`configure_logging/get_logger` 提供全局配置。所有日志都经脱敏 filter。

### `core/exceptions.py`
`DesayMemError(message, error_code, details, suggestion, debug_info)` 基类 + 7 个子类，`to_dict()` 输出 API 友好 JSON：
`ValidationError(VAL_001)`、`MemoryNotFoundError(MEM_404)`、`ConfigurationError(CFG_001)`、`DatabaseError(DB_001)`、`EmbeddingError(EMBED_001)`、`LLMError(LLM_001)`、`VectorStoreError(VECTOR_001)`。

### `core/result.py`
统一结果信封（均带 `to_public_dict()`）：`AddResult`（含 `episode`、`beliefs_applied`）、`SearchResult`（含 `profile`）、`ListResult`、`DeleteResult`、`DeleteAllResult`、`HistoryResult`、`HealthResult`。

---

## 4. stores/ — 存储层（pgvector + SQLite）

### `stores/base.py` — 协议 + 数据类
- **`StoredMemory`**：id、作用域、`memory_type`、`content`、`content_hash`、`text_lemmatized`、`embedding`、`metadata`、`score`、时间戳。
- **`StoredEntity`**：`entity_text`、`entity_type`、`normalized_text`（唯一键）、`embedding`、`linked_memory_ids`。
- **`StoredBelief`**：subject/attribute/value/conditions、stability、status、confidence、support_count、`evidence_memory_ids/episode_ids`、`attribute_embedding`、valid_from/valid_to。
- **`VectorStore` Protocol**：`insert/update/search/list/get/get_active_episode/delete/delete_by_user/existing_hashes/keyword_search/healthcheck/check_schema/close`。
- **`EntityStore` Protocol**、**`ProfileStore` Protocol**（list_active/search_similar/insert/update/snapshot 读写/delete_by_user）、**`SessionMessageStore` Protocol**（save/get_last/delete_by_user）。

### `stores/pgvector.py` — `PgVectorStore`（★）
`memory_items` 表的唯一写入者，同时是所有 PG 存储的**异步连接池提供者**（`psycopg_pool.AsyncConnectionPool` + `pgvector.psycopg.register_vector_async`）。
- `check_schema(dims)`：校验表/列/`vector(N)` 维度/pgvector 扩展；
- `insert`：`ON CONFLICT (tenant_id, user_id, content_hash) DO NOTHING`（DB 级去重最后防线）；
- `search`：`embedding <=> %s::vector`（HNSW 余弦距离），`score = max(0, 1-distance)`，带 tenant/user/可选 filters；
- `keyword_search`：`to_tsvector('simple', text_lemmatized) @@ plainto_tsquery(...)` + `ts_rank_cd`（PG FTS，即 BM25 路径）；
- `get_active_episode`：找 `memory_type='episodic_memory'` 且 `metadata->>'episode_status'='active'` 的行；
- 其余为常规 CRUD 与 `existing_hashes`。

### `stores/entities.py` — `PgEntityStore`
`memory_entities` 表。`insert` 用 `ON CONFLICT (tenant_id, user_id, normalized_text) DO UPDATE` 合并 `linked_memory_ids`；提供向量 ANN 搜索、按 normalized_text 精确查、update_links、删除。持有 PgVectorStore 引用共享连接池。

### `stores/messages.py` — `PgMessageStore`
`session_messages` 表（last-k 消息）。`save_messages` 插入后用子查询淘汰超出 limit 的旧消息；`get_last_messages` 取最近 N 条（升序）。用 PG 而非 SQLite 是为了多副本共享会话上下文。

### `stores/profile.py` — `PgProfileStore` / `InMemoryProfileStore`
`profile_beliefs` 表（信念 CRUD + `attribute_embedding` ANN 搜索）+ `user_profile_snapshots` 表（每用户一条 narrative，upsert）。内存版仅测试用。

### `stores/sqlite_history.py` — `SQLiteHistoryStore`
同步、`threading.Lock` 线程安全，自建两张表：
- `history`：记忆变更审计（memory_id、old/new_memory、event、actor_id 等）；
- `messages`：last-k 会话消息（单机场景的默认 message store）。

方法：`add_history/batch_add_history/get_history`、`save_messages/get_last_messages`（自动淘汰）、`delete_by_user`。

### `stores/memory.py` — `InMemoryVectorStore/EntityStore/MessageStore`
纯 Python 列表实现，仅测试与离线 demo 用。`search` 遍历算 `cosine_similarity`；`keyword_search` 为简单子串匹配；语义与 PG 版保持一致（含 `get_active_episode`）。

### PG 与 SQLite 分工
| 数据 | 后端 | 原因 |
|------|------|------|
| 向量记忆（L1/L2）、实体、信念、画像快照 | PostgreSQL + pgvector | 需要 HNSW ANN、全文检索、多副本共享 |
| 记忆变更审计 history、单机 last-k 消息 | SQLite | 本地轻量审计；多副本场景改用 PgMessageStore |

---

## 5. extraction/ — 抽取层（写入路径的 LLM 提取）

### `extraction/extractor.py` — `MemoryExtractor`（★）
`extract(messages, existing_memories, last_k_messages, custom_instructions, summary)`：
1. `parse_messages` 把消息展平为文本；
2. 给已有记忆编 fake id（"0","1"...），建 fake→UUID 映射；
3. `generate_additive_extraction_prompt` 组装 user prompt（含画像摘要、last-k、已有记忆、新消息、观察日期、语言要求）；
4. 一次 LLM 调用（system=`ADDITIVE_EXTRACTION_PROMPT`，`response_format=json_object`）；
5. `parse_extraction_response` 解析 JSON，把 LLM 引用的 fake id 映射回真实 UUID；
6. 返回候选记忆 `[{text, attributed_to, linked_memory_ids}]`。

注意：extractor 本身**不调用** entity_linker / deduplicator —— 两者由上层 `DesayMemory.add_result` 在其前后分别调用。

### `extraction/parser.py`
- `parse_messages(messages)`：→ `"role: content\n"` 扁平文本（既是 LLM 输入，也是检索前预处理）；
- `remove_code_blocks(content)`：剥 ```` ```json ```` 围栏与 `<think>` 标签；
- `extract_json(text)`：从夹杂杂文本的 LLM 输出中定位 JSON 边界；
- `parse_json_payload(response)`：清理 → `json.loads` → 失败重试；
- `parse_extraction_response(response)`：解析 `{"memory": [...]}` 结构；
- `normalize_extracted_memories(raw)`：兼容 string 或 dict 形式（text/fact/memory 字段），规范为 `{text, attributed_to, linked_memory_ids}`，丢弃空文本。

### `extraction/prompts.py`
| Prompt | 用途 |
|--------|------|
| `ADDITIVE_EXTRACTION_PROMPT`（约 498 行 system prompt） | 定义 Memory Extractor 角色：ADD-only、上下文丰富、自包含（代词具体化）、15-80 词、时间落地（相对→绝对）、数值精确、禁止编造/回声/污染；含 memory linking 规则与 12 个 few-shot 示例 |
| `generate_additive_extraction_prompt(...)` | 组装 user prompt 的各 section |
| `PROCEDURAL_MEMORY_SYSTEM_PROMPT` | procedural 分支：把对话总结为结构化执行步骤 |
| `VEHICLE_CUSTOM_INSTRUCTIONS` | 车辆场景可选 custom_instructions（保留空调温度、座椅、导航等精确值） |

### `extraction/entities.py` — 正则实体提取（无 NER 依赖）
四种类型：`QUOTED`（七种引号/书名号）、`IDENTIFIER`（x.y.z）、`PROPER`（英文大写开头短语 / 中文“名叫/去/在…”模式）、spaCy NER（可选叠加，加载 zh/en 模型）。`normalize_entity_text` 小写去空格做去重键；`_GENERIC_SINGLE/_GENERIC_CJK` 过滤泛词。入口 `extract_entities(text)` / `extract_entities_batch(texts)`。

### `extraction/entity_linker.py` — `EntityLinker`
- `link_memories(items, scope)`：对每条新记忆提取实体 → 实体文本嵌入 → `_upsert` 建立实体↔记忆关联；
- `_upsert` **两级消歧**：① `normalized_text` 精确匹配 → ② 向量 top-1 相似度 ≥ `entity_match_threshold`(0.95) → 复用；否则新建实体；无论哪种都合并 `linked_memory_ids`；
- `unlink_memory(memory_id)`：删除记忆时移除关联，实体无链接则删实体；
- `boosts_for_query(query, scope)`（检索期使用）：查询提取实体 → 实体向量在实体库搜索（top 500）→ 相似度 ≥ `entity_boost_min_similarity`(0.5) 的实体，对每条关联记忆给 `boost = similarity × 0.5 × memory_count_weight`（关联记忆越多权重递减）。

### `extraction/deduplicator.py`
- `memory_content_hash(text)`：MD5；
- `drop_duplicate_texts(items, existing_hashes)`：与「存储中已有 + 本批次已见」两组哈希比对，完全相同文本跳过，返回 `(保留项, 跳过数)`。策略为**纯 MD5 精确去重**（语义级去重交给 LLM prompt 前置完成）。

---

## 6. retrieval/ — 检索层（读取路径的打分与融合）

### `retrieval/retriever.py` — `MemoryRetriever`（★）
`search(query, scope, top_k, filters, threshold, explain)` 为九步管线（详见 §12）：预处理 → 向量化 → 语义 over-fetch（`max(top_k*4, 60)`）→ 关键词检索 → BM25 归一化 → 实体加权 → 候选合并 → 融合打分 → 格式化+冲突消解。
`neighbors_for_extraction(...)` 是轻量版（只做向量召回），服务写入路径的 Phase 1。

### `retrieval/scoring.py`
| 常量 | 值 |
|------|-----|
| `ENTITY_BOOST_WEIGHT` | 0.5 |
| `TEMPORAL_WEIGHT` | 0.15 |
| `TEMPORAL_HALF_LIFE_DAYS` | 30 |

| 函数 | 作用 |
|------|------|
| `cosine_similarity / distance_to_score` | 余弦相似度 / `max(0,1-distance)`（与 pgvector 对齐） |
| `get_bm25_params(query)` | 按查询词数返回 sigmoid 的 (midpoint, steepness)：短查询更陡，长查询更平 |
| `normalize_bm25(raw, mid, steep)` | `1/(1+exp(-steep*(raw-mid)))` 归一化到 [0,1] |
| `temporal_score(created_at, now)` | `0.15 * exp(-ln2·Δdays/30)`：30 天半衰期的时间新近度加分 |
| `score_and_rank(...)` | 融合公式（加权和归一化，非 RRF）：`combined = min((semantic + bm25 + entity + temporal) / max_possible, 1.0)`；门控：`semantic < threshold` 且 `bm25 ≤ 0` 的候选丢弃 |

### `retrieval/lemmatization.py`
`lemmatize_for_bm25(text)`：spaCy 词形还原（en/zh，去停用词，保留 -ing 形式）+ 正则 fallback（ASCII token + CJK 单字与二元组）合并去重，输出空格分隔 token 序列。写入时对记忆文本做同样的 lemmatize 存入 `text_lemmatized`，检索时对查询做同样处理，保证两端对齐（这是 BM25 命中的前提，尤其是中文在 PG `simple` 分词器下的匹配）。

### `retrieval/conflict_resolution.py`
偏好变更的检索期消解：
- `detect_update_expression(text)`：13 条中文更新表达正则（"改为/换成/不再喜欢/默认设置为/现在喜欢…"）；
- `extract_memory_key(text)`：11 条座舱属性模式 → 归一化 key（空调温度/座椅加热/音乐偏好/导航/音量/后视镜/车窗）；
- `extract_value(text)`：数值+单位或引号串；
- `resolve_conflicts(rows)`：按 `(user, memory_key)` 分组，组内按时间降序；**仅当存在显式 update 表达且新旧值不同**才把旧记忆标 `SUPERSEDES` 过滤；同 key 同值或无 update 表达 → `RELATED_TO` 全保留。只影响返回结果，不改库（历史可审计）。
- `annotate_metadata(row, result)`：给保留记忆补 `memory_key/is_current/supersedes` 注解。

---

## 7. layers/ — 分层记忆（L1/L2/L3 晋升）

### `layers/episodes.py` — `EpisodeBuilder`（L2）
- `judge(messages, new_facts, active, similarity, occurred_at)`：把「当前开放 episode 摘要 + embedding 相似度 + 新事实 + 最近对话」交给 LLM（`EPISODE_SYSTEM_PROMPT`），返回 `{continues, episode_summary, confidence}`；LLM 失败返回 None 不阻塞主流程；
- `episode_similarity(active, new_facts)`：新事实 embedding 平均池化后与 active episode 的余弦相似度（给 LLM 的数值线索）；
- `mean_pool(vectors)`：向量按列平均。

episode 聚合不是固定时间窗，而是**时间连续 + 语义连续**的 LLM 判断（话题/参与者/情境是否延续）。每个 `(tenant, user, occupant)` 范围最多一个 active episode（由 migration 005 的部分唯一索引强制）。

### `layers/distiller.py` — `ProfileDistiller`（L3）
- `distill(scope, cluster, episode_id)`：把事实簇（新存储记忆 + 向量近邻）交 LLM（`DISTILL_SYSTEM_PROMPT`）提取信念 JSON，逐条 `_apply_one` 落地；
- `_apply_one(raw, ...)`：校验 attribute/value/evidence_ids → 信念文本嵌入 → 在 `profile_beliefs` 做 top-1 近邻搜索（阈值 `profile_match_threshold=0.82`）→ 按 `decision` 分支：CREATE 插入 / CONFIRM 追加证据并 support_count+1 / REFINE、SUPERSEDE 旧信念置 `superseded`+`valid_to` 并插入新信念 / COEXIST 共存 / NOOP 跳过。stability 修正：LLM 建议 recurring 但证据只来自同一天 → 降级为 episode；
- `refresh_snapshot(scope)`：active 信念 → `assemble_narrative()` 生成叙事 → upsert 到 `user_profile_snapshots`；
- `profile_view(scope)`：返回 `ProfileView`（narrative + beliefs），供检索链路与 `/profile` API 使用；
- `_prefer_stability(old, new, support_count)`：`identity(2) > recurring(1) > episode(0)`。

### `layers/reranker.py` — `SemanticReranker`
查询路径组件：`select(query, candidates, profile, top_k)` 把候选（L1+L2，fake id + 层级 + 文本 + 分数）连同用户 L3 画像交给 LLM（`RERANK_SYSTEM_PROMPT`）做语义精排，返回 `{selected_ids, time_scope}`，映射回真实 ID；失败回退向量排序。读取 L3 画像作为上下文帮助 LLM 判断"当前偏好 vs 历史偏好"。

### `layers/prompts.py`
| Prompt | 用途 | 输出 |
|--------|------|------|
| `EPISODE_SYSTEM_PROMPT` | 判断新事实是否延续当前 episode | `{continues, episode_summary, confidence}` |
| `DISTILL_SYSTEM_PROMPT` | 从事实簇蒸馏信念（stability 三级 + decision 六种） | `{beliefs: [{subject, attribute, value, conditions, stability, decision, confidence, evidence_ids}]}` |
| `RERANK_SYSTEM_PROMPT` | 理解查询意图（时间范围/频率/who-what），结合画像精选 | `{selected_ids, time_scope}` |

### `layers/parser.py`
L2/L3 的轻量解析：`parse_object(response)`（复用 extraction 的 `parse_json_payload`）、`remap_ids(raw_ids, mapping)`（fake id → UUID）。这是所有 LLM 调用的通用模式：**LLM 只见 fake id，Python 负责映射与校验**。

---

## 8. api/ + services/ + providers/ — API 与外部服务

### `api/main.py`
`create_app(memory, settings)` 工厂：挂 lifespan、路由、CORS；注册全局异常处理器把 7 类 `DesayMemError` 映射到 HTTP 状态（400/404/500/503/502）；`_health_status` 中间件把失败的 /health 响应改写为 503。

### `api/dependencies.py`
`build_lifespan`：启动时依次 ① 取 Settings → ② 配置日志 → ③ `DesayMemory.from_settings()` → ④ `memory.prepare()` 建表 → ⑤ 把 settings/memory/`MemoryService` 挂到 `app.state`（单例）；关闭时释放连接。`get_service/get_app_settings` 从 request state 取依赖。

### `api/routes/memories.py` + `health.py`
端点见 §13。health 调 `service.health()`（DB 连通性），失败时置 `request.state.health_failed=True`。

### `services/memory_service.py` — `MemoryService`
薄 Facade：`add/search/get_all/delete/delete_all/history/get_profile/health` 逐一委托给 `DesayMemory` 对应 `*_result` 方法。存在意义是隔离 API 层与核心层、便于注入测试。

### `providers/llm/` 与 `providers/embedding/`
- `base.py`：两个 `Protocol` —— `complete(messages, response_format) -> str` 与 `embed(texts) -> list[vector]`，只保留系统所需最小接口（无 mem0 的 tool-calling/streaming 等能力）；
- `openai_compatible.py`：基于 `openai.AsyncOpenAI`，`base_url` 可指向任意 OpenAI 兼容服务（Qwen/DeepSeek/vLLM）。LLM 固定低温度采样并传 `enable_thinking=False`（vLLM 兼容）；Embedding 按 32 条分批、按 index 排序合并、严格校验数量与维度（不符抛 EMBED_005/006）。异常统一包装为 `LLMError/EmbeddingError`。无自定义超时/重试（交给 openai 库默认行为）。

---

## 9. migrations/ — 数据库迁移

| 文件 | 内容 |
|------|------|
| `001_initial.sql` | pgvector/pgcrypto 扩展；`memory_items` 表（VECTOR(1024)、`UNIQUE(tenant_id,user_id,content_hash)`、HNSW cosine 索引、GIN metadata 索引） |
| `002_session_entities.sql` | `memory_items` 加 `memory_type` 列；建 `session_messages`、`memory_entities`（实体唯一键 + HNSW） |
| `003_bm25.sql` | `memory_items` 加 `text_lemmatized` 列 + GIN 全文索引（BM25/FTS 路径） |
| `004_layers.sql` | `profile_beliefs`（stability/status CHECK、HNSW on attribute_embedding）+ `user_profile_snapshots` |
| `005_episode_integrity.sql` | 部分唯一索引：每 `(tenant,user,occupant)` 最多一条 active episodic_memory（L2 生命周期的 DB 级保证） |

数据表全景：PG 中 `memory_items`（L1+L2）、`memory_entities`、`session_messages`、`profile_beliefs`、`user_profile_snapshots`；SQLite 中 `history`、`messages`。

---

## 10. tests/ + scripts/ — 测试与工具脚本

### 测试策略
- **真实依赖测试**为主：conftest 的 `live_settings` 加载 `.env` 中的真实 LLM/Embedding/PG DSN（`search_threshold=0`），`session_memory`/`memory` fixture 每次测试前后 wipe 数据；
- **纯 mock 仅用于 L2/L3**：`test_layers.py` 用 `ScriptedLLM`（预设 JSON）+ `HashEmbedding`（哈希伪向量）覆盖 episode/distill/rerank 的分支逻辑；
- `api_client`：`httpx.ASGITransport` 内存直连 FastAPI，无需起服务。

### 主要测试文件
| 文件 | 覆盖 |
|------|------|
| `test_memory.py` | 真实 LLM 的偏好抽取/检索、用户与租户隔离、MD5 去重、删除权限 |
| `test_mem0_pipeline.py` | 七阶段 add、实体抽取/链接、九阶段搜索 BM25 命中、last-k、history 记录 |
| `test_layers.py` | L2/L3 全分支（ScriptedLLM + HashEmbedding） |
| `test_extractor.py` / `test_parser.py` / `test_deduplicator.py` | LLM 抽取结果、响应解析、MD5 与 mem0 对齐 |
| `test_conflict_resolution.py` | 纯逻辑：更新表达检测、key/value 提取、SUPERSEDES 判定、链式消解(22→26→24) |
| `test_scoring_and_dims.py` | 距离-分数换算与 pgvector 对齐 |
| `test_api.py`（integration） | HTTP 全流程：add→search→list→delete(跨用户 404)→delete_all(confirm) |
| `test_mem0_cockpit_flow.py`（scenarios） | 车载 10 步端到端：多乘员隔离、引号目的地实体、跨轮召回、`delete_all` 清理 |
| `test_no_mem0_dependency.py` / `test_logging.py` / `test_migration_sql.py` | 无 mem0 依赖 / 日志脱敏 / 迁移 SQL 结构 |

### scripts/ 一览
| 脚本 | 用途 |
|------|------|
| `baseline_demo.py` | 冒烟：写入→召回→列出 |
| `cockpit_simulator.py` | 5 轮车机多轮对话模拟（直连 / HTTP 两模式） |
| `run_yearlong_memory_test.py` / `run_yearlong_http_test.py` | 一年期数据集（365 天 JSONL）导入 + 5 条模糊查询的召回评测，本地 / HTTP 两模式，输出 JSON+MD 报告 |
| `generate_yearlong_cockpit_dataset.py` | 确定性生成 365 天数据集 + gold 评估标准 |
| `llm_proxy.py` | 独立 LLM 代理服务（隔离 API Key） |
| `live_cockpit_demo.py` | 真实 DashScope + 持久化的四步验证 |
| `compare_with_upstream.py` | 与 Mem0 OSS 定性对比 |
| `inspect_memories.py` | 运维：直接查 PG 落库记忆 |

---

## 11. 流程一：记忆添加（Add）端到端详解

入口：`POST /v1/memories` → `MemoryService.add()` → `DesayMemory.add_result()`。

以默认分支（`infer=True`、语义记忆）为例的七阶段流程：

```
客户端 POST /v1/memories
{ tenant_id, user_id, vehicle_id, occupant_id, session_id, scene,
  messages: [{role, content}...], infer: true }
        │
        ▼
┌─ Phase 0：上下文收集 ─────────────────────────────────────┐
│ _normalize_messages → 规范化消息                            │
│ parse_messages → 展平文本                                  │
│ build_session_scope + messages.get_last_messages           │
│   → 从 last-k 存储取最近 10 条对话（消解指代用）             │
│ profiles.get_snapshot → L3 画像叙事（若启用 profile）        │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ Phase 1：已有记忆检索 ───────────────────────────────────┐
│ MemoryRetriever.neighbors_for_extraction                   │
│   → 对话文本嵌入后在 memory_items 向量检索 top_k=10         │
│   → 作为 LLM 的"现有记忆"上下文（用于语义级去重与 linking）   │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ Phase 2：LLM 抽取（单次调用，ADD-only）───────────────────┐
│ MemoryExtractor.extract                                    │
│   system = ADDITIVE_EXTRACTION_PROMPT                       │
│   user   = 画像摘要 + last-k + 已有记忆 + 新消息 + 日期 + 语言 │
│   → LLM 返回 {"memory": [{text, attributed_to,             │
│              linked_memory_ids}]}（含语义级去重与链接判断）   │
│ parse_extraction_response → 清洗/解析/规范化                │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ Phase 3+4+5：嵌入收集 → MD5 去重 ─────────────────────────┐
│ store.existing_hashes → 用户全部已有 content_hash           │
│ + Phase 1 邻居的 hash → 去重集合                            │
│ drop_duplicate_texts → 纯文本 MD5 精确去重（批次内+库内）      │
│   （DB 端还有 UNIQUE(tenant,user,content_hash) 最后防线）    │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ Phase 6：批量持久化 ─────────────────────────────────────┐
│ _persist_texts:                                            │
│   embedding.embed(批量文本) → 1024 维向量                   │
│   每条构建 StoredMemory:                                   │
│     content_hash  = MD5(text)                              │
│     text_lemmatized = lemmatize_for_bm25(text) ← BM25 前提  │
│     metadata = {occurred_at, scene, ...}                   │
│   store.insert → pgvector memory_items（ON CONFLICT 跳过）   │
│   _record_history → SQLite history 表（ADD 事件审计）         │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ Phase 7：实体链接 ────────────────────────────────────────┐
│ EntityLinker.link_memories                                 │
│   extract_entities(正则+可选 spaCy) → 实体嵌入               │
│   _upsert: normalized 精确匹配 → 向量≥0.95 匹配 → 否则新建    │
│   合并 linked_memory_ids（memory_entities 表）              │
└──────────────────────────────────────────────────────────┘
        │
        ▼
    save_messages → last-k 消息存储（保留最近 10 条）
        │
        ▼
┌─ 分层演化 _evolve_layers（每次 add 后都触发）────────────────┐
│ ① L2 episode（enable_episodes）:                            │
│    get_active_episode → 当前开放 episode（若无则视为新建）     │
│    episode_similarity → 数值线索                            │
│    EpisodeBuilder.judge → LLM 判 {continues, summary, conf} │
│    continues=true  → 更新 active episode：改写摘要/嵌入/      │
│                      source_memory_ids 并集/时间边界         │
│    continues=false → 旧 episode 置 complete；新建 active      │
│                      episode（episodic_memory 类型入库）     │
│ ② L3 画像蒸馏（enable_profile）:                            │
│    cluster = 本轮新事实 + 向量近邻(≤12，排除 episodic)        │
│    ProfileDistiller.distill → LLM 提信念(decision/stability) │
│    _apply_one: 信念嵌入 → 0.82 阈值近邻匹配 →                │
│      CREATE/CONFIRM(+support)/REFINE/SUPERSEDE(旧置          │
│      superseded+valid_to)/COEXIST/NOOP                     │
│    refresh_snapshot → narrative upsert 到快照表               │
└──────────────────────────────────────────────────────────┘
        │
        ▼
    AddResult { memories, skipped_duplicates, extracted,
                episode, beliefs_applied }
```

另外两个 add 分支：
- **`memory_type=procedural_memory`**：跳过抽取，LLM 用 `PROCEDURAL_MEMORY_SYSTEM_PROMPT` 把对话总结为结构化过程记忆 → 嵌入 → 持久化 → 实体链接（同样走 `_evolve_layers`）；
- **`infer=false`**：不调 LLM，每条非 system 消息原文直存（MD5 哈希 + 嵌入 + 持久化 + 实体链接）。

**写路径关键点：LLM 从不 UPDATE/DELETE** —— 检索到的旧记忆只作为上下文供 LLM 判断，库里旧值仍保留（可审计），冲突消解发生在读取端。

---

## 12. 流程二：记忆检索（Search）端到端详解

入口：`POST /v1/memories/search` → `MemoryService.search()` → `DesayMemory.search_result()` → `MemoryRetriever.search()` 九步管线。

```
客户端 POST /v1/memories/search
{ tenant_id, user_id, query, top_k=5, filters }
        │
        ▼
┌─ Step 1：查询预处理 ──────────────────────────────────────┐
│ lemmatize_for_bm25(query) → 中英文词形还原/分词              │
│ EntityLinker.boosts_for_query → 提取查询实体 → 实体嵌入      │
│   → 实体库向量搜索 → {memory_id: boost} 加权表               │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ Step 2：查询向量化 ──────────────────────────────────────┐
│ embedding.embed([query]) → 1024 维查询向量                   │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ Step 3：向量语义检索（over-fetch）─────────────────────────┐
│ store.search: embedding <=> query_vec (HNSW cosine)         │
│ internal_limit = max(top_k*4, 60)                           │
│ WHERE tenant_id+user_id (+可选 filters)                     │
│ score = max(0, 1 - distance)                               │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ Step 4：关键词/FTS 检索 ──────────────────────────────────┐
│ store.keyword_search: to_tsvector('simple',                │
│   text_lemmatized) @@ plainto_tsquery(query_lemmatized)     │
│   + ts_rank_cd 排序（PG 全文检索路径）                       │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ Step 5：BM25 分数归一化 ──────────────────────────────────┐
│ get_bm25_params(按查询长度自适应) → normalize_bm25(sigmoid)  │
│ → [0,1]                                                     │
└──────────────────────────────────────────────────────────┘
        │
        ▌ Step 6：实体加权（Step 1 已算好）
        ▌
┌─ Step 7：候选合并 ────────────────────────────────────────┐
│ 语义结果为主集；BM25 结果按 id 并入                          │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ Step 8：多路融合打分 + 排序 ──────────────────────────────┐
│ score_and_rank:                                             │
│   combined = min( (semantic + bm25 + entity + temporal)     │
│                   / max_possible, 1.0 )                      │
│   temporal = 0.15·exp(-ln2·Δdays/30)  # 30 天半衰期        │
│   门控: semantic < threshold 且 bm25 ≤ 0 → 丢弃              │
│ → 按 combined 降序                                          │
└──────────────────────────────────────────────────────────┘
        │
        ▌ Step 9a：格式化（MemoryItem + score_details）
        ▌
┌─ Step 9b：冲突消解 ────────────────────────────────────────┐
│ resolve_conflicts:                                          │
│   按 (user, memory_key) 分组 → 时间降序                      │
│   检测显式更新表达（"改为/换成/不再…/现在…"）                 │
│   有 update 且值不同 → 旧记忆 SUPERSEDES 被过滤              │
│   （例："空调 22 度"→"空调 26 度"→"空调 24 度" 链式消解，      │
│     只返回最新 24 度；旧值仍在库中可审计）                     │
└──────────────────────────────────────────────────────────┘
        ▌ Step 9c：metadata 注解（memory_key/is_current/supersedes）
        ▼
    DesayMemory.search_result 后处理:
      _expand_episode_evidence → 若命中 L2 episode，按
        source_memory_ids 追加其 L1 事实证据进候选池
        (上限 fetch_k*2，标记 expanded_from_episode_id)
      distiller.profile_view → 加载 L3 画像
      SemanticReranker.select (enable_rerank) → LLM 结合画像
        对候选精排取 top_k；失败回退向量排序
        │
        ▼
    SearchResult { memories: [MemoryItem...], query, top_k,
                  profile: ProfileView }
```

说明：
- rerank 开启时 `fetch_k = max(top_k, 32)`（`rerank_candidate_limit`），先宽召回再 LLM 精选；
- 检索管线内的冲突消解（9b）处理**同类偏好的新旧值**；LLM 重排再结合画像进一步保证"当前有效优先"；
- `search` 的结果按 memory_type 可过滤（如只要 episodic 或只要 semantic）。

---

## 13. 附录：请求/响应模型与 HTTP 端点速查

### 端点列表（api/routes/memories.py + health.py）

| 方法 & 路径 | 请求 | 返回 | 说明 |
|---|---|---|---|
| `GET /health` | — | `HealthResult`（status/app/database/embedding_dims/llm_model/embedding_model） | DB 失败 → 503 |
| `POST /v1/memories` | `AddMemoryRequest` | 201 + AddResult | 添加记忆（见 §11） |
| `POST /v1/memories/search` | `SearchMemoryRequest` | SearchResult | 检索记忆（见 §12） |
| `GET /v1/users/{user_id}/memories` | query: tenant_id 等 + limit(1-500) | ListResult | 列出用户记忆 |
| `GET /v1/users/{user_id}/memories/{memory_id}/history` | query: tenant_id | HistoryResult | 变更历史（带 scope 校验，跨用户 404） |
| `DELETE /v1/users/{user_id}/memories/{memory_id}` | query: tenant_id | DeleteResult | 删除单条 |
| `DELETE /v1/users/{user_id}/memories` | query: tenant_id, confirm | DeleteAllResult | 全量删除（需 confirm=true） |
| `GET /v1/users/{user_id}/profile` | query: tenant_id | ProfileView | L3 用户画像 |

### 关键请求模型
- `AddMemoryRequest`：`user_id`（必填）、`tenant_id`(默认 "default")、`vehicle_id`、`occupant_id`(默认 "primary")、`session_id`、`scene`、`source`(默认 "conversation")、`messages: list[ChatMessage]`、`metadata`、`infer`(默认 true)、`memory_type`、`prompt`；
- `SearchMemoryRequest`：`user_id`、`query`、`top_k`(1-50, 默认 5)、`filters`（vehicle/occupant/session/scene/source/memory_type）。

---

## 附：一图总结

```
                      ┌──────────────────── HTTP API (FastAPI) ────────────────────┐
                      │  /v1/memories    /v1/memories/search    /v1/users/...     │
                      └───────────────┬────────────────────────────┬───────────────┘
                                     │ MemoryService (Facade)      │
                                     ▼                             ▼
        ╔══════════════════════ DesayMemory (core/memory.py) ══════════════════════╗
        ║  ADD 链路                                SEARCH 链路                    ║
        ║  last-k 上下文 → 邻居召回 → LLM ADD-only   lemma → embed → 向量 over-fetch ║
        ║  抽取 → MD5 去重 → 嵌入 → 落库 → 实体链接   → BM25 → 实体加权 → 融合打分   ║
        ║  → L2 episode 判断 → L3 信念蒸馏           → 冲突消解 → L2 证据展开      ║
        ║                                            → LLM 精排 (带 L3 画像)      ║
        ╚═════════════════════════════════════════════════════════════════════════╝
              │                │                │                  │
              ▼                ▼                ▼                  ▼
      PostgreSQL+pgvector   SQLite        外部 LLM/Embedding   正则/规则模块
      memory_items(L1/L2)   history       (OpenAI 兼容)       实体提取/冲突消解
      memory_entities       messages                          /词形还原
      session_messages
      profile_beliefs(L3)
      user_profile_snapshots
```

L1（原子事实）→ 每次写入后由 `EpisodeBuilder` 判断归入或新开 L2（情节），再由 `ProfileDistiller` 把事实簇蒸馏为 L3（信念/画像）；检索时三层协同：向量召回 L1+L2，L2 命中展开 L1 证据，L3 画像辅助 LLM 精排 —— 旧值通过冲突消解在读取端过滤，库里永不删除，全程可审计。
