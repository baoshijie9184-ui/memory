# 设计 Step 8：可插拔代码架构

## 1. 目标

每个记忆子模块都能单独替换，不影响 API、数据库事务和其他层级。

V1 不实现复杂的动态插件市场，只采用：

```text
稳定接口（Protocol/ABC）
+ 显式实现注册表
+ 配置选择实现
+ 统一契约测试
```

这种方式简单、可调试，也为后续接入新模型和新算法留出位置。

## 2. 目录结构

```text
DesayMem_light/
  src/desaymem_light/
    domain/
      models.py              # Message、Topic、Memory、Profile 等纯数据对象
      enums.py               # 状态、操作、关系类型
      errors.py              # 稳定错误类型
    contracts/
      modules.py             # 各记忆模块接口
      providers.py           # LLM、Embedding、Tokenizer 接口
      repositories.py        # PostgreSQL/SQLite 仓储接口
      mirror.py              # Mirror 接口
    modules/
      session/
        lightmem_segmenter.py
      fact/
        mem0_extractor.py
      event/
        deterministic_builder.py
      cross_event/
        structmem_synthesizer.py
      profile/
        timem_updater.py
      retrieval/
        hybrid_retriever.py
    adapters/
      llm/qwen_openai.py
      embedding/bge_m3.py
      postgres/
      sqlite/
      json_mirror/
    application/
      ingest_service.py
      memory_pipeline.py
      search_service.py
      mirror_service.py
      unit_of_work.py
    workers/
      memory_worker.py
      insight_worker.py
      mirror_worker.py
      reconcile_worker.py
    api/
      routes/
      schemas.py
      dependencies.py
    prompts/
      fact/v1.txt
      cross_event/v1.txt
      profile/v1.txt
    bootstrap/
      registry.py
      container.py
      settings.py
  migrations/postgres/
  migrations/sqlite/
  tests/contracts/
  tests/unit/
  tests/integration/
  docs/
```

依赖方向固定为：

```text
API/Worker -> Application -> Contracts + Domain
Modules     -> Contracts + Domain
Adapters    -> Contracts + Domain
Domain      -> 不依赖任何外层代码
```

模块之间禁止直接互相 import 具体实现。

## 3. 核心模块接口

### 3.1 TopicSegmenter

```python
class TopicSegmenter(Protocol):
    async def segment(self, request: SegmentRequest) -> SegmentResult: ...
```

输入：有序消息、token 上限、会话状态。  
输出：Topic 边界和原因。  
V1 实现：`lightmem_v1`，使用 BGE-M3，不使用生成式 LLM。

### 3.2 FactExtractor

```python
class FactExtractor(Protocol):
    async def extract(self, request: FactExtractionRequest) -> FactExtractionResult: ...
```

输入：Topic 快照、最近消息、已有 Fact 候选。  
输出：原子 Fact、实体提示、模型 usage。  
V1 实现：`mem0_additive_v1`。

Extractor 只返回结果，不直接写数据库。

### 3.3 EventBuilder

```python
class EventBuilder(Protocol):
    def build(self, request: EventBuildRequest) -> EventBuildResult: ...
```

输入：Topic、Fact、实体和时间。  
输出：Event 内容、来源顺序和继承 Tags。  
V1 实现：`deterministic_v1`，0 次 LLM。

### 3.4 CrossEventSynthesizer

```python
class CrossEventSynthesizer(Protocol):
    async def synthesize(self, request: CrossEventRequest) -> CrossEventResult: ...
```

输入：编号后的 seed/history Events。  
输出：`should_create、summary、supporting_numbers、usage`。  
V1 实现：`structmem_style_v1`。

候选检索和数据库写入不属于该插件，防止插件越权读取其他用户数据。

### 3.5 ProfileUpdater

```python
class ProfileUpdater(Protocol):
    async def update(self, request: ProfileUpdateRequest) -> ProfileUpdateResult: ...
```

输入：当前快照、相关画像项、编号后的新证据。  
输出：画像操作、summary、usage。  
V1 实现：`timem_style_v1`。

### 3.6 MemoryRetriever

```python
class MemoryRetriever(Protocol):
    async def search(self, request: SearchRequest) -> SearchResult: ...
```

V1 实现：`hybrid_v1`，执行九步检索。身份硬过滤由 application 在调用时强制传入，repository 再次校验。

### 3.7 MirrorProjector

```python
class MirrorProjector(Protocol):
    async def apply(self, event: MirrorEvent) -> MirrorResult: ...
    async def reconcile(self, table: MirrorTable) -> ReconcileResult: ...
```

V1 实现：`outbox_json_v1`。Canonical serializer 单独定义接口，方便未来增加压缩副本，但 V1 的严格 JSON 镜像不可省略列。

## 4. Provider 接口

模型模块不能直接使用 HTTP 客户端：

```python
class ChatModel(Protocol):
    async def generate_json(self, request: ChatRequest) -> ChatResult: ...

class EmbeddingModel(Protocol):
    dimensions: int
    async def embed(self, texts: list[str], purpose: str) -> EmbeddingResult: ...

class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...
```

V1 Adapter：

```text
ChatModel      = qwen_openai_compatible
EmbeddingModel = bge_m3_http
TokenCounter   = qwen_tokenizer
```

以后更换模型只替换 Adapter 和配置，不修改记忆模块。

## 5. Repository 与事务

```python
class UnitOfWork(Protocol):
    memories: MemoryRepository
    messages: MessageRepository
    profiles: ProfileRepository
    jobs: JobRepository
    audit: AuditRepository
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
```

规则：

