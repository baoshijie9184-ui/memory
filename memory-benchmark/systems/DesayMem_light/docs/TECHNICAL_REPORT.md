# DesayMem Light 技术报告

> 代码目录：`/data/pengshuang/memory-benchmark/systems/DesayMem_light`
> 数据目录：`/data/pengshuang/memory-benchmark/data/desaymem_light/`
> 报告日期：2026-09-10（迁移版本 v12，38 项单测通过）

---

## 1. 系统定位与总体架构

DesayMem Light 是面向**云端高并发车机场景**的轻量化分层记忆系统，融合了四个参考系统的核心思想：

| 来源 | 借鉴点 | 落地模块 |
|---|---|---|
| LightMem | 多级记忆分层 | L0/L1/L2/L3 架构 |
| Mem0 | ADD-only 事实抽取 | `Mem0AdditiveExtractor` |
| StructMem | 跨事件洞察 | `StructMemStyleSynthesizer` |
| TiMEM | 用户画像增量更新 | `TiMemStyleProfileUpdater` |

**记忆分层模型**：

```
L0  short_term_memories   短期记忆（滚动窗口 100 条/人，同步 embed，202 后立即可检）
L1  memory_items(fact)    原子事实（LLM 抽取）
L2  memory_items(event)    事件（facts 确定性聚合）
L2.5 memory_items(cross_event)  跨事件洞察（≥2 个 event 归纳）
L3  profile_items + profile_snapshots  用户画像（增量操作语义）
```

**数据流水线**（202 Accepted 异步）：

```
POST /v1/messages
  ├─ 同步：embed 消息 → session_messages 落库 + short_term_memories 写入 + trim → enqueue(session_segment) → 202
  └─ 异步 worker 轮询 memory_jobs：
       session_segment   → LightMemTopicSegmenter 切段（余弦相似度 <0.55 或 token>2000 断开）
       fact_extract      → Mem0AdditiveExtractor（prompt v2：抽取指令式偏好）→ 去重入库
       event_build       → DeterministicEventBuilder（零 LLM，facts 排序拼接）
       cross_event       → 攒 10 个 event 触发 → StructMemStyleSynthesizer 归纳
       profile_update    → TiMemStyleProfileUpdater（ADD/CONFIRM/COEXIST/SUPERSEDE/NOOP）

POST /v1/memories/search（1 次 embedding，0 次 LLM）
  fact / event / cross_event 三路向量检索 + short_term 第 4 路 + profile 快照
  → agent_context 拼装（[用户画像]→[近期对话]→[跨事件洞察]→[事件]→[事实]，token 预算截断）
```

**三级一致性**：PG 是事实源 → PG 触发器写 `json_outbox`（CDC）→ Mirror Worker 消费投影为 JSON 文件（原子写）→ 前端只读 JSON 文件。

---

## 2. 代码结构：每个文件的用途

