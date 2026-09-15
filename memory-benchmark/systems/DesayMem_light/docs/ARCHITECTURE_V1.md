# DesayMem_light V1 总体架构

## 1. 设计原则

1. 直接复用已经验证的代码思想，不在 V1 引入图数据库、复杂规则系统或多阶段画像推理。
2. PostgreSQL 是业务事实源；JSON 是可重建的同步镜像，不反向参与业务判断。
3. 身份键始终包含 `tenant_id、user_id、vehicle_id、occupant_id`，会话态再增加 `session_id`。
4. LLM 只用于 Mem0 事实抽取、Cross-Event 总结和画像批量更新；Event、Tag、过滤和排序不新增 LLM。
5. 所有派生记忆保存 `source_ids、model、prompt_version、created_at`，可回溯到原始消息。

## 2. 模块与数据流

### 2.1 Ingest 与 SessionBuffer

- API 接收消息和完整身份范围，写入 PostgreSQL `session_messages`。
- 同一 `session_id` 内通过单调递增 `sequence_no` 保序，`request_id` 保证幂等。
- Topic 切分借鉴 LightMem：使用相邻消息 BGE-M3 相似度与轻量注意力信号；不增加生成式 LLM。
- 每个 Topic 形成一个 `topic_segment`。达到约 2000 tokens、会话结束或显式 flush 时进入抽取队列。
- 512-token SensoryBuffer 仅作为在线切分窗口，不单独生成长期记忆，也不启用预压缩。

### 2.2 L1 Fact

- 复用本地 Mem0 最新开源 ADD 流程：一次批次抽取后执行候选检索、冲突判断和 ADD/UPDATE/DELETE/NOOP。
- 沿用 `DesayMem_mem0` 的时间冲突消解；操作、旧值、新值和来源全部审计。
- Fact 写入 `memory_items(type=fact)`；重复事实复用主记录，但在 `memory_evidence` 增加新的观察证据。
- Embedding 使用 BGE-M3，入库维度固定为 1024。

### 2.3 L2 Event 与 Cross-Event

- Event 借鉴 StructMem 的事件组织，但 V1 不照搬其双 LLM 抽取。
- 一个 Topic 对应一个 Topic Event；用程序把 Topic 元数据、Fact、实体、时间和 Tags 组装为 `memory_items(type=event)`，LLM 调用为 0。
- Cross-Event 异步执行：先按身份、时间和向量召回历史 Event，再让 Qwen3-32B 一次生成跨事件总结。
- 触发条件采用“事件数量或时间窗口”配置；默认累计 10 个新 Event 或每日低峰 flush，输入受 token 上限约束。
- Cross-Event 必须保存有序 `source_event_ids`，只使用生成时刻之前的事件，避免未来信息泄漏。

### 2.4 L3 Profile

- 借鉴 TiMEM 的“当前画像 + 新周期信息 -> 新画像”更新方式。
- 输入为当前有效 `profile_items`、新 Cross-Event 及必要 Event；一次 LLM 同时输出：结构化画像操作和简短自然语言摘要。
- 多值默认共存，例如“喜欢周杰伦”和“喜欢陶喆”是两个有效项；只有明确否定或替代证据才关闭旧项。
- `profile_items` 是事实源，`profile_snapshots.summary` 只是给 Agent 使用的投影；不对画像做向量检索。
- 画像证据按原始 Event 去重，Cross-Event 不重复增加 evidence count。

## 3. 检索链路

请求字段：

```text
query, tenant_id, user_id, vehicle_id, occupant_id,
session_id?, tags?, time_from?, time_to?, top_k?, vehicle_only?
```

执行顺序：

1. 强制 tenant/user/occupant 权限范围；默认返回用户全局记忆和当前车辆记忆，`vehicle_only=true` 时只取当前车辆。
2. 可选 Tags 与 `occurred_at` 时间过滤。
3. BGE-M3 向量召回 Fact、Event、Cross-Event；可与 PostgreSQL FTS 做 RRF 融合。
4. 去重、按类型配额截断：Fact 约 5、Event 2~3、Cross-Event 1~2。
5. 直接附加最新有效画像摘要，总上下文默认不超过 2000 tokens。

默认不调用 LLM query rewrite 和 rerank。

## 4. 服务边界

```text
api                 接入、检索、状态查询
buffer_worker       Topic 切分和抽取批次生成
memory_worker       Mem0 L1、Event 组装
insight_worker      Cross-Event、Profile 低频任务
mirror_worker       PG/SQLite Outbox -> JSON
reconcile_worker    数据库与 JSON 定期核对修复
```

V1 可将 worker 合并在一个进程部署，但代码接口保持分层。多 API 实例只共享 PostgreSQL；SQLite 必须由单个兼容 worker 持有并挂载持久卷。

所有算法模块通过稳定接口和显式注册表装配。模块只返回领域 DTO，不直接写数据库；Application Service 统一负责事务、审计、任务和 JSON Outbox。具体定义见 `ARCHITECTURE_V1_STEP8_CODE_MODULES.md`。

## 5. LLM 与资源预算

- 普通消息：0 次记忆 LLM。
- 每个约 2000-token Topic 批次：Mem0 L1 约 1 次主抽取调用；冲突操作沿用 Mem0 当前实现，不额外增加 L2 调用。
- Event：0 次。
- Cross-Event：每 10 个事件或每日 flush 最多按 token 分片调用。
- Profile：每个更新批次 1 次，并同时产生摘要。
- 检索：0 次 LLM，1 次 query embedding。

以 20 轮会话、约 2 个抽取批次估算，记忆侧约 2~4 次 LLM；原先逐轮 L1/L2/L3/rerank 的最坏设计约 80 次。实际 token 和调用数必须由 `llm_usage` 审计表测量。

## 6. 部署配置

所有地址均使用环境变量，不把现有端口写死：

```text
QWEN_BASE_URL, QWEN_MODEL, QWEN_API_KEY
BGE_BASE_URL, BGE_MODEL, EMBEDDING_DIMS=1024
POSTGRES_DSN, SQLITE_PATH
JSON_MIRROR_ROOT, JSON_MIRROR_SYNC_TIMEOUT_MS
TOPIC_BATCH_TOKENS=2000, SENSORY_WINDOW_TOKENS=512
CROSS_EVENT_TRIGGER_COUNT=10, AGENT_CONTEXT_MAX_TOKENS=2000
```

启动时必须探测模型接口、embedding 维度、数据库迁移版本和镜像目录写权限；任一不匹配则拒绝接收写请求。
