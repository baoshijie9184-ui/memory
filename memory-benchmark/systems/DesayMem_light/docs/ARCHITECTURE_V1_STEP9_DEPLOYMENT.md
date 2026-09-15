# 设计 Step 9：云端配置与部署

## 1. 部署目标

V1 与现有 `DesayMem_mem0` 并行部署，不复用其 API 端口和数据目录，但可以连接已经部署的 Qwen3-32B、BGE-M3 和 PostgreSQL 服务。

所有进程使用同一个应用镜像、同一套领域代码和插件注册表，仅启动命令不同。

## 2. 进程拓扑

```text
desaymem-light-api
  接收消息、检索、状态与 lineage API

desaymem-light-memory-worker
  Session Topic、L1 Fact、Event

desaymem-light-insight-worker
  Cross-Event、Profile

desaymem-light-mirror-worker
  PG/SQLite Outbox -> JSON、SQLite 兼容投影

desaymem-light-reconcile
  定时执行数据库与 JSON 全量核对
```

V1 推荐实例数：

```text
API             1
Memory Worker   1
Insight Worker  1
Mirror Worker   1（必须单实例）
Reconcile       定时任务，不常驻也可以
```

先以稳定测试为主。API 和计算 Worker 后续可以扩容；SQLite 与 Mirror 在迁移前始终单写。

## 3. 外部依赖

```text
Qwen3-32B OpenAI-compatible endpoint
BGE-M3 embedding endpoint
PostgreSQL 16 + pgvector
SQLite persistent volume
JSON mirror persistent volume
```

新服务不得把宿主机地址写死在代码中。在 Docker 内通过环境变量或 DNS 服务名访问。

## 4. 配置分层

配置按优先级合并：

```text
代码安全默认值
  < config/default.yaml
  < config/cloud-test.yaml
  < 环境变量与 Secret
```

- 插件选择和算法参数：YAML，纳入版本管理。
- 服务地址、密码、API Key：环境变量/Secret，不进入 Git。
- 单次请求参数只能缩小限制，不能突破系统最大 token、top_k 和超时。

## 5. 插件配置隔离

配置示例见 `config/cloud-test.example.yaml`。

每个插件只能读取自己的配置段：

```text
modules.topic_segmenter -> module_options.lightmem_v1
modules.fact_extractor  -> module_options.mem0_additive_v1
modules.event_builder   -> module_options.deterministic_v1
modules.cross_event     -> module_options.structmem_style_v1
modules.profile_updater -> module_options.timem_style_v1
modules.retriever       -> module_options.hybrid_v1
modules.mirror          -> module_options.outbox_json_v1
```

插件构造函数接收已校验的配置对象和必要接口，不允许读取全局环境变量。这样替换插件不会污染其他模块。

## 6. 环境变量

环境变量示例见 `.env.example`，分为：

- 服务：监听地址、API 端口、日志等级；
- Qwen：Base URL、模型名、Key、超时；
- BGE：Base URL、模型名、维度、超时；
- PostgreSQL：DSN、连接池；
- SQLite：文件路径和 busy timeout；
- JSON Mirror：根目录、ACK 等待时间；
- 配置：环境配置 YAML 路径。

模型名称和 embedding 维度也必须由接口实际返回值验证，不能只相信配置。

## 7. 数据卷

```text
/data/sqlite/history.db
/data/json-mirror/
/data/logs/                 # 可选，推荐日志直接输出 stdout
```

- PostgreSQL 使用独立服务的数据卷，不挂入应用容器。
- SQLite 和 JSON Mirror 必须是不同目录，便于分别设置容量告警和备份。
- API 容器只需读取镜像状态，不直接写 JSON；只有 Mirror Worker 挂载 JSON 卷为读写。
- Reconcile 任务挂载 JSON 卷为读写。

## 8. 启动顺序

```text
1. PostgreSQL/pgvector ready
2. 执行 PostgreSQL 和 SQLite migration
3. 校验镜像 registry 与 triggers
4. 启动 Mirror Worker
5. 启动 Memory/Insight Worker
6. 启动 API
7. 执行启动期小规模 reconcile
```

迁移只能由独立 init job 执行，API 和 Worker 不并发自动改表。

## 9. Readiness 校验

API 接收写请求前必须通过：

```text
配置 schema 合法
所有插件名已注册且接口版本兼容
Prompt 文件存在且 hash 与配置一致
Qwen 健康（写入链路需要）
BGE 健康且 embedding_dims=1024
PostgreSQL 可连接且 migration version 正确
pgvector 扩展可用
SQLite 可打开且 schema version 正确
JSON Mirror registry/trigger 完整
JSON 卷可写且剩余容量高于阈值
```

检索可按能力降级；写入链路的关键依赖不满足时应 not-ready，不能静默丢记忆。

## 10. Worker 调度

V1 复用 PostgreSQL `memory_jobs`，通过 `FOR UPDATE SKIP LOCKED` 领取任务。

- 每种 `job_type` 有独立并发和超时配置。
- Job 保存 `plugin_name、plugin_version、payload_schema_version`。
- Worker 只加载自己允许的 job 类型。
- 指数退避并限制最大重试次数；失败进入 `dead` 状态等待人工处理。
- Qwen/BGE 超时不占用数据库事务。

替换插件时：先注册新版本，灰度创建部分新 Job，旧版本继续处理已冻结任务；确认后再切换默认配置。

## 11. API 与现有系统并行

- 使用新的服务名、端口、PostgreSQL schema/database、SQLite 文件和 JSON 目录。
- 不直接修改 `DesayMem_mem0` 数据库表。
- 若需要对比测试，由上层测试工具向两个 API 分别发送相同请求。
- 禁止两个系统共用同一个 SQLite 文件。

建议命名：

```text
PostgreSQL database/schema: desaymem_light
SQLite: /data/sqlite/desaymem_light_history.db
JSON: /data/json-mirror/desaymem_light
API port: 使用未占用端口，通过 DESAYMEM_HTTP_PORT 指定
```

## 12. 观测指标

按模块统一输出：

```text
request/job count
success/failure/retry count
latency
LLM input/output tokens
embedding calls/text count
topic/event/profile output count
database transaction latency
mirror lag/failure/mismatch
JSON volume free bytes
```

指标标签允许：`module、plugin、version、status`。禁止把 `user_id、vehicle_id、query、memory content` 作为指标标签，避免隐私泄露和时序库基数爆炸。

## 13. 日志与追踪

每次处理统一携带：

```text
request_id
trace_id
job_id
tenant_id（可哈希）
session_id（可哈希）
module/plugin/version
prompt_version
```

日志不记录完整原始消息和画像内容。需要查看内容时通过受权的数据库/JSON 可视化接口读取。

## 14. 配置变更规则

- 修改插件实现名称：需要重启对应进程。
- 修改 Prompt：必须产生新 prompt_version。
- 修改 embedding 模型或维度：必须创建新向量列/索引并重嵌入，禁止直接覆盖。
- 修改 Topic 阈值：只影响新会话，不回切历史 Topic。
- 修改 Cross-Event/Profile 周期：只影响新 Job，不自动重算历史。
- 数据库表变更：只能通过 migration，并同步 JSON registry/trigger 和契约测试。

## 15. V1 不做

- 不引入 Kafka、Redis、Kubernetes Operator 或服务网格。
- 不为每个插件单独构建镜像。
- 不支持运行时热加载任意 Python 包。
- 不允许插件修改数据库 schema。
- 不让 API、Worker 各自维护一份不同配置。