```
DesayMem_light/
├── src/desaymem_light/
│   ├── domain/                    领域层（纯模型，无 IO）
│   │   ├── enums.py              全部字符串枚举：MessageRole/MemoryType(fact,event,cross_event)/
│   │   │                          MemoryStatus(active,superseded,stale,deleted)/JobStatus(ready,running,
│   │   │                          completed,retry,dead)/ProfileAction(ADD,CONFIRM,COEXIST,SUPERSEDE,NOOP)
│   │   ├── models.py             全部 DTO：Message/Mtopic/MemoryRecord/SearchRequest/SearchResult/
│   │   │                          ShortTermHit/ProfileItemRecord/各 *Request/*Result（Pydantic extra=forbid）
│   │   ├── errors.py             异常体系：DesayMemError→ConfigurationError/ContractError/ConcurrencyConflictError
│   │   └── text.py               canonical_text()（NFKC+casefold+空白折叠）/ content_hash()（SHA-256）
│   ├── contracts/                端口层（Protocol，定义持久化与模块边界）
│   │   ├── repositories.py      MessageRepo/ShortTermMemoryRepo/TopicRepo/MemoryRepo/ProfileRepo/
│   │   │                          JobRepo/AuditRepo/UsageRepo/CheckpointRepo/UnitOfWork（聚合全部）
│   │   ├── modules.py           算法模块协议：TopicSegmenter/FactExtractor/EventBuilder/
│   │   │                          CrossEventSynthesizer/ProfileUpdater/MemoryRetriever
│   │   ├── providers.py         外部依赖协议：ChatModel.generate_json/EmbeddingModel.embed/TokenCounter
│   │   └── mirror.py            JSON 镜像协议：MirrorProjector.apply/reconcile
│   ├── application/             应用层（管线编排）
│   │   ├── ingest_service.py    IngestService.ingest()：embed→落库→short_term 写入→trim→enqueue→commit
│   │   ├── session_pipeline.py SessionPipeline.process()：pending 消息→切段→逐 topic 入库+enqueue(fact)
│   │   ├── memory_pipeline.py  MemoryPipeline.process_fact()（抽取+hash 去重+evidence）
│   │   │                          /process_event()（构建+contains 关系+tags+enqueue(cross_event)）
│   │   ├── insight_pipeline.py  InsightPipeline.process_cross_event()：checkpoint 增量取 event→质心
│   │   │                          找历史相关→token 预算打包→synthesize→derivation_key 去重入库
│   │   └── profile_pipeline.py  ProfilePipeline.process()：checkpoint 增量 event→当前画像→LLM 操作序列
│   │                              →ADD/CONFIRM/SUPERSEDE/COEXIST 落库→快照→checkpoint 推进
│   ├── modules/                 算法实现层（可插拔）
│   │   ├── session/lightmem_segmenter.py   余弦相似度+token 阈值切段，末尾 pending
│   │   ├── fact/mem0_extractor.py          prompt_version 机制（v1/v2 可切换）+ EXISTING_FACTS 注入
│   │   ├── event/deterministic_builder.py  零 LLM 确定性拼接（排序 facts→[时间窗] bullet 列表）
│   │   ├── cross_event/structmem_synthesizer.py  ≥2 event 硬约束校验（防幻觉引用）
│   │   ├── profile/timem_updater.py         5 种操作类型校验（编号引用必须存在）
│   │   └── retrieval/hybrid_retriever.py    四路检索+profile+agent_context（见 §5）
│   ├── adapters/                适配器层（真实 IO）
│   │   ├── postgres/            pool.py（psycopg AsyncConnectionPool）/ repositories.py（9 个仓储的
│   │   │                          PG 实现）/ unit_of_work.py（事务聚合）/ migrations.py（发现+应用）
│   │   │                          / outbox.py（json_outbox 消费）
│   │   ├── sqlite/              repositories.py（history + messages_cache 兼容层）/ migrations.py
│   │   ├── json_mirror/file_projector.py  AtomicJsonTableProjector（镜像投影核心）
│   │   ├── llm/qwen_openai.py           vLLM 客户端（response_format=json_object）
│   │   ├── llm/json_utils.py             宽松 JSON 解析（剥 fence/找 {..} 区间）
│   │   ├── llm/qwen_tokenizer.py         本地 Qwen tokenizer 精确计数
│   │   ├── embedding/bge_m3.py            TEI 客户端（1024 维强制，1200 字符截断，batch=32）
│   │   └── migrations.py                 跨库迁移发现（按版本号排序，校验连续性）
│   ├── workers/
│   │   ├── memory_worker.py     job_type→pipeline 分发表 + claim/complete/fail 重试（2^n 退避，5 次后 dead）
│   │   └── mirror_worker.py     json_outbox claim→projector.apply→ack
│   ├── bootstrap/               组装层
│   │   ├── settings.py          RuntimeSettings（env 优先）+ 校验（embedding_dims 必须 1024）
│   │   ├── registry.py          PluginRegistry（options 隔离注入，重复/未知名报错）
│   │   ├── container.py         依赖组装（llm→fact/cross/profile，embedding→segmenter/retriever）
│   │   ├── builtins.py          注册 3 provider + 7 module 插件
│   │   └── runtime.py           build_runtime()：Settings→YAML→Pool→Registry→Plugins→Pipelines→Services
│   ├── api/
│   │   ├── models.py            MessageIn/MessageAccepted/ErrorResponse（extra=forbid）
│   │   ├── app.py               create_app：4 端点 + 统一 422/500 错误形状
│   │   └── main.py              uvicorn 入口 + readiness 探针（schema version >= 12）
│   └── cli.py                   5 个 CLI 入口：api_main/migrate_main/worker_main/
│                                mirror_worker_main/preflight_main
├── migrations/postgres/001-012  PG schema（见 §4）
├── migrations/sqlite/001-004    SQLite schema
├── prompts/                     fact/v1,v2 + profile/v1 + cross_event/v1（版本化 prompt 资产）
├── config/cloud-test.yaml       插件选择 + 每插件 options（含 fact prompt_version: v2）
├── tests/                       38 项单测（14 个文件，见 §7）
└── docs/                        ARCHITECTURE_V1 / DATA_AND_JSON_MIRROR_V1 / CLOUD_TEST_DEPLOYMENT_SUMMARY 等
```