- 只有 Application Service 可以开启和提交事务。
- Module 不能持有数据库连接。
- Repository 负责 scope 过滤，调用方不能绕过 tenant/user 约束。
- PostgreSQL 是核心 Repository 实现。
- SQLite 只实现 `HistoryRepository` 和 `MessageCacheRepository`。
- JSON Mirror 不实现 Repository，禁止业务代码从 JSON 读取记忆。

## 6. Pipeline 编排

### IngestService

```text
接收消息 -> 幂等写 session_messages -> 调用 TopicSegmenter
          -> 封包时创建 fact_extract Job
```

### MemoryPipeline

```text
读取 Topic
 -> FactExtractor
 -> 应用层校验/去重/embedding
 -> 单事务写 Fact/Evidence/Audit
 -> EventBuilder
 -> 单事务写 Event/Relation/Tag
```

### InsightPipeline

```text
Cross-Event Job
 -> 应用层检索候选
 -> CrossEventSynthesizer
 -> 校验并持久化
 -> 创建 Profile Job
 -> ProfileUpdater
 -> 校验并持久化
```

### SearchService

```text
校验身份 -> MemoryRetriever -> 读取最新 Profile
         -> ContextAssembler -> SearchResult
```

编排器只依赖接口，所以任一算法模块可以单独替换。

## 7. 显式插件注册

`bootstrap/registry.py` 维护允许使用的实现：

```python
MODULES = {
    "topic_segmenter": {"lightmem_v1": LightMemTopicSegmenter},
    "fact_extractor": {"mem0_additive_v1": Mem0AdditiveExtractor},
    "event_builder": {"deterministic_v1": DeterministicEventBuilder},
    "cross_event": {"structmem_style_v1": StructMemStyleSynthesizer},
    "profile_updater": {"timem_style_v1": TiMemStyleProfileUpdater},
    "retriever": {"hybrid_v1": HybridRetriever},
    "mirror": {"outbox_json_v1": OutboxJsonProjector},
}
```

配置仅允许选择注册过的名称，不允许从环境变量导入任意 Python 路径，避免安全和部署问题。

## 8. 配置结构

```yaml
modules:
  topic_segmenter: lightmem_v1
  fact_extractor: mem0_additive_v1
  event_builder: deterministic_v1
  cross_event: structmem_style_v1
  profile_updater: timem_style_v1
  retriever: hybrid_v1
  mirror: outbox_json_v1

providers:
  llm: qwen_openai_compatible
  embedding: bge_m3_http

module_options:
  lightmem_v1:
    sensory_tokens: 512
    batch_tokens: 2000
  structmem_style_v1:
    trigger_count: 10
    max_input_tokens: 6000
  timem_style_v1:
    max_input_tokens: 6000
```

密钥和服务地址仍通过环境变量注入；算法参数使用版本化配置文件，随代码发布。

## 9. 稳定数据契约

模块输入输出使用 Pydantic DTO，不传数据库 ORM 对象。所有 DTO 包含：

```text
schema_version
request/job ID
完整 scope
source IDs
model/prompt version（涉及模型时）
```

模块输出先由 Application 校验，再写数据库。未知字段默认拒绝，避免模型输出悄悄改变数据结构。

## 10. Prompt 管理

- Prompt 存在独立版本文件，不写在 Python 大字符串中。
- `prompt_version` 采用内容 SHA-256 或发布版本。
- 任务创建时冻结 prompt_version；重试使用同一版本。
- Prompt 升级不原地重算历史记忆，需要显式 reprocess Job。

## 11. Job 接口

所有异步模块统一使用 `memory_jobs`：

```text
job_type: fact_extract/event_build/cross_event/profile_update/sqlite_project
plugin_name
plugin_version
payload_schema_version
idempotency_key
status
attempts/max_attempts
next_run_at
last_error
```

Worker 按 `job_type` 找到对应 handler，再由容器注入当前插件实现。算法替换后，旧任务仍按任务中冻结的插件版本执行；已移除版本必须先排空任务。

## 12. API 边界

V1 对外接口保持与内部实现无关：

```text
POST /v1/messages
POST /v1/sessions/{session_id}/flush
POST /v1/memories/search
GET  /v1/memories/{id}
GET  /v1/memories/{id}/lineage
GET  /v1/profiles/current
GET  /v1/jobs/{id}
GET  /v1/mirror/status/{outbox_id}
GET  /health/live
GET  /health/ready
GET  /health/mirror
```

API 响应只使用公共 schema，不暴露具体插件类名；调试字段中可返回版本信息。

## 13. 可插拔验收

每类插件必须通过相同 contract tests：

- 输入 schema 和完整 scope；
- 输出 schema 与来源引用合法性；
- 幂等重试；
- 超时、空结果和异常语义；
- token/usage 可观测；
- 禁止直接数据库写入；
- 替换为 fake plugin 后 Pipeline 无需修改。

集成测试至少提供：

```text
FakeChatModel
FakeEmbeddingModel
FakeTopicSegmenter
FakeFactExtractor
FakeCrossEventSynthesizer
FakeProfileUpdater
InMemoryRepositories
```

这样单元测试不依赖云端 Qwen、BGE 或数据库。

## 14. V1 克制范围

- 不使用 Python entry_points 动态扫描第三方包。
- 不允许插件自行建表或执行迁移。
- 不建立跨进程事件总线，先复用 PostgreSQL `memory_jobs`。
- 不拆成多个独立微服务；API 与 Worker 可同镜像、不同启动命令。
- 不为每个算法建立独立数据库表，统一使用稳定领域模型。