---

## 3. 分层原理

### 3.1 六边形架构（端口-适配器）

`domain`（纯模型）← `contracts`（Protocol 定义端口）→ `adapters`（PG/SQLite/HTTP 实现）。`application` 层只依赖 contracts，测试可用内存 fake 替换（如 `tests/unit/test_retriever.py` 的 fake Uow 使 38 项测试无需真实 PG）。

### 3.2 依赖组装（bootstrap）

`build_runtime()` 顺序：`RuntimeSettings`（env，校验 dims==1024）→ `load_app_config`（cloud-test.yaml）→ `create_pool` → 注册 3 provider（qwen_openai_compatible / bge_m3_http / qwen_tokenizer）→ 注册 7 模块插件 → `build_selected_plugins` 按 YAML 选择实例并注入依赖 → 实例化 IngestService + 4 条 Pipeline。

**插件 options 隔离**：`config.options_for()` 返回深拷贝——修改 lightmem_v1 的阈值不影响 deterministic_v1（有测试断言此行为）。

### 3.3 异步管线与幂等

- 每个请求 202 Accepted，业务在 worker 中异步执行。
- `memory_jobs` 有 `idempotency_key` 唯一约束（job_type+key），重试不重复入队。
- `claim()` 用 `locked_by + locked_at` 抢占，批次默认 10 个；失败按 `2^attempts` 秒退避，5 次后 `dead`。
- 各管线用 `checkpoint` 表（cross_event_checkpoints / profile_checkpoints）记录增量处理位点，重启后从断点续跑。

### 3.4 202 即可检的 L0 短期记忆

`IngestService.ingest()` 在**同一事务**内完成：消息落库 + TEI embed（~20ms）+ short_term 写入 + trim（保留最近 100 条/人，跨 session）。因此检索无需等 worker 蒸馏——解决"指令式请求（播放周杰伦）当轮无法召回"的断链问题。fact 层 prompt v2 同步修复了蒸馏层丢偏好问题。

---

## 4. 数据库设计（PostgreSQL，schema `desaymem_light`，26 表）

### 4.1 会话域

| 表 | 用途 | 关键列/索引 |
|---|---|---|
| `session_messages` | 原始消息（唯一事实入口） | scope 五元组+sequence_no 唯一、request_id 幂等、buffer_status(pending/segmented/failed)、pending 部分索引 |
| `topic_segments` | 切段结果 | start/end_sequence_no、boundary_reason(semantic/token_limit/session_end/manual)、event_status |
| `topic_segment_messages` | 段-消息关联 | PK(topic_segment_id, ordinal)，message_id 唯一 |

### 4.2 记忆域（memory_items 为核心）

| 表 | 用途 | 关键设计 |
|---|---|---|
| `memory_items` | fact/event/cross_event 三类记忆 | VECTOR(1024) HNSW cosine 索引、search_document TSVECTOR 全文、derivation_key 部分唯一（幂等）、fact+active 的 content_hash 唯一（去重）、valid_from/to 时效性 |
| `memory_evidence` | 记忆→来源溯源（topic/message） | (memory_id, source_type, source_id) 唯一 |
| `memory_relations` | 记忆间关系 | contains/summarizes/related_to/supersedes |
| `memory_entities` | 实体归一化 | normalized_text 唯一 + HNSW |
| `memory_entity_links` | 记忆↔实体 | 复合 PK |
| `tag_definitions` | 租户级标签 | (tenant_id, normalized_name) 唯一 |
| `memory_tag_links` | 记忆↔标签 | 含 source_id 审计来源 |

### 4.3 画像域

| 表 | 用途 |
|---|---|
| `profile_items` | 画像属性（attribute=value），confirmation_count 确认计数，normalized_hash 去重，valid_from/to 时效 |
| `profile_item_evidence` | 画像←event 证据链（support/contradict 角色） |
| `profile_item_relations` | supersedes / coexists_with |
| `profile_snapshots` | 版本化快照（version 递增），当前画像即最新版 |
| `profile_snapshot_items` | 快照↔属性关联（可复现历史画像） |

### 4.4 编排与观测域

| 表 | 用途 |
|---|---|
| `memory_jobs` | 异步任务队列：status(ready/running/completed/retry/dead)、attempts/max_attempts、next_run_at、locked_by |
| `cross_event_checkpoints` | 跨事件增量位点（last_event_occurred_at/id） |
| `profile_checkpoints` | 画像更新位点（last_snapshot_version + last_event） |
| `memory_audit_events` | 全部记忆变更审计（ADD/UPDATE/SUPERSEDE/DELETE...，before/after JSONB） |
| `llm_usage` | 每次 LLM 调用计量（purpose/model/tokens/latency/status）——成本观测 |

### 4.5 基础设施域（JSON 镜像 CDC）

| 表 | 用途 |
|---|---|
| `json_mirror_registry` | 注册哪些表要镜像（主键列数组），enabled 开关 |
| `json_outbox` | 行变更事件队列：operation(INSERT/UPDATE/DELETE)、row_data、row_version 单调递增、applied_at、locked_at/by（011 加，支持并发 claim） |
| `schema_migrations` | 版本 1-12，checksum 防篡改 |
| `json_mirror_registration_issues`（视图） | 检测注册与物理表/触发器不一致 |

`short_term_memories`（012 新增）：id/五元组 scope/message_id(FK→session_messages CASCADE)/role/content/embedding(VECTOR 1024)/occurred_at；scope 复合索引 + HNSW；每行入库即触发 outbox→镜像。

**外键级联策略**：删除上游（session_messages）→ CASCADE 清理下游证据；引用计数型（memory_relations.target）用 RESTRICT 防悬空。

**SQLite 侧**（history.db）：`history`（记忆变更历史，兼容 Mem0 契约）+ `messages_cache`（带 expires_at 的消息缓存）+ 同款 outbox 三件套。

---

## 5. 检索原理（FilteredVectorRetriever）

**单次调用成本固定：1 次 embedding + 0 次 LLM**（有测试断言）。

1. query → TEI embed → 1024 维向量
2. 四路并发向量检索（HNSW cosine）：
   - `memories.search(memory_type=fact/event/cross_event)`——带 scope 过滤 + 可选 session_id/tags（HAVING 精确匹配）/时间窗/vehicle_only
   - `short_term_memories.search`——JOIN session_messages 取原文，vehicle_id 用 `IS NOT DISTINCT FROM` 兼容 NULL
3. `profiles.current_snapshot` 取画像摘要
4. `_context()` 按序拼装 `[用户画像]→[近期对话](short_term_top_k=5)→[跨事件洞察]→[事件]→[事实]`，超 `context_token_limit`(1800) 的段丢弃——保证 agent_context 有硬预算

**为什么三路分开查而非一次**：memory_items 虽是单表，但按 memory_type 分路可对每路单独 top_k + 展示分组，且 events 的检索内容是聚合文本（与 fact 原子文本 embedding 空间不同）。

---

## 6. JSON 镜像：文件清单与含义

**机制**：PG 触发器 `capture_json_outbox()` 捕获 21 张业务表变更 → `json_outbox` → Mirror Worker claim → `AtomicJsonTableProjector.apply()` 按 `row_version` 判序（旧事件忽略，乱序/重复安全）→ tmpfile+`os.replace` 原子替换 → `checkpoint` 表记录位点。

**文件格式**（每表一个文件）：

```json
{
  "table_version": 12,
  "rows": {
    "<pk>": { "data": { "…整行数据（含 embedding 数组）…": }, "version": 42 }
  }
}
```

`data/desaymem_light/json_mirror/postgres/` 下 12 个文件（即当前有数据的表）：

| JSON 文件 | 对应表 | 代表什么 |
|---|---|---|
| `session_messages.json` | session_messages | 全部原始对话消息——前端"接入层 L0" |
| `short_term_memories.json` | short_term_memories | 短期记忆滚动窗口（含向量） |
| `topic_segments.json` | topic_segments | 切段结果与状态机 |
| `topic_segment_messages.json` | topic_segment_messages | 段-消息映射 |
| `memory_items.json` | memory_items | L1/L2/L2.5 全部记忆（fact/event/cross_event） |
| `memory_evidence.json` | memory_evidence | 记忆溯源链接 |
| `memory_relations.json` | memory_relations | contains/summarizes 关系 |
| `tag_definitions.json` + `memory_tag_links.json` | 标签 | 租户标签体系与挂载 |
| `memory_jobs.json` | memory_jobs | 任务队列全历史（排查管线卡点入口） |
| `memory_audit_events.json` | memory_audit_events | 记忆变更审计 |
| `llm_usage.json` | llm_usage | LLM 成本计量 |

（profile_*、entity、checkpoint 等表已注册镜像但暂无数据，产生数据后自动出现文件。）

前端（`:20147`）**只读这些 JSON** 实现零侵入观测：表行数徽章实时变化、点表名看全行、任务队列看管线进度。

---

## 7. 测试与运维

**38 项单测覆盖**（14 文件）：域模型校验（time 顺序/extra 拒绝）、配置隔离、切段 pending 语义、Mem0 单次 LLM 调用、event 确定性+tags 去重、cross_event 防幻觉（不存在编号→ContractError）、profile 多值 COEXIST、**检索 1 embedding 0 LLM**、UoW 提交/回滚、迁移连续性（PG 1-12/SQLite 1-4）+22 表镜像注册、JSON 投影原子性+旧版本忽略、SQLite 车辆隔离、适配器 JSON 宽松解析、API 202/422 形状、插件注册表。

**部署拓扑**（当前运行）：
- 20140 vLLM（Qwen3-32B）/ 20141 TEI（bge-m3）
- 20148 DesayMem Light API / 20149 独立 PG 实例
- 20147 前端（静态托管 + JSON 镜像读取 + API/LLM 反向代理）
- 三进程：uvicorn API + memory worker + mirror worker（`PYTHONPATH=src`，cli.py 各入口）

**已知限制**（docs/KNOWN_LIMITATIONS_V1.md）：event 确定性表达力弱、cross_event/profile 最终一致（异步延迟）、无 LLM rerank、JSON 磁盘开销（1024 维向量 ~4KB/条）、SQLite 单写者。

**v12 实测结论**（tenant-e2e）：消息 202 后立即检索命中 short_term（score 0.71）；切段蒸馏产出"用户喜欢听周杰伦的歌"+"用户偏好空调温度22度"（prompt_version=v2）；最终检索三路召回齐全。
