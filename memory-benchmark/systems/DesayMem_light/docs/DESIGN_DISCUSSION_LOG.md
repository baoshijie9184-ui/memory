# DesayMem Light 设计讨论与改进记录

> 用途：持续记录方案讨论、设计依据、已确认决策、已知不足、代码状态和验证结果，方便后续回溯与算法改进。
>
> 维护约定：每次讨论后更新对应模块；尚未确认的内容标记为“待讨论”，不能直接视为最终方案。

> 当前收口入口：[DESAYMEM_V1_DESIGN_BASELINE.md](DESAYMEM_V1_DESIGN_BASELINE.md)；图示：[DESAYMEM_TECHNICAL_FLOW_REPORT.html](DESAYMEM_TECHNICAL_FLOW_REPORT.html#v1-baseline)。下文保留逐轮讨论与已被修正的提案，不应把所有历史段落同时视为当前决定。

## 讨论记录规范

后续每个模块必须分别记录：

1. 参考项目原始设计：LightMem、Mem0、StructMem、MemOS或TiMem实际采用了什么机制。
2. DesayMem Light设计：直接复用、裁剪或新增了什么。
3. 修改原因：结合车载云端多用户并发、LLM/Token成本、存储、追溯与可维护性说明取舍。

不能把DesayMem Light自己的工程化扩展表述成参考项目的原始方案。

## 1. 项目目标

DesayMem Light 是面向车载云端、多用户和高并发场景的分层记忆系统。第一版优先保证：

- 方案简单、稳定、参考来源明确。
- 控制 LLM、Embedding、Token 和云端磁盘消耗。
- 按 tenant、user、vehicle、occupant、session 正确隔离数据。
- PostgreSQL 作为数据事实源，并维护一一对应的 JSON Mirror。
- 保存完整证据链，支持从画像回溯至原始消息。
- 各算法模块可插拔，方便后续替换和扩展。

## 2. 当前总体架构

```text
用户对话 / 助手回复 / 车机操作
              ↓
        Session Buffer
              ↓
     LightMem 式 Topic 切分
              ↓
       Mem0 式 Fact 抽取
              ↓
       确定性 Event 聚合
              ↓
 StructMem 式 Cross-Event 总结
              ↓
       TiMEM 式 Profile 更新
              ↓
 QueryPlanner + 分层检索与去重
```

参考来源：

- LightMem：Session Buffer、Topic 切分和轻量化思路。
- Mem0：Fact 抽取及记忆写入思路。
- StructMem：Event 时间窗口 `Cbuf`、历史相似 Event `Sk` 和 Cross-Event 总结。
- MEMOS：Tag 和时间过滤思路。
- TiMEM：结构化用户画像的增量更新思路。

## 3. 当前写入流程

### 3.1 Session Buffer

消息与车机操作先写入 PostgreSQL 的 `session_messages`。

隔离范围：

```text
tenant_id + user_id + vehicle_id + occupant_id + session_id
```

当前设计：

- 支持 `user / assistant / system` 角色。
- 默认不为每条 Session 消息生成向量。
- 回答时读取尚未沉淀的消息，并补充最近 4 条已沉淀消息。
- Topic 成功形成 Event 后，相关消息才标记为已沉淀。

### 3.2 Topic 切分

参考 LightMem，根据语义变化、Token 容量或强制刷新切分 Topic。当前实现使用 Embedding，不额外调用 LLM。

### 3.3 Fact（L1）

每个完成 Topic 调用一次 Qwen3-32B，抽取：

- `action`
- `state`
- `explicit_preference`
- `personal_fact`

Fact 保存内容、Embedding、时间、Tag、Topic、来源消息及归属范围。只在同一 Topic 内去重，不删除不同 Topic 中重复发生的行为。

#### 四类 Fact 的来源与实际含义（与技术流程图同步）

- **原版 Mem0**：当前本地开源版的 additive extraction 从对话中提取自然语言记忆，主要输出 `text` 和可选的 `linked_memory_ids`；它没有强制使用这四个 `fact_type`，也没有我们的“Topic Facts → 显式 Event”链路。我们借鉴的是新消息抽取、历史关联与证据约束，不是原样复用其输出格式。
- **DesayMem 新增的四类**：`action` 是一次具体请求或操作（“用户请求播放周杰伦”）；`state` 是有回执支持的实际状态（“车辆正在播放《晴天》”）；`explicit_preference` 是用户明确表达的喜欢、习惯或舒适设置；`personal_fact` 是用户明确提供的身份、关系、工作、计划等信息。一次普通播放请求不能自动写成“喜欢周杰伦”。
- **判定和校验**：一个完成 Topic 只调用一次 Qwen，抽取零条或多条 Fact，同时给出类别、来源消息编号、语义 Tag 和新旧操作。代码校验类别枚举与来源编号，强制 `action` 为 `ADD`；目前没有第二个模型或规则来验证语义分类是否正确。
- **为什么增加**：车载系统需要区分“用户提出操作”与“车机已执行”，防止一次行为被推断为长期偏好；分类也方便 Event 聚合、时间检索与后续 Profile 蒸馏。它不增加一次 LLM 调用，但增加分类边界和误判风险，后续应基于证据和评测案例调整。

示例：`user: 播放周杰伦的歌`，`assistant: 正在播放《晴天》`，模型可返回：

```json
{"memory":[{"text":"用户请求播放周杰伦的歌曲","fact_type":"action","operation":"ADD","attributed_to":"user","source_message_numbers":[0],"semantic_tags":["音乐","周杰伦"]},{"text":"车辆正在播放《晴天》","fact_type":"state","operation":"ADD","attributed_to":"assistant","source_message_numbers":[1],"semantic_tags":["音乐","晴天"]}]}
```

两条分别保存为 `memory_items` 的 `FACT` 行：`content` 保存文本，`metadata.fact_type` 保存类别；代码补上时间、向量、Tag、Topic/Message 证据关联。这里**不会**生成“用户喜欢周杰伦”的 `explicit_preference`。

对应图文报告：[DESAYMEM_TECHNICAL_FLOW_REPORT.html](DESAYMEM_TECHNICAL_FLOW_REPORT.html#fact-types)。

### 3.4 Event（L2）

同一 Topic 的 Fact 通过确定性代码聚合成一个 Event，不额外调用 LLM。

```text
Event --contains--> Fact --evidence--> Topic / Message
```

### 3.5 Cross-Event（L2）

触发条件：

- checkpoint 后新增 Event 达到 10 条；或
- 等待 10 分钟且至少存在 2 条新 Event；或
- 手动强制触发。

当前参考 StructMem：

```text
Cbuf：本批实际处理的新 Event
Sk：根据 Cbuf 中心向量召回的历史相似 Event
Cbuf + Sk → 一次 LLM → 一个 Cross-Event
```

Cross-Event 区分：

- `covered_event_ids`：本批实际处理的新 Event。
- `support_event_ids`：LLM 生成总结时实际引用的 Event。
- `seed_event_ids`：召回的历史相似 Event。

checkpoint 只推进到最后一个 covered Event。因 Token 限制未进入本批的 Event 留到下一批。

### 3.6 Profile（L3）

只有成功创建 Cross-Event 后才触发 Profile 更新。输入包括：

- 当前画像摘要。
- 当前结构化画像条目。
- 新 Cross-Event。
- `support_event_ids` 对应的 Event。

支持操作：

```text
ADD / CONFIRM / COEXIST / SUPERSEDE / NOOP
```

结构化条目允许同一属性拥有多个并存值，例如“喜欢的歌手=周杰伦”和“喜欢的歌手=陶喆”。同时生成自然语言 Profile Snapshot，供回答模型使用。

## 4. 当前查询流程

### 4.1 QueryPlanner

普通问题不调用 QueryPlanner LLM。模糊时间、历史、偏好或排序问题，可调用一次 Qwen，将问题转换为：

```json
{
  "query": "去过的餐厅",
  "tags": ["餐厅"],
  "time_from": "带时区的开始时间",
  "time_to": "带时区的结束时间",
  "intent": "history"
}
```

### 4.2 分层召回

```text
当前 Session
→ Profile
→ Cross-Event
→ Event
→ Fact
```

- `session_id` 只约束当前 Session Buffer。
- Fact、Event、Cross-Event 是跨 Session 的长期记忆。
- 返回 Cross-Event 后，过滤它已覆盖的 Event。
- 返回 Event 后，过滤它已包含的 Fact。
- 未被高层记忆覆盖的 Fact 仍可用于具体细节召回。

## 5. 当前资源消耗

- 普通查询：1 次 Embedding，0 次 LLM。
- 需要查询规划：1 次 QueryPlanner LLM，1 次 Embedding。
- 每个 Topic：1 次 Fact LLM；Fact 批量 Embedding；Event 1 次 Embedding。
- 每批 Cross-Event：1 次 LLM，成功创建时 1 次 Embedding。
- 每次 Profile 更新：1 次 LLM；产生新条目时批量 Embedding。
- Topic、Event 聚合、时间、关系和大部分证据处理使用确定性代码。

## 6. 当前已知不足

### P0：Profile 数据库事务生命周期错误

状态：**已发现，待修复**。

最新代码中 Profile 的稳态判断、Snapshot、Usage、Checkpoint 和 commit 位于 `async with unit_of_work()` 之外，却继续使用 `unit`。内存测试桩无法暴露该问题，但真实 PostgreSQL 连接可能已经归还连接池。

影响：云端 Profile 更新可能失败或产生事务一致性问题。

### P0：历史 Sk 排除范围过大

状态：**已发现，待讨论/修复**。

最新代码排除了所有已经成为 `summarizes` 或 `related_to` 目标的 Event。这与 StructMem 原实现不完全一致：StructMem 只用 `consolidated` 控制 Cbuf 扫描，已经处理的 Event 仍可能被召回为后续窗口的历史 Sk。

影响：同一历史 Event 一旦参加过 Cross-Event，就永久不能参与新的跨时间规律，可能漏掉长期行为变化。

### P1：一个窗口只生成一个 Cross-Event

状态：**已记录，第一版暂不扩展**。

如果一个窗口混合音乐、导航和餐厅等主题，一个总结可能混杂多个主题，或忽略部分内容。按 Topic/Tag 分组会提升质量，但会增加 LLM 调用次数。

### P1：“最喜欢”缺少可靠排序

状态：**已记录，待讨论**。

当前画像支持多个值、确认次数及显式偏好，但尚无统一的偏好强度和排名模型。没有明确表达时，只能回答“经常/可能喜欢”，不能可靠断言唯一“最喜欢”。

### P1：QueryPlanner 触发覆盖有限

状态：**已记录，待讨论**。

为减少 LLM 调用，当前通过有限触发词决定是否规划。复杂但未命中触发词的时间表达可能不能转换为准确过滤条件。

### P1：JSON Reconcile 尚未完整实现

状态：**已记录，待实现**。

PostgreSQL Outbox 到 JSON Mirror 的实时投影已实现，但定期全量对账、孤儿删除和自动修复仍是占位逻辑。

## 7. 当前代码与验证状态

- GitHub 当前提交：`6ede89b`。
- 本地完整测试：`57 passed`。
- PostgreSQL Schema 最新版本：`016`。
- 本地测试未覆盖真实 PostgreSQL、pgvector、Qwen3-32B、BGE-M3 和云端 Mirror Worker 联调。

## 8. 后续讨论顺序

1. 修复 Profile 事务生命周期问题。
2. 明确历史 Sk 的复用策略，并与 StructMem 对齐。
3. 逐步检查 Session Buffer 与 Topic 边界。
4. 检查 Fact 抽取、去重、Tag、时间和证据。
5. 检查 Event 聚合内容是否完整。
6. 检查 Cross-Event 多主题与触发策略。
7. 检查 Profile 多值、冲突和排序。
8. 检查 QueryPlanner 和最终上下文组装。
9. 完成云端端到端及 JSON 一致性验证。

## 9. 讨论日志

### 2026-09-11：先按 Turn 聚合再进行 Topic 切分（原提案已修正）

- 原提案考虑增加 `turn_id`；后续确认 Agent 已按顺序传入 user/assistant，因此第一版不增加该字段。
- Session 内的数据层级调整为：`Message → Turn → Topic`。
- Turn 是 Topic 切分的最小不可拆分单元；禁止在一个 Turn 内根据消息相似度切分。
- Topic Segmenter 改为比较相邻 Turn，而不是直接比较相邻 Message。
- Turn 的表示文本按消息顺序拼接，例如：

```text
user: 播放周杰伦
assistant: 好的
system: 已开始播放《晴天》
```

- Topic 仍保存原始 `message_ids`，不额外复制消息内容；Fact 抽取继续使用完整角色与来源编号。
- 最终采用同一 Session 内基于 `sequence_no` 和角色的自动配对方式，详见后续“Turn 自动配对方案确认”。

### 2026-09-11：Session 与 Topic 参数配置原则

- 车载场景通常不会在一个 Topic 内产生 50 条对话；`50` 仅作为测试期安全上限，不代表业务常态。
- 所有容量、时间和阈值都必须作为模块超参数，禁止散落硬编码在算法流程中。
- 第一批配置项包括：
  - `similarity_threshold`：相邻消息语义阈值，测试初值 0.55。
  - `idle_flush_minutes`：空闲切分时间，测试初值 10 分钟。
  - `topic_max_messages`：单 Topic 消息上限，测试初值 50。
  - `topic_batch_tokens`：单 Topic Token 上限，测试初值 2,000。
  - `session_buffer_max_messages`：回答时 pending 消息上限，待确认。
  - `session_buffer_max_tokens`：Session 上下文上限，测试初值 2,000。
  - `materialized_last_k_messages`：补充的已沉淀消息数，测试初值 4。
  - `embedding_cache_ttl_minutes`：进程内缓存 TTL，测试初值 30 分钟。
  - `embedding_cache_max_entries`：每 Worker 缓存容量，测试初值 10,000。
- 云端测试需要记录真实 Topic 消息数、切分原因、相似度、等待时长和 Token 使用，再确定生产参数。

建议配置及注释：

```yaml
lightmem_v1:
  # 相邻两条消息的余弦相似度低于该值时，认为发生话题切换。
  # 值越高越容易切分，值越低越容易把不同内容合并；0.55 为测试初值。
  similarity_threshold: 0.55

  # 当前 Session 连续多少分钟没有新消息时，强制结束最后一个 Topic。
  # 值越小沉淀越及时，但短暂停顿更容易把一个话题拆开。
  idle_flush_minutes: 10

  # 单个 Topic 允许包含的最大消息条数，是异常长会话的安全上限。
  # 达到上限后立即切分；50 为测试保护值，不代表正常车载会话长度。
  topic_max_messages: 50

  # 单个 Topic 允许的最大 Token 数，防止 Fact LLM 输入无限增长。
  # 达到上限后立即切分；值越大上下文更完整，但模型输入成本越高。
  topic_batch_tokens: 2000

  # Worker 进程内 Topic Embedding 缓存的存活时间。
  # 超时后允许释放；值越大重复计算越少，但占用内存时间越长。
  embedding_cache_ttl_minutes: 30

  # 每个 Worker 最多缓存多少条消息的 Topic Embedding。
  # 达到上限时淘汰旧条目；值越大节省计算越多，但占用更多内存。
  embedding_cache_max_entries: 10000

hybrid_v1:
  # 回答时最多读取多少条尚未沉淀的当前 Session 消息，具体初值待确认。
  session_buffer_max_messages: 10

  # 提供给回答模型的 Session 消息最大 Token 数；超限时优先保留最新消息。
  session_buffer_max_tokens: 2000

  # Topic 已沉淀后仍补充多少条最近原始消息，解决切分边界附近的上下文衔接。
  materialized_last_k_messages: 4
```

### 2026-09-11：Topic 消息数量硬上限确认

- 第一版增加可配置项 `topic_max_messages=50`。
- Topic 达到 50 条消息时立即完成，不再等待语义切换或空闲超时。
- 当前 Topic 完成条件统一为：相邻语义切换、达到 2,000 Token、达到 50 条消息、连续空闲 10 分钟。
- 该上限用于防止前 200 条 pending 消息长期占据扫描窗口，导致后续消息无法参与切分。

### 2026-09-11：Session 空闲延迟触发方案确认

- Session 连续空闲 10 分钟后，强制切分当前最后一个 Topic。
- 每次新消息写入时，创建一个可延迟执行的 Topic flush Job。
- Job 携带 `expected_last_message_id` 和 `expected_last_ingested_at`。
- Job 执行时重新查询最后一条 pending 消息：若仍是预期消息且已空闲 10 分钟，则 `force_flush`；若出现更新消息则旧 Job 执行 `NOOP`。
- 新消息负责安排新的 10 分钟延迟 Job，不要求主动取消旧 Job。
- 该机制需复用 `memory_jobs.next_run_at`、幂等键和数据库范围隔离，以适配多 Worker 并发及失败重试。

### 2026-09-11：Topic Embedding 缓存方案确认

- 第一版增加 Memory Worker 进程内 TTL Embedding 缓存。
- 建议缓存键：`embedding_model/version + message_id + content_hash`。
- 初始 TTL：30 分钟；初始最大容量：每个 Worker 10,000 条，可配置。
- 缓存仅优化 Topic 切分，不启用语义短期记忆检索，不写 PostgreSQL 和 JSON Mirror。
- 接受 Worker 重启、跨 Worker 接管或缓存淘汰时重新计算。
- 后续待讨论：是否增加 PostgreSQL 持久化 Embedding 缓存，以换取多 Worker 共享和重启复用；需同时评估每条约 4KB 以上的向量存储与 JSON Mirror 成本。

### 2026-09-11：Turn 自动配对方案确认

- 第一版不要求 Agent 或车机额外传入 `turn_id`。
- 在同一 Session 内按 `sequence_no` 排序，一条 `user` 消息及其后、下一条 `user` 之前的 `assistant` 消息自动组成一个 Turn。
- 可选 `system` 执行结果归入当前 Turn；下一条 `user` 到达时开始新 Turn。
- Topic 语义切换应比较完整 Turn，而不是拆开比较 user 与其 assistant 回复。
- 不单独设置 Turn 消息数量上限，继续由 Topic 的消息数、Token和空闲超时负责保护。
- 后续可选扩展关联 ID，用于处理异步车机结果跨越下一轮才返回的情况；第一版暂不增加协议复杂度。
- 没有对应 `user` 的独立 `system` 车机状态（例如低电量提醒）单独组成一个 Turn。
- 位于 user/assistant 交互期间的 `system` 执行结果仍归入当前 Turn。

### 2026-09-11：Session 上下文按完整 Turn 装入

- 回答模型的 Session 上下文不再按单条消息机械截断，而是以完整 Turn 为最小单位。
- 从最新 Turn 向前装入，优先保证最新交互；达到 `session_buffer_max_tokens` 或消息安全上限后停止。
- 同一 Turn 内的 user、assistant 和可选 system 执行结果必须一起保留，不能只传其中一部分。
- 全部容量仍由可配置超参数控制，测试阶段记录真实车载 Turn 长度和 Token 分布后再调整。
- 如果最新 Turn 本身超过 `session_buffer_max_tokens`，仍完整传入该 Turn，本次允许临时超限，但不再追加更早 Turn。
- 记录最新 Turn 超限指标，后续再评估截断或压缩；第一版不为此增加 LLM 压缩调用。
- “完整 Turn”要求只针对尚未沉淀的当前 Session 内容。
- 已沉淀历史继续使用最近若干条消息 `materialized_last_k_messages`，不改成完整 Turn；其主要作用是连接切分边界，历史语义内容由 Event 召回承担。
- `materialized_last_k_messages` 保持可配置，测试初值仍为 4。

### 2026-09-11：Topic 语义切换方案确认

- 第一版继续使用相邻消息 Embedding 的余弦相似度判断话题切换。
- 当前阈值保持 `similarity_threshold=0.55`，后续根据云端数据评估调整。
- 暂不改为“新消息与 Topic 中心向量比较”，避免第一版复杂化。
- 已记录缺陷：相邻句局部相似可能造成跨话题误合并，后续再评估。

### 2026-09-11：Session 空闲强制切分确认

- 增加可配置的 Session 连续空闲超时，测试阶段设为 10 分钟。
- 计时基准使用服务端 `ingested_at`，不依赖可能延迟或错误的客户端 `occurred_at`。
- 每次写入新消息后，以最新消息重新计算空闲截止时间；旧超时任务执行时必须检查是否已有更新消息。
- 连续空闲达到 10 分钟后，将该 Session 剩余 pending 消息强制组成 Topic，进入 Fact 抽取。
- 超时任务必须幂等；若 pending 已被语义切分、Token 切分或其他 Worker 处理，则直接 NOOP。

### 2026-09-11：结合代码模拟写入与沉淀

模拟输入：

```text
08:00 user：播放周杰伦
08:01 assistant：正在播放《晴天》
08:20 user：导航到公司
```

实际执行：

1. 每条消息经 `POST /v1/messages` 写入 `session_messages`，同时创建一个 `session_segment` Job。
2. Memory Worker 读取当前 Session 的 pending messages，自动组成完整 Turn，Topic Segmenter 批量生成 Embedding并比较相邻 Turn。
3. “导航到公司”与前两条消息语义差异较大时，前两条形成音乐 Topic；最后一条导航消息继续 pending。
4. Topic 创建后产生 `fact_extract` Job。Fact Pipeline 召回相似历史 Fact，调用一次 Qwen抽取 action/state，并批量生成 Fact Embedding。
5. Fact、Message/Topic Evidence、Tag 和时间写入数据库，随后产生 `event_build` Job。
6. Event Builder 确定性拼接 Fact，生成 Event Embedding，写入 contains/evidence/tag，完成 Topic 并标记相关消息已沉淀。
7. Event 创建后产生 `cross_event` Job；达到数量/时间条件后生成 Cross-Event，成功后再产生 `profile_update` Job。

本轮发现的写入侧不足：

- 最后一个未出现后续语义边界的 Topic 由可配置空闲任务 force-flush；测试初值为连续空闲 10 分钟。
- 每条消息仍会调度 Topic Job，但同一 Worker 使用 TTL 缓存复用 Message Embedding；跨 Worker 共享缓存留待后续讨论。
- `sensory_token_limit` 已传入 `SegmentRequest`，但当前 `LightMemTopicSegmenter` 没有使用该值。
- 异步沉淀期间依靠 Session 检索保证即时回答，这一设计本身正确。

### 2026-09-11：Session/Topic 第一版修改与验证完成

- 已新增自动 Turn 分组；Topic 的语义、Token 和消息数边界都不会拆开 user/assistant Turn。
- 已新增 10 分钟可配置延迟 force-flush，并用预期最新消息 ID/写入时间校验；过期任务 NOOP。
- 已新增 Worker 进程内 TTL Embedding 缓存，TTL 30 分钟、容量 10,000 均可配置。
- Session 回答上下文按完整 pending Turn 从新到旧装入；最新 Turn 即使超预算也保持完整。
- 已沉淀内容仅补最近 4 条原始消息，更多历史依靠 Event/Cross-Event 召回。
- 已默认关闭 `persist_short_term`，避免为原始 Session 消息额外写向量和 JSON Mirror。
- 本地单元测试：63 项通过；云端 PostgreSQL、多 Worker 并发和真实 BGE-M3 延迟仍需部署验证。

### 2026-09-11：Fact 时间、Tag、Evidence 与触发来源确认

- Fact 的 `occurred_at/occurred_end` 取所引用证据消息的时间范围，`observed_at` 取服务端处理时间。
- Fact Tag 合并上游消息 Tag 与同一次 Qwen 抽取返回的语义 Tag，不增加额外 LLM 调用。
- Qwen 仅返回 `source_message_numbers`；服务端校验后转换为真实 Message ID，并建立 Fact→Topic、Fact→Message Evidence。
- `trigger_type` 不由 LLM 生成，直接继承 Topic 的 `boundary_reason` 并写入 Fact metadata。
- 空闲延迟任务明确产生 `idle_timeout`，人工强制切分保持 `manual`。
- PostgreSQL 迁移 017 同时补充允许 `idle_timeout` 和此前遗漏的 `message_limit` Topic 边界值。

### 2026-09-11：Topic、Fact 与 Event 关联方案确认

- 一个 Topic 最多生成一个 Event；没有关联 Fact 时跳过 Event。
- Event 内容第一版按 Fact 顺序确定性拼接，不调用 LLM。
- Action Fact 跨 Topic 不去重，Event 跨 Topic 也不去重，以保留行为次数和时间。
- 稳定 Fact 后续执行 CONFIRM 时复用历史 Fact，但当前 Topic 仍须生成自己的 Event。
- 新增 `topic_fact_links`，记录 `topic_segment_id/fact_id/operation/ordinal`；支持 ADD、CONFIRM、COEXIST、SUPERSEDE。
- Event Builder 改为只读取当前 Topic 的显式 Fact 关联，不依赖 Fact 最初所属 Topic。
- Event 时间和 Message Evidence 始终取当前 Topic，避免复用历史 Fact 时混入旧时间。
- PostgreSQL 迁移 018 创建并回填关联表，同时注册 JSON Mirror。
- Fact Prompt 升级为 v4，在同一次 Qwen 调用中输出 ADD、CONFIRM、COEXIST或SUPERSEDE；空数组表示NOOP。
- Action无条件按新发生记录执行ADD，防止跨Topic复用抹掉行为次数与时间。
- CONFIRM复用候选Fact且不新增Fact Embedding；其余新增Fact继续一次批量生成Embedding。
- COEXIST新增Fact并建立`related_to`，SUPERSEDE新增Fact、建立`supersedes`并将旧Fact标记为superseded。
- Event读取显式Topic-Fact关联时保留后来被supersede的Fact，保证历史事件不会因当前状态变化而缺失。

### 2026-09-12：Event保留Fact操作语义（待整体讨论后实施）

- Event需保留当前Topic中每条Fact对应的`ADD/CONFIRM/COEXIST/SUPERSEDE`操作。
- `ADD`与`COEXIST`正文直接使用新Fact内容；`CONFIRM`确定性增加“再次确认”；`SUPERSEDE`确定性增加“更新为”。
- Event metadata建议保存`fact_id/operation/target_fact_id`，供Cross-Event归纳、审计和问题回溯使用。
- `NOOP`不建立Topic-Fact关联，也不参与Event；Topic没有任何有效Fact时不生成Event。
- 继续使用确定性Event Builder，不增加LLM调用。
- 当前`topic_fact_links`尚无`target_fact_id`字段，Event Builder也尚未接收操作信息。
- 本项暂只记录设计，不立即修改代码；待Event与Cross-Event整体细节讨论完成后统一实现和迁移。

### 2026-09-12：StructMem时间窗口与Cross-Event证据语义分析

**StructMem原始设计**

- 从最早一条`consolidated=false`的记忆时间开始，构造`[current_time, current_time + time_window]`固定时间窗口。
- 窗口内全部未consolidated条目组成`Cbuf`；窗口为空时跳到下一条未来未处理记忆的时间。
- 拼接Cbuf文本生成一次查询Embedding并召回Top-K补充条目`Sk`，然后把全部Cbuf与Sk交给一次LLM生成一段摘要。
- StructMem的LLM输出是自由文本，没有返回“实际支持摘要的entry IDs”；存储时只记录全部`covered_entry_ids`与全部`seed_entry_ids`。
- 摘要完成后全部Cbuf标记为consolidated。Sk不被标记；已consolidated的Cbuf仍保留在基础向量库，并可在后续作为Sk召回。

**DesayMem Light当前差异**

- 输入单位是已构建的Event，不是StructMem底层的事实/关系记忆条目。
- Cbuf由Checkpoint之后的连续新Event批次产生，并采用数量触发加时间兜底，而非纯固定时间窗。
- Qwen结构化返回`supporting_event_numbers`，显式区分“本批已处理Event”和“真正支持本条Cross-Event的Event”。

**为什么需要区分**

- 按时间或数量形成的Cbuf可能同时包含音乐、导航、空调等无关Event，不应强迫一条Cross-Event引用全部内容。
- StructMem也可能在自由文本摘要中省略无关条目，只是原实现没有记录这种省略，覆盖关系较粗。
- DesayMem为增强可追溯性，应保留`covered_event_ids`与`support_event_ids`两个集合；前者用于Checkpoint，后者才代表语义证据。
- 待确认：Checkpoint推进全部Cbuf，但仅对support建立摘要/证据关系；未支持Event继续允许作为未来Sk召回。

**最终确认与修改**

- 按用户对Cross-Event的定义，第一版回归StructMem的完整窗口摘要：Cross-Event必须覆盖全部Cbuf Event，而不是只输出一个局部显著模式。
- Cbuf与历史Sk在Prompt中分区输入；Cbuf全部进入`covered_event_ids`并建立`summarizes`及Evidence。
- Qwen只返回实际引用的历史Sk编号；这些历史Event进入`support_event_ids`并建立`related_to`及Evidence。
- Cross-Event Prompt升级为v2；一次调用必须生成完整Cbuf摘要，不能以“没有显著模式”为由跳过整个窗口。
- 历史Event召回不再排除仅有`summarizes`关系的Cbuf，因此处理过但未形成跨时支持的Event仍可像StructMem一样作为未来Sk召回。
- 为控制云端重复LLM消耗，已经作为历史Sk实际引用并建立`related_to`的Event不再重复充当Sk；这是我们的资源约束改造，不是StructMem原始逻辑。
- 保留我们的数量触发、10分钟兜底、数据库Checkpoint、Token上限及结构化证据，这些属于面向云端多用户和资源控制的工程化改造，并非StructMem原始实现。

### 2026-09-12：Cross-Event历史Sk召回方案确认

**StructMem原始设计**

- 将时间窗口Cbuf中底层记忆条目的`memory`文本拼接成一个整体，再额外生成一个查询Embedding。
- 使用该向量从基础记忆向量库召回Top-K seed条目，并排除当前Cbuf。
- 命中seed后，再补齐与seed相同时间戳的其他条目，用于重建完整事件上下文。
- 已生成的Cross-Event Summary位于独立`summary_retriever`，不作为下一轮基础Sk参与递归蒸馏。

**DesayMem Light改造**

- 我们已经显式建立Event对象，因此Sk直接从历史Event中召回，不再从Fact条目命中后重建Event。
- 使用Cbuf内已有Event Embedding的中心向量查询历史Event，避免为拼接后的Cbuf文本额外调用一次BGE-M3。
- Fact向量库不参与Cross-Event的Sk召回；Fact向量用于Fact候选判断、Fact直接检索及构建Event前的L1能力。
- 已有Cross-Event不参与后续Cross-Event递归生成，只用于最终检索和Profile更新。
- 召回限制在完整用户/车辆身份范围、过去90天、Top-10；参数均可配置。

**修改原因**

- Event已经承担Fact的局部绑定，Cross-Event继续召回Fact会重复输入信息并增加Token。
- 复用Event Embedding可减少Embedding调用；历史时间窗和Top-K控制云端数据库与LLM成本。
- 避免Cross-Event递归总结Cross-Event，降低摘要漂移和证据链膨胀。
- 已知不足：多主题Cbuf的中心向量可能稀释语义，后续可评估按Tag或逐Event多路召回后合并去重。

### 2026-09-12：Cross-Event时间、Tag和证据结构确认

**StructMem原始设计**

- Summary时间范围取Cbuf第一条和最后一条记忆时间；另存生成时间、全部`covered_entry_ids`、全部`seed_entry_ids`及数量。
- 原实现不区分真正采用的历史Sk，也没有独立的Cross-Event Tag归一化与关系表。

**Memos参考设计**

- 借鉴其时间轴、Tag组织和按时间/Tag过滤思想；Memos不负责Cross-Event生成。

**DesayMem Light改造与原因**

- `occurred_at/occurred_end`只取全部Cbuf Event范围，历史Sk不扩大当前窗口时间；`observed_at`取生成时间。
- `covered_event_ids`记录全部Cbuf，`seed_event_ids`记录全部召回Sk，`support_event_ids`只记录Qwen实际引用的历史Sk。
- Cross-Event对全部Cbuf建立`summarizes`和Evidence，对实际引用Sk建立`related_to`和Evidence，增强追溯精度。
- Tag由全部Cbuf及实际引用Sk的已有Tag确定性合并、归一化、按频次排序并限制最多10个，不增加LLM调用。
- 保存`trigger_type/trigger_event_count/trigger_thresholds`，便于评估数量、时间和人工触发效果。
- 这些字段支持按时间与Tag检索，并控制PostgreSQL、JSON Mirror及Prompt体积。

### 2026-09-12：StructMem存储层级澄清

**StructMem原始设计**

- StructMem并未实现Fact、Event、Cross-Event三个独立向量库。
- `event`抽取模式会从Topic对话分别抽取factual条目和relational条目；这些条目带`topic_id/time_stamp/entry_type`等绑定信息，并逐条写入同一个基础`embedding_retriever`。
- Event在该实现中主要体现为同Topic、同时间上下文的绑定关系，不是我们这种独立持久化且拥有单独Embedding的Event记录。
- Cross-Event阶段从基础向量库取得时间窗口Cbuf；拼接Cbuf文本生成查询向量，再从同一基础库召回Sk，并按相同时间戳补齐相关条目。
- Cbuf与Sk经一次LLM生成Cross-Event Summary，Summary向量写入独立`summary_retriever`。
- 因此StructMem更准确地说有两个逻辑向量索引：基础记忆条目索引与Cross-Event Summary索引。

**DesayMem Light改造**

- 我们显式定义Fact、Event、Cross-Event三种MemoryRecord，并分别保存对应Embedding；当前物理上共用PostgreSQL`memory_items.embedding`，通过`memory_type`区分，而不是必须部署三个数据库。
- Fact由Mem0风格流程产生；一个Topic的Fact显式聚合成独立Event；若干Cbuf Event加可选历史Event Sk再生成Cross-Event。
- Cross-Event阶段不再把Fact与Event同时输入，防止重复信息和Token膙江；Fact通过`Event --contains--> Fact`保持可追溯。

**修改原因**

- 车载云端需要可独立检索Fact、具体Event和跨事件摘要，并通过PostgreSQL关系表精确追溯。
- 独立Event层避免StructMem按时间戳重建事件的隐式逻辑，更适合多用户、多Session和异步Worker。
- 共用一张主表和一个pgvector字段可以减少运维复杂度，同时保留三种逻辑层级。

### 2026-09-12：StructMem Topic绑定与DesayMem Event的对应关系

- StructMem不是把整个Topic压成一条记录和一个向量；它从Topic中抽取多条factual/relational MemoryEntry，每条单独生成Embedding并写入基础向量库。
- 同一Topic产生的多条Entry共享`topic_id`及相近时间，从而形成隐式的Event绑定；源码中的`topic_summary`字段目前主要为预留，并非核心独立Event向量。
- 因此，StructMem“同Topic Entry集合”在语义上近似DesayMem的Event，但单条factual Entry本身也近似Fact，不能说StructMem完全没有逐条Fact向量。
- StructMem缺少的是独立、可直接检索的Event主记录及Event Embedding，也没有Mem0式Fact历史候选决策和新旧关系。
- DesayMem将隐式结构显式化为`Topic → Fact → Event → Cross-Event`，代价是多保存一层Event向量；收益是Event召回、Checkpoint、证据关系和JSON Mirror更清楚。

### 2026-09-12：StructMem历史Sk可能只恢复部分Topic

- StructMem向量检索首先命中一条factual/relational Entry，因此命中结果可能只是某个Topic的一条信息。
- 原代码随后按命中Entry的精确`time_stamp`补齐同时间戳条目，而不是按`topic_id`加载该Topic全部Entry。
- 如果同一Topic的Entry来自不同消息并具有不同时间戳，Sk可能只恢复其中一个时间点的局部内容，不能保证完整Topic。
- DesayMem以显式Event为Sk检索单位，一次命中即可得到该Topic已经聚合的全部Fact内容；这是增加Event层的重要原因之一。
- 代价仍是额外Event记录和Embedding；第一版接受该成本，以换取Cross-Event输入完整性和稳定追溯。

### 2026-09-11：建立总体架构讨论图

- 新增 `docs/DESAYMEM_ARCHITECTURE_OVERVIEW.html`。
- 图中包含总体框架、写入沉淀、Cross-Event 与 Profile、查询召回、存储追溯五个视图。
- 图中标记当前两个 P0：Profile 事务生命周期、历史 Sk 排除过度。
- 后续讨论可先对照图定位模块，再把结论回写本文档。

### 2026-09-11：建立讨论记录

- 已记录当前总体架构、资源消耗和主要不足。
- 已确认后续讨论应逐模块进行，先明确设计，再修改代码。
- 下一项建议：先讨论并修复 Profile 事务生命周期问题。

### 2026-09-12：Profile 输入证据范围讨论

**TiMEM原始参考**

- TiMEM通过时间层级把下层记忆逐步压缩成高层用户摘要；L5更偏向周期性的单文档画像，不是可证据化的结构化条目权威库。
- 优点是读取简单、压缩率高；缺点是多值、条件、新旧冲突和原始证据追溯能力较弱。

**画像调研文档的建议**

- 采用 `Profile Item + Evidence Edge + Snapshot` 混合模型：Item是当前权威状态，Evidence连回原始观察，Snapshot是给Agent的有界文本投影。
- 画像不能只根据Cross-Event摘要更新，必须回到其覆盖或引用的Event/Fact证据。
- 活跃Item应直读，Snapshot由活跃Item重建，不应让旧Snapshot自我改写成唯一真源。

**DesayMem Light当前实现**

- 每次Cross-Event成功后触发一次Profile LLM，输入当前Item、当前Snapshot、Cross-Event和最多20个Event。
- LLM输出 `ADD/CONFIRM/COEXIST/SUPERSEDE/NOOP` 和新的叙述Snapshot；新Item才生成Embedding。
- 存在证据选取缺陷：当Cross-Event存在 `related_to` 历史Sk时，代码不再读取 `summarizes` 的当前Cbuf Event，可能使本轮新证据被排除。
- 当前Snapshot由LLM同时改写，而不是从更新后的全部active Item确定性重建。
- 代码第二阶段写入离开 `async with unit_of_work()` 后仍继续使用 `unit`，属于上云前必须修复的事务生命周期问题。

**建议的第一版改造**

- Profile证据固定取“全部当前Cbuf Event（`summarizes`）+实际引用的历史Sk（`related_to`）”，按Event ID去重后再做数量与Token截断。
- Cross-Event只用于提示候选规律，任何Item操作都必须引用Event证据。
- 暂时保留一次Profile LLM调用，但后续增加可配置的批处理触发，避免每个Cross-Event都立即调用。
- 第一版继续保留原子Item和Snapshot；Evidence Edge连到Event，后续再用 `observation_id` 防止Fact/Event/Cross-Event多层派生证据重复计数。

**改造原因**

- 保留TiMEM“高层压缩后低成本读取”的优点，同时弥补单文本画像不可精确更新、不可追溯的缺陷。
- 本轮Event是画像更新的新证据，历史Sk只是跨时支持，两者不能二选一。
- 分开Item权威态与Snapshot读取投影，便于多值共存、用户纠错、时间失效及后续业务精确消费。

### 2026-09-12：Profile更新触发时机

**参考方案**

- TiMEM的高层画像是下层记忆累积后的周期性压缩，不在每条对话上同步更新。
- 画像调研文档建议将更新移到后台批处理，显式偏好与隐式行为使用不同的准入规则。

**DesayMem Light当前方案**

- 每生成一个Cross-Event，立即创建一个 `profile_update` Job，因此Profile LLM调用数约等于Cross-Event生成数。
- Prompt虽可返回NOOP，但NOOP仍然已消耗一次LLM调用。

**第一版建议**

- 在Cross-Event与Profile Pipeline之间增加可插拔 `ProfileTriggerPolicy`，按完整 `tenant_id + user_id + vehicle_id` scope独立计数。
- 采用“数量阈值 + 最长等待时间 + 手工强制”三种触发，任一满足即提交Profile Job。
- 主要超参：`profile_trigger_cross_event_count`、`profile_trigger_max_wait_seconds`、`profile_max_cross_events_per_run`、`profile_trigger_enabled`。
- 测试建议：`count=1`、`max_wait=600`，便于快速观察每个Cross-Event的画像结果。
- 云端试运行起始建议：`count=3`、`max_wait=1800`，再根据LLM成本、NOOP率和画像延迟调整；这是初始值，不是固定业务结论。
- 数量触发后将Checkpoint之后的若干Cross-Event一次交给Profile Updater；时间触发需延迟Job保证低频用户不永久沉积不了。
- Trigger只决定“何时调用”；是否实际修改Profile Item，仍由显式/隐式证据准入和 `NOOP` 判断决定。

**原因**

- 比每个Cross-Event立即调用更节省Qwen Token，又不会让低频车辆长期等不到数量阈值。
- 不把车载业务参数写死，便于压测环境和实际车队分别调优。
- 以完整scope隔离触发状态，避免多用户、多车并发时相互推动计数。

### 2026-09-12：第一版Profile简化为固定Preference Schema + 摘要

**参考方案**

- LangMem Profile支持预定义Schema，适合字段范围明确、只关心当前状态的场景。
- AWS AgentCore将偏好抽取与旧偏好合并分开，并允许对一次性、临时或过度推测内容返回SkipMemory。
- TiMEM高层记忆保留自然语言用户摘要，便于直接向LLM提供全局用户背景。
- 画像调研建议对会改变车控、导航、搜索和推荐结果的高价值字段建立属性注册表，开放兴趣不需要建立庞大本体。

**第一版DesayMem Light方案**

- 固定的是Preference字段和类型，不是预先枚举用户的具体值；值仍由Qwen从Event证据中抽取。
- 第一版仅保留少量车载高频类别：音乐、餐饮、空调/座椅、导航/地点、对话与服务偏好。
- 多值偏好用列表或多个原子Item共存，例如音乐歌手可同时为周杰伦和陶喆；空调温度等条件值保留场景条件。
- 同一次Profile LLM返回两部分：受限的Preference更新操作，以及给Agent使用的简短 `profile_summary`，不增加第二次LLM调用。
- 程序校验Preference的字段名、数据类型、取值范围与证据Event ID；Qwen只负责语义抽取、共存或替换判断。
- 未落入固定Preference Schema的信息仍保留在Fact/Event/Cross-Event，不为了画像强行增加字段。

**简化原因**

- 车载系统的个性化消费方相对明确，固定Schema比完全开放的attribute/value更容易测试、校验和接入业务。
- 同时保留Profile摘要，避免结构化字段无法覆盖的用户背景完全丢失。
- 结构化Preference是可执行当前态，Profile Summary是低成本语言投影，Fact/Event仍是可追溯历史，三者职责分开。

### 2026-09-12：第一版固定Preference字段

**参考与边界**

- 画像调研的原则是：没有明确消费方、不能改变业务决策的信息，不应进入第一版结构化画像。
- 参考LangMem的Schema约束思路，但不采用一个无限扩张的大JSON；参考TiMEM保留一段补充性高层摘要。

**建议字段**

- `music.artist`：多值字符串，用于音乐搜索和排序。
- `music.genre`：多值字符串，用于音乐推荐。
- `food.cuisine`：多值字符串，用于餐厅筛选和排序。
- `food.restaurant`：多值字符串，用于餐厅搜索与“最喜欢”查询。
- `cabin.temperature`：数值+单位+条件，用于空调参数建议；第一版不仅凭弱隐式证据自动执行。
- `navigation.destination`：多值地点，用于常用地点解析；实际历史行程仍存Event，不全部画像化。
- `navigation.route_option`：多值枚举，例如避免收费、优先高速。
- `interaction.response_style`：多值枚举，例如简洁、详细、少主动提示。

**通用数据外壳**

- 每个偏好仍使用统一Item结构：`attribute` 、`value`、`conditions`、`scope`、`status`、`evidence_event_ids`、`first_observed_at`、`last_confirmed_at`。
- `vehicle_id=null` 表示用户跨车通用；具体车辆的空调、座椅或设备偏好使用当前 `vehicle_id`。
- 第一版不将性格、职业推测、敏感特征和一次性状态写入固定Preference。

**与当前实现的差异**

- 当前 `attribute/value` 是完全开放文本；改造后由可配置Attribute Registry校验字段、类型、多值策略和允许条件。
- 未命中Registry的Qwen输出不写入Preference Item，但原始Event仍保留，后续扩展Schema后可重新蒸馏。

### 2026-09-12：是否新增独立Preference表

**参考来源**

- LangMem区分Profile与Collection：Profile可使用预定义Schema保存当前态，Collection保存独立记忆条目。
- AWS AgentCore User Preference将偏好作为独立Memory Record，抽取后再执行Add、Update或Skip。
- MemoryOS和TiMEM更接近“每个用户一段高层画像文本”，读取简单，但字段级更新与证据追溯较弱。
- 调研文档综合后推荐 `Profile Item + Evidence Edge + Snapshot`；这支持“独立偏好条目+文本投影”，但不强制必须使用一张新的物理表。

**三种数据库选择**

- 复用现有 `profile_items`：只增加Attribute Registry和类型/条件校验；改动最小，且现有Evidence、Relation、Snapshot全部可复用。
- 新增 `preference_items` 并保留 `profile_items`：概念清晰，但两套Item、Evidence和同步逻辑容易重复，第一版不建议。
- 将 `profile_items` 正式改造/迁移为 `preference_items`：命名更准确，但需要数据迁移、JSON Mirror变更与兼容处理，可在Schema稳定后执行。

**当前建议**

- 第一版不同时保留两张语义重复的表。
- 先复用 `profile_items` 作为结构化Preference权威表，`profile_snapshots` 作为自然语言Profile读取投影，并在代码接口层统一命名为Preference Item。
- 如果为汇报或长期数据模型希望语义更明确，后续应把旧表迁移为 `preference_items`，而不是再新增一张并行表。

### 2026-09-12：Profile批次证据与Token预算

**参考方案**

- TiMEM使用本周期新增下层记忆、少量历史高层记忆和旧高层摘要生成新摘要，避免全量历史输入。
- 画像调研建议“新下层记忆 + 少量相关历史 + 上一版画像”，同时要求画像操作回指可追溯证据。

**DesayMem Light改造**

- 一次Profile批次输入Checkpoint之后、本次实际装箱的Cross-Event，不读取全量Cross-Event历史。
- 每个Cross-Event的证据池由当前Cbuf Event（`summarizes`）与实际引用的历史Sk（`related_to`）合并，按Event ID全局去重。
- 只进行一次Profile LLM调用；输入包含上一版有界Snapshot、相关active Profile Item、本批Cross-Event及装箱后的Event证据。
- 采用Token预算为主、数量上限为保险；超限时在各Cross-Event间轮询分配证据配额，避免前一条Cross-Event占满全部预算。
- 优先装入本批Cbuf Event，再装入历史Sk；任何画像操作只能引用已装入Event。
- Profile Checkpoint只推进到已完整装箱并处理的最后一个Cross-Event；未装入的Cross-Event留给下一批，防止Token截断造成永久遗漏。

**超参建议**

- `profile_max_input_tokens=6000`：Profile单次输入预算。
- `profile_max_output_tokens=1000`：结构化操作与Snapshot输出预算。
- `profile_max_cross_events_per_run=5`、`profile_max_evidence_events=20`：数量保险上限。
- `profile_relevant_items_per_cross_event=5`：使用已有Cross-Event/Profile Item向量召回相关旧Item，不新增Embedding调用。
- 上述均为测试初值，云端应根据实际Token统计和遗漏率调整。

**与原架构的差异及原因**

- TiMEM主要迭代高层文本；我们要求每个Item引用Event ID，用于车载误判解释和用户纠错。
- 当前代码全量输入active Item且只处理一个Cross-Event；改造后批处理新Cross-Event并只取相关Item，控制长期运行时的Token增长。
- 公平装箱和Checkpoint前缀推进，是为了在限制成本的同时不让某个用户的后续证据被静默丢弃。

### 2026-09-12：可变长期Profile + 固定Schema Preference双轨设计

**别人怎么做**

- LangMem区分Profile与Collection：Profile是面向任务、可直读的当前状态，Collection是持续增长的独立记忆集合；Profile可用自定义Schema，但过大后局部更新容易出错。
- AWS AgentCore将User Preference、Semantic、Summary和Episodic记忆分成不同策略；偏好先Extraction，再Consolidation为Add、Update或Skip，说明稳定偏好不应等同于任意高层摘要。
- MemoryOS的User Profile更接近每用户一段自然语言文本，适合整体注入，但不擅长字段级偏好与证据管理。
- TiMEM把对话逐级蒸馏到L5稳定Persona Profile，但其核心是高层抽象记忆，不是面向车控参数的固定Preference Registry。

**我们怎么改**

- 保留一份字段不固定、自然语言的 `Profile Snapshot`，描述长期兴趣、习惯、目标和交互特征，由Qwen随新证据逐版蒸馏。
- 同时保留一组固定Schema的 `Preference Item`，仅承载音乐、餐饮、舱内、导航和交互等必须被业务精确消费的偏好。
- 第一版不必新建重复表：现有 `profile_items` 承载固定Preference，`profile_snapshots` 承载可变长期Profile文本，`profile_item_evidence` 连回Event证据。
- 一次后台Profile任务中，Qwen同时输出 `preference_operations` 和 `profile_summary`，双轨不增加LLM调用次数。
- Preference必须通过Attribute Registry和证据校验；开放Profile也只能基于本批Cross-Event/Event及旧Snapshot更新，并保留版本和来源Cross-Event ID。

**为什么这样改**

- 只有开放Profile：表达灵活，但多歌手喜好、条件温度等难以被业务稳定精确读取。
- 只有固定Preference：可执行，但无法容纳未预先建模的长期兴趣、目标和用户背景。
- 双轨将“给LLM理解用户”与“给车机确定执行”分开：Profile Snapshot用于对话与泛化，Preference Item用于精确查询、过滤、排序和参数补全。
- 这不是重复保存两套历史，而是对同一批Event证据建立两种用途不同的读取投影。

### 2026-09-12：开放Profile与固定Preference的更新准入

**参考方案**

- TiMEM通过多层时间蒸馏形成稳定Persona，说明高层Profile应来自累积证据，而非每轮对话即时改写。
- AWS AgentCore区分显式偏好与隐式偏好，一次性事件、临时状态、重复内容和过度推测应Skip；其公开方案没有给出通用的固定次数门槛。
- 画像调研因此建议：显式偏好与隐式行为使用不同准入；隐式偏好的独立观察数和跨日要求属于需用真实车载数据校准的工程参数。

**开放Profile准入**

- 用户明确声明的稳定身份、长期目标或交互方式，一份明确Event证据可更新。
- 由行为推测的兴趣、习惯或特征，必须达到可配置的独立Event数和跨日数。
- 一次性行程、临时情绪、当前车内状态、敏感属性推测不进入长期Profile。
- 没有有效新信息时保留旧Snapshot，不因批处理触发而强制改写。

**固定Preference准入**

- `explicit`：用户明确说“我喜欢……”、“以后……”或明确纠正，一份用户Event证据即可写入或替换。
- `implicit`：仅有点播、调温、导航等行为时，必须达到独立Event数和跨日数；单次操作只保留在Event。
- 固定Schema外的候选不写Preference；与当前Item相同时CONFIRM，多值属性新值COEXIST，只在用户明确否定或替换时SUPERSEDE。
- 弱隐式Preference最多用于建议或排序，不直接执行空调、座椅等车控动作。

**第一版超参初值**

- `profile_explicit_min_events=1`。
- `profile_implicit_min_events=3`。
- `profile_implicit_min_distinct_days=2`。
- `profile_evidence_window_days=30`。
- 功能测试可临时设为 `implicit_min_events=2` 和 `distinct_days=1`，但不作为生产默认值。

**我们的实现方式与原因**

- 一次Qwen调用同时返回 `profile_changed/profile_summary` 和带 `evidence_type`、Event编号的Preference操作，不因双轨新增LLM次数。
- Qwen判断语义是显式还是隐式；程序根据Event ID、日期、主体和超参确定是否达到准入，避免让LLM自己计数。
- 这比TiMEM单纯高层蒸馏多了可执行Preference，比AWS通用偏好策略多了车载多用户、跨日和车控安全限制。

### 2026-09-12：开放Profile的保留、修正、删除与防漂移

**参考方案**

- MemoryOS将旧用户画像文本与新的中期记忆交给LLM，返回完整新画像；实现简单，但局部内容可被全文改写。
- TiMEM使用新下层记忆和少量历史L5递进生成新L5，可保持长期连续性，但摘要误差仍可随层级传播。
- LangMem Profile允许生成完整新Profile或局部Patch，并指出Profile变大后更新容易出错，需要Schema、拆分或结构化解码。

**第一版DesayMem Light方案**

- Profile Snapshot仍保存一段字段不固定的自然语言全文，避免第一版引入开放画像条目库或知识图谱。
- Qwen输入只包含上一版Snapshot、本批Cross-Event和可验证Event，输出完整新Snapshot，同时输出 `profile_action=KEEP|UPDATE`、`change_reason`和 `evidence_event_numbers`。
- `KEEP`时程序直接复用旧Snapshot，不新建重复版本；`UPDATE`时才写入新版本。
- Prompt要求最小改写：保留本批证据未触及的旧内容，只添加被新证据支持的稳定特征。
- 只有用户明确纠正/撤销，或达到隐式反向证据门槛，才允许修正或删除旧画像内容；单次反常行为不得改写长期Profile。
- Snapshot记录 `version`、`previous_version`、`source_cross_event_ids`、`change_reason`、`created_at`、`model`和 `prompt_version`，旧版不覆盖，支持审计与回滚。
- 使用 `expected_version` 乐观并发控制；版本冲突时重读最新Snapshot后重新计算，防止多Worker相互覆盖。

**资源与长度控制**

- `profile_summary_max_chars=600`作为中文测试初值，并保留Token上限；超出时要求Qwen压缩，不直接截断字符串。
- Profile只保留长期且对个性化有用的描述，具体历史细节由Event/Cross-Event承担。
- 第一版不做周期全量重建；后续如发现摘要漂移，可增加低频“从有效Preference+可追溯Cross-Event重建Profile”的校准Job。

**为什么这样改**

- 保留MemoryOS/TiMEM全文画像易注入、表达自然的优点，但以KEEP、最小改写、证据编号和版本链降低递归摘要漂移。
- 开放Profile不是事实真源；如果文本出错，仍可依据Event证据重建，而不会污染原始记忆。

### 2026-09-12：双轨画像更新案例

- 旧Profile：“用户工作日经常自驾通勤，希望车机回复简洁。”
- 旧Preference：`interaction.response_style=简洁`。
- 新Event E1：用户明确说“我喜欢周杰伦和陶喆”；E2：本周第二天在通勤时播放周杰伦；E3：用户说“今天有点冷”。
- Profile Updater应将“喜欢华语流行音乐”最小追加到Profile，保留原有通勤和回复风格；E3属于临时状态，不进Profile。
- Preference Updater依据E1一次显式证据新增 `music.artist=周杰伦` 和 `music.artist=陶喆`；E2可作为周杰伦偏好的后续隐式支持，但不独立创建新偏好；E3不生成温度偏好。
- 结果是一个新Profile Snapshot、两个新Preference Item以及指向E1的Evidence Edge；原Event不修改。

### 2026-09-12：Profile、Preference与历史记忆的查询路由

**参考方案**

- LangMem Profile通常按user namespace直接读取，Collection则按问题检索；说明当前态和历史集合不应使用同一种召回方式。
- AWS User Preference是独立偏好记录，适合按作用域/类别获取；TiMEM支持先使用高层记忆，再根据需要向下层回溯。
- Memos的Tag与时间组织适合“上周去过哪里”类历史查询，而不是读取Profile来回答。

**DesayMem Light路由**

- `profile`意图：询问长期兴趣、习惯或“你了解我什么”，直读当前Profile Snapshot，必要时追加少量相关Event。
- `preference`意图：询问喜欢的歌手、餐厅、温度或路线选项，按 `attribute + scope + status=active` 精确读取Preference Item，不依赖向量Top-K。
- `history`意图：询问上周去过的餐厅、昨天播放过的歌曲，使用时间范围、Tag和语义检索Event/Fact，不用Preference代替真实历史。
- `general`意图：通用对话按Token预算注入简短Profile，并召回相关Cross-Event/Event；默认不全量注入所有Preference。
- `action`意图：车控或导航执行前，只读业务声明的 `required_attributes`；显式或已确认偏好可补全参数，弱隐式偏好只能建议或询问确认。

**为什么改**

- 当前QueryPlanner只有 `general/profile/history/ranking`，需扩展 `preference/action` 或为现有意图增加 `required_profile_attributes`。
- 精确Preference直读解决向量召回漏掉当前偏好的问题；Profile有界注入控制Token；Event的时间/Tag检索保持历史问答的事实性。

### 2026-09-12：Query Planner误判与降级设计

**当前代码**

- 只当查询命中“上周、去过、最喜欢、偏好”等 `trigger_terms` 时才调用Qwen Planner；其他查询直接使用general。
- Qwen输出单一 `general/profile/history/ranking` intent、查询改写、Tag和时间范围。
- 关键词未覆盖的口语化表达可能不进Planner，单一intent也可能错过需要Profile与历史联合检索的问题。

**参考原则**

- LangMem将Profile直读与Collection检索分开，但不要求一个问题只能选择其一。
- TiMEM的层级召回可在不同层级间查找，不把查询永久限定在单层。

**DesayMem Light改造**

- 将单一intent改为多路需求：`needs_profile`、`preference_attributes`、`needs_history`、`needs_ranking`、`time_range`和 `tags`；同一问题可同时选中多路。
- Planner不作为硬开关：无论规划结果如何，都保留Session/Last-K与基础Event/Cross-Event语义检索；Planner只追加精确Preference、时间和Tag通道。
- 车控、音乐、导航等上游Agent如已知消费域，应直接传入 `required_profile_attributes`，跳过Planner猜测；开放问答才使用Planner。
- Planner结构校验失败、超时或输出未知attribute时，降级为“基础混合检索+有界Profile Snapshot”，不直接返回空记忆。
- 第一版保留当前关键词门控以节省Qwen调用，但将它明确作为可配置插件和已知召回率缺陷。

**为什么**

- LLM分类一定存在误判，不应让错误intent关闭所有正确记忆通道。
- 多路需求能处理“用我上周去过的餐厅，推荐一家符合我口味的”这类Profile+历史+排名混合问题。
- 上游显式声明消费字段最可靠，可同时减少LLM调用和误判。

### 2026-09-12：取消Query Planner关键词规则门控

**对前案的修正**

- 不再使用 `trigger_terms` 决定是否进入Planner。关键词法对“来点合我口味的”、“还去上回那家吧”等车载口语表达泛化性不足。
- 所有没有显式检索计划的自然语言记忆查询，统一调用一次轻量Qwen Planner，输出多路检索需求。
- 只有当上游Agent已经显式传入 `required_profile_attributes/time_from/time_to/tags`等完整约束时才跳过Planner；这是接口契约，不是文本规则。

**成本控制**

- Planner使用短Prompt、结构化JSON、`temperature=0`和较小输出上限，不输入历史记忆，单次只做查询解析。
- 保留可配置 `query_planner_enabled`、`query_planner_max_output_tokens`、`query_planner_timeout_ms`和请求级规划结果TTL缓存。
- Planner失败时仍降级到基础混合检索，不使用新的关键词规则补判。

**参考与改造原因**

- LangMem和TiMEM都依赖语义/层级记忆选择，不把开放问法限定为少量手工关键词。
- 我们使用Qwen结构化多路规划，以换取中文口语、指代、复合意图和后续新Preference类别的泛化性。
- 代价是每个自然语言检索最多增加一次小LLM调用；这一成本必须通过实际云端统计与召回率收益评估，而不用规则牺牲泛化性。

### 2026-09-12：可选Embedding轻路由与Qwen级联

**参考方案**

- Aurelio Labs Semantic Router使用Embedding表示路由示例，按语义相似度选择路由，目标是用语义决策减少对LLM分类的依赖，而非使用关键词。
- LMSYS RouteLLM研究在较弱/廉价模型与较强/昂贵模型之间学习路由，其可借鉴原则是：容易请求走低成本路径，不确定请求升级强模型。
- 这些方案提供的是语义路由与级联思想，不是现成的车载记忆检索路由；我们需要按Profile/Preference/History通道做工程改造。

**DesayMem Light方案**

- 将Query Router设计为可插拔策略：`qwen_always`、`embedding_cascade`、`upstream_plan_only`。
- `embedding_cascade`为每个语义通道维护若干问法示例，使用BGE-M3预先生成并缓存路由向量；查询不做关键词匹配。
- 查询Embedding与基础向量检索共用，先对Profile、Music Preference、Food Preference、Cabin Preference、Navigation Preference、History、Ranking等路由示例计分。
- 只有最高路由分数足够高、且与次高分差距足够大时，才直接生成简单检索计划；多意图、分数接近、需要解析相对时间或任何不确定情况升级到Qwen Planner。
- Qwen结果仍是最终的复杂查询规划；轻路由只用于高把握的单通道查询，失败时不会取代基础混合检索。

**初始超参**

- `query_router_mode=qwen_always|embedding_cascade|upstream_plan_only`。
- `query_router_similarity_threshold=0.75`、`query_router_margin=0.08`；两者只是起始值，必须用BGE-M3和真实中文车载问法校准。
- `query_router_examples_per_route=20`、`query_router_cache_ttl_seconds=3600`。
- 必须记录 `route_source=embedding|qwen|upstream`、路由得分、margin、Qwen升级率、最终召回命中和误路由样本。

**为什么这样改**

- 复用已部署的BGE-M3和原本就需要的查询Embedding，高把握查询可不增加Qwen调用。
- 与关键词规则相比，语义示例能泛化到口语、同义表达和部分指代；与每轮Qwen相比，可降低LLM成本与延迟。
- 保守阈值和Qwen升级保留复合意图、时间计算与新问法的泛化性。

### 2026-09-12：BGE-M3轻路由的参考、改造与云端并发边界

**明确参考来源**

- Semantic Router：参考“路由由多条示例utterance表示，通过Embedding语义相似度做低延迟选择”；不参考具体记忆Schema。
- RouteLLM：参考“容易请求走低成本路径，难例/不确定请求升级强LLM”的级联思路；我们不是在两个对话模型间路由，而是在BGE-M3语义路由与Qwen Planner之间路由。
- BGE-M3：参考其多语言、dense/sparse/multi-vector多功能表示能力；本项目第一版轻路由仅使用dense向量。
- LangMem/TiMEM：参考Profile与下层记忆可分路读取、复杂问题可跨层召回；路由不能把问题永久锁死在单一层。

**DesayMem Light的车载改造**

- 使用BGE-M3将Profile、各类Preference、History和Ranking问法示例预编码为路由向量，查询与这些示例/路由中心计算相似度。
- 高置信单路问题直接得到检索通道；多路、分数接近或低置信请求升级Qwen。
- 不使用用户身份生成路由向量；路由计算是全局无状态的，真正检索时再使用 `tenant_id/user_id/vehicle_id` 做数据隔离。

**BGE-M3并发与资源结论**

- 可以使用BGE-M3。本系统原本就需要为用户查询生成向量做Event/Cross-Event召回，轻路由应复用同一个query embedding，不额外调用第二次BGE-M3。
- BGE-M3 dense输出为1024维；假设140个路由示例，float32原始向量仅约0.55MB，可常驻worker内存，矩阵点积成本远小于模型推理。
- 多用户并发下的主要成本是BGE-M3查询编码，不是路由比较；应使用独立Embedding Service、动态micro-batching、有界队列、超时和多副本水平扩展。
- 轻路由仅使用dense短查询编码，不在路由阶段开启sparse或multi-vector/ColBERT输出，避免无必要的显存、网络与序列化开销。
- 无法在未知GPU型号、服务框架、batch size和查询长度时给出可信的QPS；上云后必须对p50/p95/p99延迟、队列长度、GPU利用率和Qwen升级率做压测。

**建议部署超参（仅作起始值）**

- `embedding_dense_only=true`、`embedding_normalize=true`。
- `embedding_batch_max_size=32`、`embedding_batch_wait_ms=5`；根据GPU压测调整。
- `embedding_timeout_ms=500`、`router_timeout_ms=50`；超时时可选升级Qwen或降级基础检索。
- Query Embedding缓存使用有界LRU+TTL，缓存key由模型版本、归一化查询和编码选项组成，不使用无界全量缓存。

### 2026-09-12：Profile与查询模块后续讨论/实施清单

**已确认**

- 采用开放Profile Snapshot + 固定Schema Preference Item双轨设计，共用一批Event证据和一次Qwen更新。
- Profile使用数量+时间+手工强制触发，参数可配置。
- 显式与隐式证据分开准入，次数和跨日由程序校验。
- 查询不使用关键词规则；保留Qwen Planner，可选BGE-M3语义轻路由，低置信时升级Qwen。

**仍需讨论并确认**

1. Preference/Profile最终数据结构：字段、多值、conditions、user/vehicle scope、版本和Evidence。
2. Profile批处理的精确输入和Checkpoint推进：一批多少Cross-Event，Token超限时怎样不遗漏。
3. Qwen Profile Prompt与JSON契约：Profile最小改写、Preference操作、证据编号和explicit/implicit类型。
4. QueryPlan最终契约：多路需求字段、上游直传契约、BGE路由示例格式和Qwen升级条件。
5. 检索结果装配：Session、Preference、Profile、Cross-Event、Event和Fact的优先级与Token上限。
6. 用户纠错/删除：删除Preference、回滚Profile、证据级联、缓存与JSON Mirror同步。
7. 评测指标与测试集：画像正确率、偏好误写率、历史召回率、路由升级率、Token和p95延迟。

**编码前P0**

- 修复Profile Pipeline离开UnitOfWork上下文后仍使用 `unit` 的事务生命周期问题。
- 修复Profile证据只在 `related_to` 与 `summarizes` 中二选一的问题，应合并本批Cbuf Event和实际引用的历史Sk。
- 解决当前Profile强制 `vehicle_id` 的作用域问题，允许音乐/餐饮等用户通用偏好在 `vehicle_id=null` 时跨车读取。

### 2026-09-12：Preference/Profile最终数据结构讨论

**参考方案**

- AWS AgentCore Preference Record将context、preference和category分开，并对新旧偏好做Add/Update/Skip，支持独立偏好条目。
- LangMem Profile Schema说明可执行字段应结构化；Collection模式支持各条记忆独立更新。
- Zep/Graphiti的时序有效性思路支持 `valid_from/valid_to` 保留新旧状态，但我们第一版不引入图数据库。
- 画像调研推荐Profile Item、Evidence Edge和Snapshot，并将多值、条件、作用域、历史有效期和版本视为一等字段。

**当前代码**

- `profile_items` 已有attribute/value、embedding、status、first/last time、valid_from/to、confirmation_count、model和prompt_version。
- `profile_item_evidence` 已能将Item连到Event和Cross-Event，但只有support/contradict，没有explicit/implicit类型。
- `profile_snapshots` 已有scope、version、summary、model和prompt_version，但缺少上一版、变更原因和来源Cross-Event。
- `vehicle_id` 和 `occupant_id` 当前强制非空，跨车通用偏好不能被正确表达。

**第一版最小改造**

- 复用 `profile_items` 作为Preference Item表，保留 `value` 文本以兼容检索和展示，增加 `value_json JSONB` 用于温度、单位、地点等结构化值。
- 增加 `conditions JSONB NOT NULL DEFAULT '{}'`，保存季节、驾驶员角色、时段等条件，并由Attribute Registry校验。
- 增加 `scope_type=user|vehicle` 和 `version INTEGER`；user scope允许 `vehicle_id=NULL`，vehicle scope必须有vehicle_id。
- `profile_item_evidence` 增加 `evidence_kind=explicit|implicit`；现有support/contradict仍作为独立 `evidence_role`。
- `profile_snapshots` 增加 `previous_snapshot_id`、`change_reason` 和 `source_cross_event_ids`；summary仍是字段不固定的自然语言全文。
- 保留 `profile_snapshot_items`，表示该Snapshot生成时的active Preference集合，便于历史回放。

**核心结构**

- Preference Item：`id/scope_type/tenant_id/user_id/vehicle_id/occupant_id/attribute/value/value_json/conditions/status/version/valid_from/valid_to/model/prompt_version`。
- Evidence Edge：`profile_item_id/event_id/via_cross_event_id/evidence_role/evidence_kind/created_at`。
- Profile Snapshot：`id/scope/version/summary/previous_snapshot_id/change_reason/source_cross_event_ids/model/prompt_version/created_at`。

**为什么这样改**

- 不新建重复表，保留现有数据、Evidence、Relation和JSON Mirror链路，降低第一版迁移风险。
- `value_json+conditions` 让Preference可被车机精确消费，同时保留文本value供LLM和搜索使用。
- `scope_type+nullable vehicle_id` 区分跨车用户喜好和当前车辆的设备偏好。
- Evidence的role与kind分开，才能表达“这是隐式行为，且它与当前偏好相反”。
- version用于乐观并发更新，避免多Worker对同一用户画像相互覆盖。

### 2026-09-12：Profile批处理与Checkpoint

**参考方案**

- TiMEM使用本周期新增下层记忆与少量历史高层记忆递进更新Profile，而不每条对话更新。
- StructMem使用Cbuf和Checkpoint保证当前时间窗被完整巩固；我们借鉴“只推进已完整处理前缀”的思路，但Profile的输入单位是Cross-Event。

**第一版DesayMem Light方案**

- 以一个“完整Cross-Event包”为最小处理单位：Cross-Event本身 + 全部 `summarizes` 当前Cbuf Event + `related_to` 历史Sk Event。
- Profile Worker从Profile Checkpoint之后按时间顺序读取待处理Cross-Event，逐包计算Token，只将能完整装入的连续前缀交给Qwen。
- 不拆分Cross-Event包；超出预算的后续包留到下一批，Checkpoint只推进到本次成功处理的最后一个Cross-Event。
- 多个包引用同一Event时按Event ID去重，但保留Cross-Event到Event的映射，便于Qwen证据引用。
- 单个包超限时，先截减历史Sk，必须保留Cross-Event和全部当前Cbuf Event；由于当前Cross-Event已有Cbuf数量上限，正常情况下应能装入。
- 如仍无法装入，任务明确报错并保留Checkpoint，不静默截断当前证据。

**初始超参**

- `profile_trigger_cross_event_count=3`、`profile_trigger_max_wait_seconds=1800`。
- `profile_max_cross_events_per_run=3`，与数量触发对齐，保持第一版简单。
- `profile_max_input_tokens=6000`、`profile_max_output_tokens=1000`。
- `profile_max_history_events_per_cross_event=3`，只限制可选历史Sk，不限制当前Cbuf Event。
- 功能测试可使用 `trigger_count=1`、`max_wait_seconds=600`，其他打包逻辑不变。

**与原方案的差异和原因**

- TiMEM的输入为分层摘要；我们为了Preference可追溯，除Cross-Event外必须携带当前Event证据。
- 完整包比在20个Event中全局截断更简单，也不会出现Checkpoint已推进但某个Cross-Event证据只输入一半的情况。
- 历史Sk用于跨时支持，当前Cbuf才是本轮新证据，所以Token不足时先减历史Sk。

### 2026-09-12：Qwen Profile单次调用契约

**参考方案**

- Mem0参考点：用受限操作表达新旧记忆的新增、确认、共存、替换或无操作，不让LLM直接写数据库。
- AWS AgentCore参考点：偏好Extraction与Consolidation使用结构化输出，明确区分新偏好、旧偏好和Skip。
- LangMem参考点：Profile可使用JSON Schema/结构化解码限制输出。
- TiMEM参考点：保留可直接交给Agent的高层自然语言Profile摘要。

**输入契约**

- `CURRENT_PROFILE`：当前Snapshot版本和全文。
- `CURRENT_PREFERENCES`：与本批Cross-Event相关的已编号active Item，包含attribute、value、conditions、scope、确认数和版本。
- `NEW_CROSS_EVENTS`：本次完整装箱的已编号Cross-Event，包含时间和关联Event编号。
- `EVIDENCE_EVENTS`：全局去重后的已编号Event，包含content、occurred_at/end、tags以及 `current|history` 来源角色。
- Qwen只能引用上述编号，不接触真实UUID，服务端在校验后转换。

**输出契约**

- 顶层只有 `profile` 和 `preference_operations`。
- `profile` 输出 `action=KEEP|UPDATE`、完整 `summary`、`change_reason` 和 `evidence_event_numbers`。
- `preference_operations` 每项输出 `action=ADD|CONFIRM|COEXIST|SUPERSEDE`、attribute、value/value_json、conditions、可选scope_type、可选existing_item_number、`evidence_kind=explicit|implicit` 和evidence_event_numbers。
- 没有可写入偏好时返回空操作数组，不需要为每个候选输出NOOP。

**程序端确定性校验**

- attribute必须存在Attribute Registry；value_json、conditions和scope必须符合该attribute的Schema。
- Event编号和Item编号必须存在当前请求，同一Event ID只计一份独立观察。
- explicit/implicit的次数、跨日和时间窗由程序计算，Qwen输出只是语义候选，不能自己越过准入门槛。
- user/vehicle scope以Attribute Registry默认值为主；Qwen只能在证据明确指定“这辆车”等限制时建议vehicle scope。
- `KEEP` 必须与旧summary完全一致且无change evidence；`UPDATE` 必须引用有效Event并满足长度上限。
- 单个无效Preference operation可拒绝并记录审计，不应使已校验的Profile或其他操作全部失败；但伪造证据编号、顶层JSON破损等整体契约错误应使任务失败且不推进Checkpoint。

**与原架构的差异与原因**

- 当前契约只有一个Cross-Event、一段summary和开放attribute/value操作；新契约支持Cross-Event批次、双轨输出、固定Attribute Registry、结构化值和证据类型。
- 将语义判断交给Qwen，将身份、编号、类型、次数、时间和并发交给程序，可在保留泛化性的同时防止LLM直接污染数据库。
- 双轨共用一次Qwen调用，比分别生成Profile和Preference少一次LLM开销。

### 2026-09-12：QueryPlan统一契约

**参考方案**

- LangMem的Profile直读与Collection检索说明当前态和历史记忆需要不同召回通道。
- TiMEM的层级召回说明一个问题可以同时需要高层Persona与下层事件。
- Memos的Tag和时间组织适合为历史查询生成tags和time range。
- Semantic Router/RouteLLM提供轻路由与强模型升级思路；我们要求轻路由和Qwen都输出同一份检索契约。

**统一QueryPlan**

- `semantic_query`：用于Fact/Event/Cross-Event向量检索的简短改写查询。
- `needs_profile`：是否注入最新开放Profile Snapshot。
- `preference_attributes`：需要精确直读的固定Preference字段列表。
- `needs_history`：是否强化Event/Fact历史检索。
- `needs_ranking`：是否需要对多个历史观察做频次/近期性统计，而非仅返回向量Top-K。
- `time_from/time_to`：可选绝对时间范围，必须带时区。
- `tags`：可选宽泛语义Tag，用于与向量召回交集/加权。

**Qwen Planner输入**

- 当前query、当前时间与时区、Attribute Registry中可用字段名，以及最多4条已有Session消息用于解析“上次那家”等指代。
- 不输入Profile、Preference或长期记忆内容；Planner只决定去哪里查，不先看答案。
- 不输出置信度；LLM自报置信度不作为可靠的业务阈值。

**计划来源**

- `upstream`：上游已给出完整约束，校验后直接使用。
- `embedding`：BGE-M3只在高置信的单路语义上填充有限字段；不负责复杂相对时间计算。
- `qwen`：处理低置信、多路、指代和时间问题，输出完整QueryPlan。
- `fallback`：任何Planner失败时使用原query做基础混合检索，不关闭Session或Event通道。

**程序校验与边界**

- `preference_attributes` 必须存在Registry，未知字段丢弃并审计。
- 时间必须满足from<=to且两端带时区；如时间无效，不应带错误时间去查数据库。
- tenant/user/vehicle/occupant作用域永远来自SearchRequest和鉴权上下文，不由Qwen输出或修改。
- 基础语义检索始终存在；`needs_*` 字段只追加Profile、Preference、时间和Ranking通道。

**缓存键**

- QueryPlan缓存key必须包含规范化query、最近Session上下文摘要或哈希、时区、当前日期桶、Attribute Registry版本和Planner版本。
- 相对时间问题不能只按query文本长时间缓存，否则“今天”会在跨日后仍得到旧日期。

**与当前代码的差异**

- 当前仅有单一 `intent=general|profile|history|ranking`；新契约允许Profile、Preference、History和Ranking同时为true。
- 当前Planner仅读当前query；新契约可带最多4条Session消息解析指代，但仍不输入长期记忆，控制Token。

### 2026-09-12：Agent Memory Context最终拼装

**参考方案**

- LightMem参考点：未沉淀的近期对话直接保留，已压缩的长期记忆通过检索补充，不用长期记忆取代当前上下文。
- TiMEM参考点：高层记忆提供压缩概览，问题需要细节时再向下层记忆回溯。
- Memos参考点：时间和Tag先限定历史范围，避免只靠向量相似度把无关时期的记忆注入上下文。
- LangMem参考点：Profile可作为当前态直读，Collection内容按问题检索，两者不必全量同时注入。

**当前代码**

- 按“近期对话→Profile→Cross-Event→Event→Fact”固定顺序组装整个分区。
- 某个分区整体超过剩余Token时直接跳过整区，不会在区内逐条填充。
- 当前没有固定Preference分区，Profile无论问题是否需要都会被加入。

**DesayMem Light改造**

- 上下文使用“全局硬上限 + 分区软上限 + 区内逐条填充”，不再整区二选一。
- Session/Last-K始终第一优先，且按完整Turn保留；这是当前语义上下文，不能被历史检索挤掉。
- QueryPlan声明的Preference精确直读后紧随Session，仅输入相关attribute的active Item、conditions、scope和证据强度。
- Profile Snapshot只在 `needs_profile=true` 或general个性化对话时注入，不再无条件全量注入。
- Cross-Event提供跨事件概览；Event提供时间与行为细节；Fact只在需要原子细节或Event未覆盖时补充。
- 使用 `summarizes/contains` 关系做ID级去重：已注入Cross-Event时，默认不重复注入其概括Event；已注入Event时，默认不重复注入其contains Fact。
- 历史问答例外：当 `needs_history=true` 时，Event优先于Cross-Event，即使Cross-Event已命中，也可保留少量相关原Event作为可核对的时间证据。

**按路由调整顺序**

- action/preference：Session→required Preference→必要Event；默认不注入完整Profile和无关Fact。
- profile/general：Session→Profile→required Preference→Cross-Event→Event→Fact。
- history：Session→Event→Fact→Cross-Event→可选Profile/Preference。
- ranking：Session→统计结果→相关Preference→少量证据Event，不将所有原始记录交给Agent自己计数。

**第一版Token初值**

- `memory_context_max_tokens=4000`：只限记忆上下文，不包含Agent系统Prompt和当前问题。
- `session_context_max_tokens=800`、`preference_context_max_tokens=400`、`profile_context_max_tokens=500`。
- `cross_event_context_max_tokens=900`、`event_context_max_tokens=1200`、`fact_context_max_tokens=600`；分区上限之和可大于全局上限，最后仍受全局限制。
- `cross_event_top_k=3`、`event_top_k=6`、`fact_top_k=6`；均为上云调试起始值。

**可追溯输出**

- 每条注入内容带简短类型、ID、时间和可选Tag，例如 `[EVENT:e12 2026-09-08 tags=restaurant]`，便于Agent回答时引用和日志回放。
- SearchResult同时返回结构化命中列表和最终 `agent_context`，不只返回一段不可拆解的文本。

**为什么这样改**

- 车载当前指令依赖最近Turn，必须优先于任何长期画像。
- 精确Preference是业务参数，不应在自然语言Profile中间接猜测。
- 分层关系去重减少Cross-Event/Event/Fact重复表述占用Token；历史问答则保留原Event以避免摘要丢失时间细节。

### 2026-09-12：用户纠错、偏好撤销与真实删除

**参考方案**

- Mem0提供memory update、delete、delete_all和history，最新客户端还允许delete时清理linked/superseded memory，避免删掉当前值后旧值重新浮现。
- AWS AgentCore默认Preference consolidation主要是Add、Update和Skip，没有单独的用户隐私删除状态机；因此不能直接照搬处理我们的全链路删除。
- Zep/Graphiti的时序有效性思路是将旧状态结束有敌期而不改写过去，适合“偏好已变化”。
- 画像调研要求画像可被用户纠正，删除时需要处理Snapshot、缓存、来源边和派生索引。

**必须区分的三种语义**

- `CORRECT`：用户说系统记错了，新证据取代旧结论，但审计历史保留。
- `REVOKE`：用户表示偏好已经结束，旧Preference设置 `status=revoked/valid_to`，不再用于当前个性化，但历史Event仍客观存在。
- `ERASE`：用户明确要求删除某类记忆或全部数据，需要清理原始数据与派生结果，不能只把Preference标记失效。

**案例：“我不喜欢周杰伦了”**

- 新Message/Fact/Event照常保存，作为明确撤销证据。
- `music.artist=周杰伦` 从active变为revoked，`valid_to` 取该Event发生时间，Evidence Edge记录 `evidence_role=revoke`、`evidence_kind=explicit`。
- 如用户只说“最近想听别的”，证据不足以撤销长期偏好，可保留为临时Event。
- 新Profile Snapshot最小删除或修正“喜欢周杰伦”，其他画像不改；旧Snapshot保留作为审计版本，不再对Agent可见。
- 陶喆等其他 `music.artist` 多值Item不受影响；“不喜欢周杰伦”不等于删除整个music.artist属性。

**真实ERASE流程**

- 删除请求必须由明确API/交互确认进入Deletion Orchestrator，不由普通Profile LLM仅凭推测执行。
- 先在完整tenant/user/vehicle/occupant scope内解析目标，找到Message、Topic、Fact、Event、Cross-Event、Preference Evidence和Snapshot派生关系。
- 将目标立即从在线检索和个性化中隔离，再异步清理数据库、pgvector可检索记录、Session/Embedding/Query缓存和JSON Mirror。
- 含有其他有效信息的Event/Cross-Event不能粗暴整条删掉；应标记stale并从剩余未删除证据重建，重建前不参与召回。
- 最后从仍然有效的Preference与证据重建Profile Snapshot；审计仅保留不含被删内容的request ID、范围、时间、状态和算法版本。

**JSON Mirror一致性**

- PostgreSQL仍是权威库；所有INSERT/UPDATE/DELETE与Outbox事件在同一事务提交，Mirror Worker按稳定主键幂等投影。
- REVOKE/CORRECT是数据库UPDATE，JSON修改对应记录；ERASE是DELETE，JSON按主键删除。
- 每次删除任务结束后运行reconcile，校验数据库与JSON的主键集合及行内容；失败时不将删除任务标记完成。

**当前代码差距与改造原因**

- 当前ProfileAction只有ADD/CONFIRM/COEXIST/SUPERSEDE/NOOP，需增加REVOKE；Evidence role需增加revoke。

### 2026-09-12：评测定位与剩余原理讨论

**评测定位**

- 通用长期对话能力后续使用LoCoMo与LongMemEval，覆盖事实提取、跨会话、时间推理、知识更新和拒答。
- 车载能力后续以VehicleMemBench及CARMEN类数据为参考，重点覆盖多用户偏好冲突、动态偏好和车辆工具执行；VehicleMemBench当前仓库已有长历史、QA、Gold/KV/Summary及若干记忆系统接口，但其数据、适配器和本项目评分链仍需单独验证。
- 另行模拟一年实车对话，用于测试存储增长、画像漂移、偏好演化、跨季节条件、低频用户、并发及成本。
- 评测是后续验证层，不作为当前架构唯一来源；先完成模块原理、技术来源、改造理由与已知代价。

**当前技术来源链**

- LightMem：Session Buffer、近期上下文和轻量沉淀；我们增加完整Turn、10分钟空闲、车载超参与云端缓存。
- Mem0：七步写入、九步检索及新旧Fact操作；我们在Topic后抽取Fact，并保留证据、时间、Tag和Event绑定。
- StructMem：Topic内事实/关系条目及Cbuf+历史Sk生成Cross-Event；我们显式建立Event，使用Event中心向量召回Sk，并增加Checkpoint和完整证据关系。
- Memos：Tag与时间组织；我们将Tag、时间范围加入Fact/Event/Cross-Event和QueryPlan过滤。
- TiMEM：时间层级蒸馏与L5 Persona；我们改为开放Profile Snapshot与固定Preference双轨。
- LangMem/AWS Preference：Profile/Collection、结构化Schema及偏好Extraction/Consolidation；我们增加车载Attribute Registry、条件、多值、user/vehicle scope和显式/隐式门槛。
- Semantic Router/RouteLLM：Embedding语义路由与强模型升级；我们复用BGE-M3查询向量，高置信轻路由，复杂请求升级Qwen。
- VehicleMemBench：多用户偏好、冲突和车辆执行评测；只作为后续车载基线，不直接照搬其KV或Summary记忆实现。

**编码前仍需完成的原理讨论**

1. 多用户、多乘员、多车辆的身份和作用域合并优先级。
2. 多Worker的幂等、锁、Checkpoint与失败重试。
3. Qwen/BGE/PostgreSQL的资源预算、超时与降级。
4. API输入输出及上游Agent责任边界。
5. 最终技术方案总图和模块来源对照表。
- 当前多个外键使用ON DELETE RESTRICT，真实ERASE不能依赖单条SQL级联，必须由Deletion Orchestrator按依赖顺序处理和重建。
- 将偏好变化与隐私删除分开，既保留“以前喜欢、现在不喜欢”的时序语义，又能在用户真正要求删除时清除派生数据。

### 2026-09-14：论文式技术流程报告

- 新增 docs/DESAYMEM_TECHNICAL_FLOW_REPORT.html，包含总体算法图、Memory Add 子图、Memory Search 子图，供技术报告和浏览器展示。
- 图中用蓝色标注参考方法、绿色标注我们的改造、橙色标注已讨论但尚待实现，避免把设计稿误认为当前已交付代码。
- Add 图按 LightMem → Mem0 → 显式 Event → StructMem Cross-Event → TiMEM/LangMem/AWS 双轨画像展开。
- Search 图按 BGE-M3 轻路由/Qwen Planner → 统一多路 QueryPlan → Session/Preference/Profile/Fact/Event/Cross 检索 → 去重与 Token 装箱展开。
- 本地 D:/agent memory/code/memos 的 README 指向 usememos/memos，不能将其与 MemOS 项目混同；报告来源已按本地仓库标注。

### 2026-09-12：多用户、多乘员与多车辆作用域

**参考方案**

- VehicleMemBench将多用户偏好恢复、用户间冲突、偏好变化和最终车辆工具状态作为核心评测对象，说明车载记忆不能默认一车一用户。
- LangMem使用namespace隔离不同用户/应用的长期记忆；AWS Preference也按actor/session等命名空间形成记录。我们借鉴强作用域隔离，而不把座位角色当永久主体。
- 用户画像调研提出tenant→user/account→person/occupant的主体链，vehicle、seat、role、session属于环境上下文；未知访客默认不写已知用户长期画像。

**ID职责**

- tenant_id：品牌/业务租户，是绝对安全边界，任何查询都不能跨tenant。
- user_id：登录账号或数据控制主体，不必等于当前说话的人。
- occupant_id：被可靠识别的实际自然人/乘员，是Fact、Preference和Profile的主要主体。
- vehicle_id：本次车辆及车辆专属配置作用域，不代表用户身份。
- session_id：一次会话/行程的近期上下文边界，不进入跨会话长期身份。
- seat/role：driver、front_passenger等是本次condition，不能替代occupant_id。

**写入规则**

- Session Message始终保存完整tenant+user+vehicle+occupant+session。
- Fact/Event归属于明确speaker occupant，并保留发生时vehicle/session；不能因为当前角色是driver就默认归到车主。
- 第一版Cross-Event继续按同一tenant+user+vehicle+occupant生成，避免不同车辆和乘员事件被错误合并；跨车规律在Profile阶段汇总。
- Preference分为user scope和vehicle scope：音乐/餐饮通常为user scope且vehicle_id为空；座椅、车辆设备通常为vehicle scope。
- 开放Profile以tenant+user+occupant形成全局Snapshot；当前车辆Preference在查询时动态合并。
- 多人共同指令保存speaker_occupant_id和affected_occupant_ids；只有本人明确表达或存在可靠授权时才更新该乘员Preference。

**未知访客**

- 未识别访客使用session级临时occupant标识，只保存Session Buffer和必要操作Event，不进入Cross-Event、Preference或开放Profile。
- 第一版不自动把历史访客记忆迁移到后来绑定的身份，避免错误归属；迁移能力留作后续扩展。

**查询合并优先级**

- 先严格匹配tenant、user和occupant；任何相似度不得突破身份过滤。
- 执行查询读取user-global active Preference与current-vehicle active Preference；相同attribute和conditions下vehicle scope优先。
- multi属性不同值并集；single属性按scope、condition、显式证据和更新时间处理，不能由向量相似度决定冲突。
- 历史问题默认可查询同一occupant跨车辆Event；明确询问“这辆车”或vehicle_only时才限制当前vehicle。
- Session/Last-K只读取当前vehicle+occupant+session。

**当前代码差距与改造原因**

- MemoryScope允许vehicle_id为空，但Profile Pipeline、Cross-Event和多个Repository仍强制vehicle_id；Profile表也将其定义为NOT NULL。
- 当前固定匹配user_id和occupant_id是安全起点，但缺少访客状态、speaker/affected主体和user-global Profile读取。
- Cross-Event保持车内局部、Profile负责跨车汇总，可降低第一版组合与并发复杂度，同时满足换车后读取通用偏好。
- 作用域优先级由程序与Attribute Registry决定，不交给LLM或Embedding，避免语义相似造成跨用户串用。

**第一版暂不实现**

- household/group画像、跨账号家庭共享、访客历史自动认领和复杂授权图暂不进入第一版；保留扩展接口。

### 2026-09-12：多Worker幂等、锁、Checkpoint与重试

**参考方案与工程模式**

- LightMem采用后台异步整理以避免阻塞在线对话；我们保留写入与沉淀解耦，但将进程内任务改为PostgreSQL持久化Job。
- StructMem使用consolidated/Cbuf进度避免同一窗口反复参与当前巩固；我们将其改造成数据库Checkpoint，按Event或Cross-Event连续前缀推进。
- Mem0使用memory ID、历史操作和更新/删除接口保持新旧记忆演化；我们进一步为每个派生任务增加idempotency key与derivation key。
- PostgreSQL的FOR UPDATE SKIP LOCKED适合多个Worker并发领取任务；Transactional Outbox用于让业务数据变更和JSON Mirror事件在同一事务提交。
- 上述队列和锁属于云端工程化增强，不是LightMem、StructMem或Mem0原算法的完整实现。

**当前代码已有**

- memory_jobs以(job_type,idempotency_key)唯一，支持ready/running/retry/dead、attempts、next_run_at、locked_by和locked_at。
- Worker使用FOR UPDATE SKIP LOCKED批量claim；失败后指数退避，默认最多5次。
- Cross-Event和Profile已有Checkpoint；Memory Item另有derivation_key避免部分重复写入。
- JSON Mirror使用数据库Outbox，业务行与镜像事件可同事务提交。

**当前风险**

- 不同Worker可以同时claim同一scope同一阶段的不同Job，从而并发读取相同Checkpoint并重复调用Qwen。
- lock_timeout默认300秒但没有heartbeat；长Qwen调用超过租约后，第二个Worker可能重新领取同一Job，而第一个Worker仍在运行。
- Job唯一键只能防止重复入队，不能保证执行过程“恰好一次”；Worker崩溃可能发生“数据库已写成功，但Job尚未complete”的重复执行。
- Checkpoint当前是普通upsert，没有expected checkpoint/version条件，两个并发结果可能后写覆盖先写。
- 当前Job按next_run_at,id排序，不等于同一scope内严格因果顺序。

**第一版一致性模型**

- 明确采用at-least-once任务投递，不声称分布式exactly-once；通过幂等写入、scope-stage串行和Checkpoint CAS达到业务上的effectively-once。
- 每个Job增加stage和lock_scope_key；claim时同一个lock_scope_key只允许一个running Job，不同用户/车辆/Topic仍可并行。
- 锁粒度：session按完整SessionScope；fact/event按topic_id；cross_event按tenant+user+vehicle+occupant；profile按tenant+user+occupant全局；erase按tenant+user+occupant独占。
- running Job定期续租locked_at；只有持有相同locked_by和lease token的Worker才能complete/fail，防止旧Worker覆盖已被重新领取的任务。
- 所有派生记录使用稳定derivation_key或唯一业务键；重复执行INSERT时返回已有记录，不再次生成重复Fact/Event/Cross-Event/Profile版本。
- 写Memory、Relation、Evidence、Audit、Usage、下游Job、Checkpoint和JSON Outbox必须在同一数据库事务中提交。

**Checkpoint规则**

- Pipeline开始读取expected checkpoint；提交时执行compare-and-set，只有数据库仍等于expected值才能推进到本次完整处理前缀的最后一项。
- CAS失败表示其他Worker已推进：当前结果不得覆盖；重新读取后若本批已处理则NOOP，否则重新构建剩余批次。
- Cross-Event Checkpoint记录最后处理Event；Profile Checkpoint应记录最后处理Cross-Event，而不是其某个Evidence Event。
- LLM失败、JSON契约失败、数据库提交失败或Mirror Outbox写入失败均不推进Checkpoint。

**重试分类**

- 网络超时、Qwen/BGE临时错误、PostgreSQL连接错误：指数退避加随机抖动后重试。
- LLM JSON契约错误：允许一次受限重试或修复调用，仍失败则dead并阻塞该scope-stage后续Checkpoint推进。
- 未知attribute、单个非法Preference operation：丢弃该操作并审计，不必让整个有效批次失败。
- 身份越权、scope不匹配、数据不存在等永久错误：直接dead，不重复消耗LLM。
- dead Job进入人工/运维重放队列；重放仍使用原idempotency key和input hash。

**为什么这样改**

- 同一scope-stage串行可防止画像版本、Checkpoint和新旧偏好互相覆盖；不同用户仍能并行，适合多用户云端吞吐。
- 不在Qwen调用期间持有长数据库事务或行锁，避免连接池耗尽；Job lease负责长调用期间的逻辑所有权。
- at-least-once加幂等/CAS比承诺无法真正实现的exactly-once更可靠，也便于故障注入测试。

### 2026-09-12：Qwen、BGE-M3与PostgreSQL资源隔离和降级

**参考方案与原则**

- LightMem将昂贵整理放到异步路径，参考其“在线交互与后台巩固分离”；我们进一步为两类流量配置不同超时和资源池。
- Semantic Router/RouteLLM提供低成本路径优先、复杂请求升级强模型的级联思路；我们使用BGE-M3轻路由，不确定时才调用Qwen Planner。
- Bulkhead、Timeout、Circuit Breaker和Backpressure是云服务常用韧性模式；它们属于我们的工程实现，不是上述记忆论文的算法组成。
- PostgreSQL连接池与Transactional Outbox继续承担持久化、任务和JSON Mirror一致性，但模型调用不得占用数据库事务连接。

**当前代码**

- Qwen使用统一timeout=60秒，BGE-M3使用统一timeout=30秒，PostgreSQL池默认min=2/max=10。
- BGE adapter支持批量输入；Session Topic已有进程内TTL/LRU Embedding缓存。
- 在线检索与后台Fact/Cross-Event/Profile目前共用provider和超时，缺少独立并发配额、熔断和总体时限。

**在线查询策略**

- Memory Search设置总体deadline；Session直读、Preference/Profile精确读取、BGE查询和PostgreSQL检索分别使用较短子超时。
- BGE成功后复用同一query vector完成轻路由与pgvector检索；轻路由不确定且剩余deadline足够时才调用Qwen Planner。
- Qwen Planner超时或熔断时，使用BGE语义计划或基础混合检索；BGE失败时返回Session + 精确Preference/Profile + 可用的时间/Tag数据库结果，并标记degraded。
- PostgreSQL向量查询超时不能阻断已取得的Session和精确Preference；SearchResult返回partial/degraded及具体degradation_reasons。
- 在线请求不等待Fact、Cross-Event或Profile沉淀完成，刚写入但未沉淀的信息由Session/Last-K保证。

**后台沉淀策略**

- 原始Message必须优先持久化；BGE或Qwen不可用时只让后续Job进入retry，不丢失输入，也不生成伪降级Fact/Profile。
- Fact、Cross-Event、Profile允许较长超时和指数退避，但各自使用独立并发上限，避免Profile积压占满Fact资源。
- 任何外部模型调用前先完成短事务读取并释放连接；模型返回后重新开启短事务，执行版本/CAS校验后写入。
- Job backlog达到阈值时启用背压：继续接收并持久化Message，但降低低优先级Profile/Cross-Event调度速率，优先Fact/Event和在线查询。

**资源隔离**

- BGE服务使用动态micro-batching、有界队列和多副本；在线query embedding优先级高于后台批量add embedding。
- Qwen至少划分online_planner与background_memory两个逻辑队列/并发信号量，即使底层是同一个Qwen3-32B服务。
- PostgreSQL连接池预留在线查询容量，后台Worker限制并发UoW数；不得让Worker数量直接等于或超过全部连接池容量。
- 按tenant设置请求速率和待处理Job配额，避免单一用户或测试任务占满全局模型服务。

**第一版超参起点**

- online_memory_deadline_ms=2000，online_bge_timeout_ms=500，online_planner_timeout_ms=1200，online_db_timeout_ms=800。
- background_bge_timeout_seconds=10，background_qwen_timeout_seconds=60。
- provider_failure_threshold=5，provider_circuit_open_seconds=30。
- embedding_batch_max_size=32，embedding_batch_wait_ms=5；具体值按GPU实测调整。
- online_planner_max_concurrency、background_fact_max_concurrency、background_cross_event_max_concurrency和background_profile_max_concurrency必须分别配置，不在未压测时写死生产值。

**降级等级**

- L0正常：Qwen/BGE/PostgreSQL全部可用。
- L1 Planner降级：BGE轻路由或基础计划 + 正常记忆检索。
- L2 BGE降级：Session + 精确Preference/Profile + 时间/Tag/关键词数据库检索。
- L3长期库降级：仅当前Session/Last-K及已经直读到的安全数据。
- 写入侧只有“原始消息已持久化、沉淀待重试”，不允许用低质量规则或空结果替代正式Fact/Profile写入。

**为什么这样改**

- 在线车机不能等待60秒，但后台记忆可以重试；统一超时会同时伤害交互延迟和沉淀可靠性。
- Session与固定Preference属于低成本确定性数据，即使模型服务故障也应继续提供基本个性化。
- 资源舱壁和优先级防止一年数据回放或某个大租户的后台任务挤占实时车辆查询。
- 所有建议时限只是云端压测起点，最终以Qwen/BGE硬件、p95/p99、错误率和积压速度调优。

### 2026-09-12：API契约与上游车机Agent责任边界

**参考方案**

- Mem0提供add/search/update/delete/history等明确记忆API，参考其“Agent调用记忆服务、记忆服务不直接执行外部工具”的边界。
- VehicleMemBench的执行链要求Agent在需要偏好或历史时先调用search_memory，再调用车辆工具，并分别评分记忆调用和最终工具状态；这支持将记忆检索与车辆执行解耦。
- AWS AgentCore将Memory作为Agent能力之一而不是车辆业务本身；Preference策略负责形成记录，最终消费和动作仍由Agent/应用决定。
- 我们在此基础上增加车载可信身份、speaker/affected乘员、结构化车辆操作和执行安全边界。

**当前代码**

- 对外只有POST /v1/messages、POST /v1/memories/search和健康检查。
- Message支持user/assistant/system，已经可以完整传入用户与助手消息；结构化tool/action目前只能塞入metadata。
- SearchRequest由请求体直接携带MemoryScope，生产环境若无网关校验可能被伪造tenant/user/occupant。

**第一版写入API**

- POST /v1/messages：只接收对话消息；user和assistant都必须原样传入，使用稳定message_id/request_id和session内sequence_no，异步返回202与segmentation_job_id。
- POST /v1/observations：接收结构化车机操作或工具结果，不伪装成assistant文本；包含observation_id、action_type、parameters、result、speaker_occupant_id、affected_occupant_ids、vehicle/session和occurred_at。
- 对话和Observation最终进入同一Session/Topic沉淀链，但保留source_type，使Fact/Profile能够区分用户明确表达与系统观察行为。
- 重复message_id或observation_id必须幂等返回已有结果，不能重复触发沉淀。

**第一版读取API**

- POST /v1/memories/search：接收自然语言query、session_id、可选时间/Tag，以及可选的上游retrieval_plan；返回结构化Session/Preference/Profile/Fact/Event/Cross-Event命中、agent_context、plan_source和降级信息。
- GET /v1/preferences/current?attributes=...：供音乐、空调、导航等业务按attribute精确读取当前有效Preference，返回conditions、scope、version和evidence strength。
- GET /v1/profile/current：读取当前开放Profile Snapshot；主要供展示、通用对话和用户画像管理页使用。
- 上游已传完整required_profile_attributes/time/tags时可跳过Planner；否则走BGE/Qwen可插拔路由。

**纠错、删除与运维API**

- POST /v1/memory-feedback：提交“记错了/偏好已变化”及目标Item或自然语言说明，形成可追溯用户Event，再走CORRECT/REVOKE流程。
- POST /v1/deletion-requests：提交明确ERASE范围并返回异步deletion_request_id；GET对应状态用于查看隔离、派生重建、Mirror清理和reconcile进度。
- POST /v1/internal/consolidation：仅内部/测试权限使用，按scope和stage强制flush或沉淀；普通车机Agent不能调用。
- 管理接口必须与业务查询接口分权，真实删除和强制沉淀不能由普通自然语言Planner直接执行。

**可信身份边界**

- tenant_id和user_id来自认证令牌/API Gateway，不接受客户端任意覆盖；vehicle_id来自受信车辆凭证。
- occupant_id、seat和speaker信息来自上游身份/座位识别服务，并带识别状态或可信等级；记忆系统不负责声纹、人脸和座位识别。
- 请求体scope只能作为声明值，与认证上下文不一致时拒绝；Qwen和BGE永远不能修改scope。
- 未识别访客由网关传入ephemeral occupant和guest状态，记忆系统据此禁止长期画像沉淀。

**上游Agent负责**

- 提供完整user/assistant消息、当前车辆/session、可靠speaker/affected乘员和结构化工具结果。
- 在已知业务域时声明required_profile_attributes，减少Planner调用。
- 根据Preference的scope、conditions和evidence strength执行安全确认，并负责最终车辆工具调用；弱隐式Preference不得直接驱动车控。
- 向用户展示记忆使用、纠错和删除入口。

**记忆系统负责**

- 输入校验、幂等持久化、Session/Topic/Fact/Event/Cross-Event/Profile沉淀、证据和版本。
- QueryPlan、混合检索、固定Preference直读、Context装箱、降级和资源统计。
- 不执行车辆工具，不自行识别乘员，不以画像替代上游权限与安全策略。

**响应与可观测字段**

- 所有请求传播request_id/trace_id；异步操作返回job/request ID。
- SearchResult增加query_plan、plan_source、preferences、degraded、partial、degradation_reasons和usage。
- Preference消费返回item_id/version/evidence_kind，便于VehicleMemBench式工具执行回放和线上Badcase审计。

**为什么这样改**

- 将结构化Observation从对话文本中分离，可避免把assistant工具描述误当成用户明确偏好。
- 将身份识别和车辆执行留在上游，记忆系统只管理证据和上下文，降低越权和错误车控风险。
- Preference精确API与开放search并存，既支持固定车载业务，又保留自然语言泛化查询。
### 2026-09-14：车载行程、条件偏好与控车 Skill 沉淀（待确认）

**参考与边界**

- [AWS AgentCore Episodic Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/episodic-memory-strategy.html) 将交互过程归为 episode，再从多个 episode 反思可复用经验；[LangGraph 的 procedural memory](https://docs.langchain.com/oss/python/concepts/memory) 说明程序性记忆偏向“如何执行”而非用户事实；[AWS User Preference](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/user-preference-memory-strategy.html) 区分偏好提取与新旧合并。上述都不是现成的车控授权系统。
- 我们把 **Trip/Event（发生过什么）**、**Profile/Preference（什么条件下喜欢什么）**、**Skill Candidate（常用操作步骤）** 三种对象分开；执行权限、自动化开关和确认策略属于上游车机/手机与安全策略，不由模型从习惯推断。

**建议的最小写入链路**

1. 上游提供结构化车机操作与工具回执（目标设备、参数、执行状态、车辆/操作者、时间），行程系统提供 trip_id、起终点或其隐私化表示、出发/到达时间；天气、季节、工作日和乘员仅在上游有可信数据时附加。对话仍按现有 Topic→Fact→Event 流转。
2. `action` Fact 记录请求，`state` Fact 只记录真实回执；同一 trip_id 的行程事实可形成 Trip Event。一次路线或控车操作不自动变成长期偏好。
3. 后台 Profile 批次从 Event/Cross-Event 及结构化条件中更新 `Preference Item`：如 `cabin.temperature=22℃` 且 `conditions={"season":"winter","day_type":"weekday","vehicle_id":"V1"}`。不同条件可共存；同条件冲突才比较新旧。开放 Profile Snapshot 只写稳定且可追溯的概括。
4. 另设可插拔 `Skill Distiller`，第一版只形成**候选模板**：`intent + ordered_steps(tool_name, parameter_source) + conditions + scope + supporting_event_ids + status`。仅同类成功操作多次出现或用户明确提出“记住这个流程”时才考虑生成候选；失败/撤销/人工修正作为反证。初版可从结构化工具日志确定性聚合，不额外调用 Qwen；自然语言步骤归纳留作可选插件。
5. 检索时先用当前 user/vehicle、时间、天气等过滤候选，再交给车机 Agent 结合实时状态、安全策略与用户授权决定“建议、询问确认或执行”。**历史上常执行 ≠ 已授权自动执行**；通知/免打扰和是否需确认应作为用户显式设置或独立权限配置，而不是隐式行为推断。

**例子**：工作日冬季 07:30，用户在 V1 上多次先打开空调 22℃、再打开座椅加热；候选 Skill 可表示“上车舒适准备”，条件是工作日/冬季/V1，步骤是空调 22℃→座椅加热。若用户未开启该自动化，它只能用于推荐或补全；不能静默远程执行。夏季 24℃可另存条件偏好，不覆盖冬季 22℃。

**后续需确认**：第一版是否纳入结构化行程和工具回执；Skill 的启用阈值、有效期与删除/撤销；出发规律是否仅做统计候选；用户/车辆/乘员作用域；哪些操作只能建议、哪些允许用户授权自动化。所有阈值应是超参数，不把它们当作通用事实。

### 2026-09-14：对照 Mem0 / memU / MIRIX / FluxMem 后的 Skill 方案细化（待确认）

**本地代码实际做法**

- Mem0：本地 `D:/agent memory/mem0` 使用的是 `memory_type="procedural_memory"`（未找到 `procedure_type` 字段）；`_create_procedural_memory` 对 Agent 的消息/执行过程单独调用 LLM，生成较完整的顺序文本，再嵌入向量存储。它偏向 Agent 工作进度/轨迹复述，不是车控步骤授权契约。
- memU：`skill` 与 `memory` 分轨；后台读取包含工具调用的完整 session，选择不做、修改已有 Skill 或新建 Markdown Skill；搜索时主要向量化 Skill 的名称和描述，按需读正文。可借鉴“与用户记忆分轨、相似 Skill 优先更新、不必每次生成”的组织方式，不直接用 Markdown 驱动车控。
- MIRIX：独立的 procedural memory，存 `entry_type + summary + steps[]`、用户/Agent、更新时间、过滤 Tag 和向量；有插入、更新和去重入口。可借鉴结构化步骤、身份范围和单独索引，但还需我们添加上下文条件、执行回执、授权隔离和证据。
- FluxMem（LightMem 仓库内的另一套 `src/fluxmem`，不可等同 LightMem Topic 模块）：离线把 EpisodicNode 聚类，LLM 从含成败结果的轨迹归纳 ProceduralNode，以 `DistillEdge` 连回源 Episode，支持回放/PEMS 迭代。借鉴“Episode→Skill 的来源边和成功验证”；第一版不引入 KMeans、迭代回放与多轮 LLM。
- VehicleMem-Eval 本地 README 将 VehicleMemBench 描述为多人车控工具调用、偏好冲突、条件约束、指代、纠错和状态变化五类；本地 QA JSON 是 Git LFS 指针，不能从未下载的标准答案推断具体工具清单。history 文本有座椅通风/按摩、车窗与内循环的条件要求，可用于构造初版测试样例，但不能当作完整金标。

**我们的第一版取舍**

1. 历史行程独立为按 `trip_id` 归属的 Episode/Trip Event（时间、车辆、驾驶员、起终点的必要且隐私受控字段），保留原操作、回执及异常；不要把每天出发的明细塞进 Profile 或 Skill。
2. Preference 复用拟扩展的 `profile_items`：一行一个 `attribute + value_json + conditions + scope + valid_from/to + evidence`。例：`cabin.temperature=22℃`、工作日/冬季/V1；通勤时间段、常用车辆、充电/泊车习惯和免打扰属于不同属性。显式偏好与统计观察分开；“常出发于 07:30”不能表述成“希望 07:30 自动出发”。
3. 增加独立、可插拔的 `skill_items` 候选存储（与 profile_items 分开）：`intent/summary/steps_json/conditions/scope/status/version/support_count/last_success_at/evidence_event_ids`，仅摘要做可选向量索引；步骤使用受允许工具名及参数来源的结构化契约，不保存无限长原始轨迹。示例步骤：`set_climate(temperature=preference:cabin.temperature)` → `set_seat_heating(level=explicit_preference)`；参数来源可来自当前用户指令、有效 Preference 或固定模板，缺失则不能自动补全。
4. 写入由结构化成功回执构成操作 Episode；同一 `intent + scope + 相容conditions` 的 Episode 累积后产生/更新 Skill 候选，阈值可配置。第一版先做受控工具序列的确定性归并与条件匹配，不每次 Qwen 提 Skill；跨措辞、复杂分支可在后台触发一次 Qwen/批次作为可插拔升级，并要求输出受限 JSON、保留证据。
5. 查询 Skill 用 `intent + scope + 当前条件` 先筛，再按需看摘要向量；只有在显式授权与上游安全策略均通过时车机 Agent 才能执行。Skill status=`candidate` 只用于建议/确认，不能将 `support_count` 视为授权。执行失败、用户撤销或纠错触发反证和版本更新；JSON Mirror 对应新增表同步镜像。

**与前条讨论的修正**：此前示例把“多次成功”写成候选 Skill 的一般触发；现明确**一次用户直接说“记住这个流程”也仅能建立候选或显式配置建议，不自动获得车控执行权限**。另外行程 Episode/工具序列可以与对话 Topic Event 并行，不强迫按 Topic 切断一段跨时间的远程控车过程。

### 2026-09-14：车控记忆与 Skill 是否独立存库（待确认）

- 建议第一版**逻辑分库、物理共用 PostgreSQL**：车控操作 Episode/行程、条件 Preference、Skill 各有独立表/Repository/检索接口；不混进通用 Fact/Event 的一个向量 Top-K。现有 PostgreSQL、事务、权限、JSON Mirror 和备份机制可以复用，避免再运维一套数据库。
- 车控历史按 `user_id + vehicle_id + time/trip_id/tool_name` 精确检索；条件 Preference 按 `attribute + scope + conditions + valid time` 查当前值；Skill 按 `intent + scope + conditions + status` 查候选，只有自然语言模糊匹配时才用摘要向量。三个索引目的不同，不能只依赖 pgvector。
- 推荐边界：通用对话 Fact/Event 仍可引用车控 Episode ID，供自然语言问答；车控执行链路直接读取专用结构化表和明确权限，不从大段 Profile 文本或自由生成 Skill 中执行。
- 第二阶段只有在数据量、租户隔离、访问权限或吞吐量证明需要时，才把车控 schema 迁往独立 PostgreSQL 实例。此时 Repository 接口不变，但跨库事务与 JSON Mirror 一致性需另行设计。

### 2026-09-14：双输入通道确认——对话与结构化车控 JSON

- 输入同时允许对话消息和车机/手机的结构化操作 JSON；第一版建议保留现有 `/v1/messages` 对话接口，新增独立 `/v1/vehicle-operations` 契约。两者共享 `tenant_id/user_id/vehicle_id/occupant_id`、时间、`request_id` 等身份与幂等原则，但不把工具回执硬塞进 `assistant` 自然语言或宽松 `metadata`。
- 对话沿现有 Session→Topic→Fact→Event 流转；结构化车控 JSON 先保存原始请求及结果，通过 `operation_id` 对齐异步回执，再生成车控 Operation Event。`trip_id` 可空；远程控车不必属于一次行程。跨多次车控操作的 Episode/Trip 可由 `trip_id` 或关联 ID 聚合，不受对话 Topic 边界限制。
- 结构化请求至少包含身份、`operation_id`、来源（车机/手机）、工具名、请求参数、发生时间；回执包含 `operation_id`、执行状态、实际结果/失败原因与结果时间。天气/季节/工作日/乘员/地点只能来自可信上游或确定性时间计算，不能由模型凭空补齐。
- 同一操作的“用户请求”与“成功执行”是两个证据状态；只有成功回执才计入成功 Skill Episode。对话里提及了相同操作时，可通过明确关联 ID 建立证据关系，避免重复计数。失败、取消和纠错保留用于反证。
- 当前代码 `/v1/messages` 接收自然语言 `content` 与通用 `metadata`，尚无上述结构化 JSON API、车控专表或回执状态机；这是设计结论，不代表已实现。

### 2026-09-14：车控请求/回执与失败兜底（待确认具体时限）

- 上游可提供每次操作的 `operation_id`、用户请求时间、车机完成时间、成功/失败结果；两条上报以 `operation_id` 幂等合并。同一操作只记一次成功，不因重复回执重复计数。
- 最小状态流转：`requested → success | failed | unknown`。请求后在可配置等待时限内无最终回执，标记 `unknown`（非成功、也非确定失败）；迟到回执仍可按版本/时间更正为最终结果并留下审计。若上游支持 `cancelled`，可加独立终态。
- 明确失败：保留 `error_code/error_message`（必要时脱敏）、请求参数和完成时间，用于用户查询、失败模式分析和纠错；不计入成功 Preference/Skill 证据。失败后若用户手动重新发起，应生成**新的** `operation_id`，并用 `retry_of` 关联，而不是覆写旧失败。
- 兜底分两层：记忆侧对迟到/重复/缺失回执做幂等、超时标记、对账与审计；执行侧的重试、补偿、用户提示由上游车控系统/车机 Agent 在权限和实时状态检查后决定。记忆系统**不自动重试物理车控**，避免车窗、空调等操作被重复或越权执行。
- 对查询和 Skill 蒸馏，`failed/unknown` 只能作失败或不确定证据；不得把“发出请求”说成“车辆已完成”，也不得由多次失败学习出成功 Skill。上游后续可提供可查询的操作状态接口供对账，但第一版不依赖新增外部服务。

### 2026-09-14：连续车控操作如何组成 Episode（待确认）

- **最可靠的聚合键**：上游如能提供一次交互/场景的 `interaction_id`（或 `command_group_id`），同一 `tenant_id + user_id + vehicle_id + interaction_id` 的操作请求与回执组成一个车控 Episode；每个操作仍保留独立 `operation_id`、请求/完成时间、参数和结果。`trip_id` 只表示行程关联，不等于 Episode 分组键，一次行程可含多组互不相关的操作。
- **没有分组键的第一版兜底**：一条操作形成一个单操作 Episode；不因“相隔几分钟”就把空调、车窗、充电等动作拼成同一 Skill。后续可增加离线关联插件（结合意图和步骤证据），但不得跨用户、车辆或身份不明的操作合并。
- Episode 只在组内操作收到终态回执，或可配置空闲/等待时限到达时封口；晚到回执更正对应 Operation/Episode 版本并重算可用证据。`failed/unknown` 步骤必须保留，不能生成“全部成功”的 Skill。成功的顺序以操作请求时间和同组顺序确定；完成时间用于确认真实结果，不能仅以回执到达顺序重排用户操作。
- 例：同一 `interaction_id=I7` 内，07:30 请求空调 22℃并成功、07:31 请求座椅加热 2 档并成功 → 一个“上车舒适设置”Episode，支持候选 Skill；07:40 单独开窗，若无同组 ID则是另一 Episode。若座椅加热失败，这个 Episode 仍完整保存，但不能当成“空调+座椅加热均成功”的证据。
- 这借鉴 FluxMem 的“保留完整成败轨迹→Episode→Skill 来源边”和 MIRIX 的分步 Procedure，但我们先用上游分组键与确定性顺序建立 Episode，不对每条操作调用 LLM，也不把任意相邻动作自动归成一个用户意图。相似 Episode 如何进一步蒸馏 Skill 留待下一轮讨论。

### 2026-09-14：上游没有 Topic/interaction_id 时的修正

- 上游**不需要**提供对话 `topic_id`；车控 JSON 与对话 Topic 是两条独立输入链。`interaction_id` 也只作为可选的强分组证据，不能当作车控接口必填字段。
- 每个 `operation_id` 先独立保存为 Operation Event（请求、回执、时间、身份、参数、结果）。单操作本身就可用于历史查询与单步条件偏好候选；不应因缺少组 ID 而丢弃或延迟入库。
- 没有交互组 ID 时，后台可按同一 user/vehicle 的时间顺序生成**候选活动窗口**，窗口间隔与最大跨度是超参数。窗口只是缩小候选范围，不证明动作具有相同意图，也不立即写成已确认的多步 Episode/Skill。
- 在不同日/行程中反复出现且顺序、工具类型和条件相容的操作序列，才可被提为多步 Skill 候选；需保留每次来源 Operation Event、成功/失败记录。没有重复证据时维持独立单操作 Event。第一版可用确定性序列归并；仅在候选形成后，可选一次后台 LLM/批次生成自然语言名称或分支说明，不能让模型创造不存在的步骤。
- 用户一次请求“上车帮我调好空调和座椅”若上游拆成两个操作但无组 ID，可通过原始请求 ID 或明确消息关联加强证据；若这些关联也没有，就只把它们视作邻近候选，不能声称它们一定属于同一用户意图。所有操作仍隔离 tenant/user/vehicle/occupant scope。
- **修正上一节**：“无分组键时一条操作形成一个 Episode”仅表示写入和查询的最小证据单元，不阻止后台以后从重复序列中归纳更高层的操作 Episode；也不要求上游提供 Topic ID。

### 2026-09-14：Skill 候选如何从操作序列晋升（待确认）

**参考方法**

- FluxMem `src/fluxmem/stages/stage3_consolidation.py`：离线聚类 Episode，LLM 从成败轨迹归纳 Skill，并以 `DistillEdge` 连接来源，随后回放/PEMS 迭代验证。适合启发“多次经历→可复用流程→可追溯验证”，但当前实现需要聚类、多轮 LLM/回放。
- memU `src/memu/hosts/bridging/instructions.py`：每个 session 可以不生成 Skill、修改旧 Skill 或新增 Skill；值得借鉴“不强行生成、优先更新相近旧流程”。其 Markdown 技能适合 Agent 指令，不适合直接作为车控可执行参数。
- MIRIX `mirix/schemas/procedural_memory.py` 和 `mirix/functions/function_sets/memory_tools.py`：独立存 `summary + steps[]`，插入前检查相同流程；值得借鉴步骤结构和去重，但其文本步骤尚不构成车控授权契约。
- Mem0 本地 `memory_type="procedural_memory"` 直接 LLM 摘要并向量存储 Agent 轨迹；可借鉴独立记忆类型，但车控第一版不复制其逐次高成本、长文本方式。

**我们建议的最小晋升流程**

1. 只使用上游注册过的工具名、规范化参数和真实回执构造 Operation Episode；同一用户/车辆、时间窗口内的顺序仅产生候选序列，不能单凭邻近推断共同意图。跨不同日/行程重复的相同工具顺序可累积支持证据，条件从可信上下文取。
2. 同一工具序列但参数随条件改变时，不为每个数值复制一个 Skill：步骤参数可引用条件 Preference（如 `temperature=preference:cabin.temperature`），Skill 只存“先设空调、再开座椅加热”的流程；若差异是有意义的工具分支，第一版分成两个候选，不生成复杂分支脚本。
3. 测试初值可设“至少 3 个独立日/行程的成功序列”才从 `observed` 晋升 `candidate`，且近期失败/撤销/用户否定使候选暂停或退回；次数、时间窗、近期范围均是超参数，需 VehicleMemBench 类评测和模拟一年数据调参，不伪称论文阈值。`candidate` 只可用于建议或等待用户确认，不自动执行。
4. 若只有反复出现的单步数值（例如冬季工作日常设 22℃），优先沉淀为条件 Preference；只有用户明确保存为快捷操作或需要跨工具复用时，再建单步 Skill。Preference回答“参数是什么”，Skill回答“如何按步骤完成”。
5. 新候选先匹配同 `intent/tool_sequence/scope/conditions` 的旧 Skill：相同流程增证据/版本，条件不同可共存，矛盾或失败记录为反证；不因一次相似度高就覆盖旧 Skill。保留每个候选的来源 Event ID、成功/失败统计、版本和状态。
6. 初版规范化、计数和条件过滤由代码完成，**Skill 蒸馏可为 0 次额外 LLM**；后台仅在需要命名/归纳复杂多步候选时可选 1 次 Qwen/批次，输入受限 Episode 摘要而非全量轨迹，输出受限步骤 JSON 并回查证据。向量只索引短摘要用于模糊搜索，不是精确车控的唯一入口。

**例子**：1 月 12、13、14 日的冬季工作日，U1 在 V1 分别成功执行“空调 22℃→座椅加热 2 档”；同一日的重复点击不算三个独立支持。三次独立成功可按测试阈值形成“上车舒适准备”候选，证据指向六条实际 Operation Event；若 15 日座椅加热失败，则保留失败轨迹并暂停候选，而不伪称四次全部成功。若 7 月改为 24℃，优先更新/共存夏季温度 Preference，流程 Skill 不必复制。

**为什么这样改**：比 FluxMem 少聚类和反复 LLM，比 Mem0 的全轨迹摘要省 token；比纯文本 Skill 多工具参数约束、条件、证据和失败处理；比把所有重复操作都变成 Skill 更能控制云端存储与检索噪声。授权/自动执行仍由上游独立控制。

### 2026-09-14：纳入 Hermes Agent Skill 自进化（待核对同事实现）

**参考来源与事实边界**

- [Hermes Agent 官方 Skills 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)：Agent 可创建/局部修改/整体编辑 Skill 文档，后台复盘也可建议更新；`skills.write_approval` 能将写入暂存等待审核。其 Skill 是 Agent 工作方法文档，不是车控工具的授权凭证。
- [NousResearch/hermes-agent-self-evolution](https://github.com/NousResearch/hermes-agent-self-evolution)：公开 README 描述以 DSPy/GEPA 根据执行轨迹生成候选、评测、过测试/约束门槛并送人工 review；当前 README 标注 Skill 文件优化已实现，连续改进自动流水线仍为 planned。它需要多轮模型/API 调用，不能视为零成本在线流程。
- 本地 memU 的 Hermes 适配器 `src/memu/hosts/hermes/BRIDGING_TASK.md` 是“读取 Hermes 会话→self-evolve→commit”的桥接机制；不等同于 Hermes 原生 Skill 管理或单独的 GEPA 自进化项目。同事具体用了哪一种、怎样评测与部署，目前没有其代码/配置，不应擅自断言。

**我们的改造建议**

1. 保留现有 `skill_items` 作为**运行时结构化车控候选**：受限工具名、步骤参数来源、conditions、user/vehicle scope、证据、版本和状态；车机/手机只读已发布的受限 JSON，不能执行 Hermes 的自由文本 Skill。
2. 可插拔 `HermesSkillEvolver` 只在**离线**接收脱敏的成功/失败 Episode、用户纠错和旧 Skill 版本，输出候选新版本/改进建议；不直接改线上 `skill_items`。普通重复序列仍由确定性统计处理，不把每条车控操作送去自进化。
3. 候选变更必须通过工具白名单和参数 Schema、身份/条件/权限边界、不编造步骤、来源证据、失败回放与 VehicleMemBench/模拟行程回归评测；比较旧版与新版的工具调用正确率、错误执行率、确认率、token/延迟。通过后人工/受控发布并记录版本，失败则保留旧版。自动执行授权永远不由自进化模块生成或提升。
4. 可先只让 Hermes 改进 Agent 的**检索/询问确认提示与推荐话术**；结构化车控步骤优化作为后续灰度功能。这样第一版稳定且不增加在线 Qwen 调用，离线模型成本可按周期和样本量控制。

**后续需核对**：请取得同事的 Hermes 代码/Skill 示例/评测脚本，确认其使用的是原生 Skill、memU 桥接还是 self-evolution/GEPA；再决定复用其产物格式、触发条件与审核门槛。

### 2026-09-14：Skill 状态、Hermes 改动边界与版本发布（待确认）

**参考与改造原因**

- [Hermes 官方 Skills 写入审核](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills) 允许 Agent 的 Skill 变更先暂存再批准；[Hermes Self-Evolution](https://github.com/NousResearch/hermes-agent-self-evolution) 以执行轨迹提出候选、经过测试和约束门槛后人工审阅，不直接覆盖旧版。我们借鉴“候选→验证→批准”闭环。
- FluxMem 的 Skill 版本/来源 Episode/回放评分可作溯源与评估参考；但车控涉及物理动作，需额外锁住工具许可、参数、安全条件和执行授权。上述参考项目均不提供我们车控业务的权限判定。

**最小状态机**

- `observed` 只是操作统计/序列候选，尚未是可读取的 Skill Item；达到测试阈值后创建 `candidate`。
- `candidate`：可内部分析和向用户建议，但不能被执行链当作已发布模板；Hermes 的新版也先落在这里或独立 `proposal` 记录。
- `published`：经过验证、审核后可被检索给车机 Agent 用于推荐或填参；**仍不等于自动执行授权**。
- `suspended`：近期失败、用户纠错或证据撤销时暂停推荐；保留历史和版本以便追查。旧版废弃可标 `retired`，不硬删证据。权限配置不随 Skill 状态升级。

**Hermes 第一版可改 / 不可改**

- 可建议修改：Skill 名称、简短说明、适用场景的文字解释、推荐/询问确认话术；这些只影响 Agent 如何描述，不直接改变工具调用。
- 不可直接修改：`tool_name`、步骤顺序、参数来源、用户/车辆作用域、可信 conditions、证据 ID、是否自动执行和确认要求。若后续允许步骤改动，必须作为**新的结构化版本提案**重新验证，不能就地 patch 线上 `published` JSON。

**发布/回滚**

1. 对旧版 `v1` 生成 `v2 candidate`，保留两者与来源 Episode；线上继续用 v1。
2. 先通过 JSON Schema、工具白名单、参数取值/来源、身份/条件边界和证据覆盖校验；再用历史成功、失败、条件冲突、多人和状态变化案例做离线回放。至少比较工具调用正确率、错误执行/越权、需要确认时是否确认、token/延迟。评测集版本和通过阈值需配置，不宣称固定数值来自 Hermes。
3. 验证通过后经人工或受控发布把同一 Skill 家族的当前版本原子指向 v2；v1 保留为可回滚历史版本。失败则拒绝 v2，v1 不变。线上 badcase 可立即 suspend/回滚；数据库与 JSON Mirror 同步更新版本和当前指针。

**例**：v1 只在冬季工作日推荐“空调→座椅加热”；Hermes 发现夏天同样有上车舒适需求，提出把描述改成泛化的“上车舒适设置”，允许作为文案提案。若还想把步骤改为“空调→座椅通风”，这是不同结构化流程或条件分支，不能仅改文字就发布，须有真实夏季操作证据并重新回放。

### 2026-09-14：已发布 Skill 如何在车机/手机请求中检索和使用（待确认）

**参考与差异**

- memU 按 Skill 名称/描述建检索段、需要时再加载正文；MIRIX 存独立 summary/steps 和过滤 Tag；FluxMem 从相关 Episode 沿 DistillEdge 取得 Skill。借鉴“短摘要定位、按需加载步骤、保留来源”，不把整个 Skill 库塞进每次 Prompt。
- Hermes Skill 主要是给 Agent 阅读的操作指导；我们的在线 Skill 必须返回受限 JSON 与条件/证据，不把 Hermes 生成的自由文本当成可直接调用的车控工具参数。

**建议的运行时读取路径**

1. 上游给 `tenant/user/vehicle/occupant`、当前请求、可信天气/时间/乘员/车辆能力和实时状态。用户明确参数优先，例如“设 23℃”不能被历史 22℃偏好覆盖；身份或车辆不确定时不加载个人车控 Skill。
2. 请求已带规范化车控意图时，先按 `status=published + scope + intent + conditions` 精确查 Skill；自然口语“弄舒服点”可复用已有 BGE 查询向量匹配 Skill 短摘要，低把握再用现有 Planner，而不是另加每次 Skill 专属 Qwen。未知条件不默认当作满足（例如天气缺失时不自动匹配“雨天关窗”）。
3. 取回最多少量候选，再读取相关条件 Preference 来补步骤参数；候选中任何步骤参数缺失或当前车不支持该工具，则返回 `needs_confirmation/unsupported`，不能臆造值。若多个条件 Skill 冲突，优先更具体且证据有效的版本，仍无法确定则询问用户。
4. 记忆系统只返回建议契约：`skill_id/version/intent/steps/parameter_sources/conditions/evidence_ids/match_reasons/missing_inputs`；不发送执行命令。上游权限/安全策略依据当前车况和用户显式授权决定 `recommend/confirm/execute`，每一步最终回执再作为新 Operation Event 回写。
5. 最小安全优先级：当前用户明确指令与实时车况/上游安全策略高于历史 Preference/Skill；Skill 只负责候选流程和参数补全，不能覆盖安全禁令或确认要求。`candidate/suspended/retired` 默认不进入在线执行检索。

**例**：用户说“现在把车里弄舒服点”，U1/V1 的发布 Skill 候选为“空调→座椅加热”；当前为夏季，冬季条件不匹配，不能把冬季 22℃和座椅加热直接作为夏季控车动作。若有夏季 Preference 24℃但没有夏季流程 Skill，可建议单步空调 24℃并询问确认，不从冬季 Skill 擅自拼新流程。

**资源与存储**：正常车控意图走 PostgreSQL 精确索引和少量结果，无额外 LLM；模糊口语复用 BGE/Planner，Prompt 只装相关 Skill 摘要/步骤，不全量注入。已发布版本及 JSON Mirror 仍保持一一对应。

### 2026-09-14：条件 Preference 的存储、匹配与冲突（待确认）

**参考与我们的差异**

- [AWS AgentCore User Preference](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/user-preference-memory-strategy.html) 将偏好抽取与合并分开，并保存 context/preference/category；本地 `调研-车载云端记忆用户画像设计.md` 强调 Profile Item 的多值、条件、作用域、有效期和 Evidence。我们沿用前面拟议的 `profile_items.value_json + conditions + scope + valid_from/to`，额外给车控消费加确定性的参数选择和冲突兜底。
- TiMEM 的高层 Profile 适合给 Agent 理解用户，但自由文本不宜当作空调温度、座椅档位等可执行参数源；车控必须读结构化 Preference Item。

**建议的第一版选择流程**

1. 每条偏好只表达一个 `attribute + value + 适用条件`，保留 `evidence_kind`（用户明确说出/行为观察）、来源 Event、版本与有效期。例：`cabin.temperature=22℃, conditions={winter,weekday}, scope=U1/V1`；另有 `24℃, conditions={summer,weekday}`，两者共存。
2. 运行时先用可信当前上下文过滤：用户/乘员、车辆、时间有效期、工作日/周末、天气/季节等。条件没提供时不把受限偏好当成无条件有效；季节/工作日可在确定时区和地点后由服务端确定性计算，天气与乘员需可信上游。
3. 用户本轮明确指定的值优先于历史偏好；上游安全/车辆能力约束始终必须通过。历史候选中优先范围更贴合当前车辆/乘员、条件更具体且有明确证据的条目，但不设一个固定“全局偏好永远压车辆偏好”的绝对规则。若仍有两个同样适用却冲突的值，返回 `ambiguous` 并附候选与证据，请上游询问用户，不让 LLM 凭空选。
4. 单步行为可形成弱观察候选，但手机/车机执行参数需要明确偏好或经用户确认的值；“最近常设 22℃”与“我希望冬季都设 22℃”不可混为同等强度。用户纠错时旧值失效或缩小条件，新值另建版本，历史 Event 不重写。
5. 查询结果只返回被选中的 Preference ID/版本、value_json、匹配的 conditions、evidence_kind 和歧义/缺失信息。PostgreSQL 属性与 scope 索引先筛，JSONB 条件小集合逐条校验；不需每次 Qwen，也不靠向量 Top-K 决定精确数值。

**例**：U1/V1 在冬季工作日有明确 22℃偏好，夏季工作日有明确 24℃偏好。当前夏季只返回 24℃；若天气条件要求“雨天关窗”但上游没给天气，则不给可执行匹配。若当前明确说“这次设 23℃”，执行参数候选就是 23℃，历史 24℃不覆盖本轮指令。

### 2026-09-14：历史行程与通勤习惯如何沉淀（待确认）

**参考与边界**

- [AWS AgentCore Episodic Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/episodic-memory-strategy.html) 将一段有开始/完成的交互保存为 Episode，并可在跨 Episode 的反思中发现模式；我们借鉴“具体经历先存、规律后归纳”，但行程使用上游结构化记录，不逐次用 LLM 摘要。
- 本地画像调研强调“上周去了杭州”是历史事件，“冬季驾驶偏好 24℃”才是条件偏好。对应地，“常在 7:30 出发”是**统计观察**，不等于“用户希望 7:30 自动出发”。

**第一版建议**

1. 独立 `trip_events`（或同一车控 schema 内的 Trip 表），一行一个 `trip_id`：`tenant/user/driver/vehicle/start_at/end_at/origin_label/destination_label/status/source`，可选行驶里程或停车/充电关联。缺失终点可暂为进行中；迟到数据与纠错按版本更新，不重复新增。精细 GPS 轨迹第一版不存入记忆库；地点标签须来自用户明确设置或可信上游，并遵守隐私/保留期。
2. 车控 `operation_id` 可通过 `trip_id` 关联行程，但手机远程操作允许 `trip_id=null`。行程记录回答“上周去哪了、哪天出发、开了哪辆车”；Operation Event 回答“空调/充电/泊车操作是否成功”，不能互相替代。
3. 后台按 user、车辆、可信地点标签和工作日/周末统计出发时间段、常用车辆、常去目的地，形成 `observed_habit` 候选，保留样本数、覆盖天数、最近时间和来源 trip IDs；阈值与观察窗口是超参数。跨日稳定后可进开放 Profile 的审慎表述（“近期工作日常在 7:20–7:40 出发”），不自动升级为显式 Preference 或自动化授权。
4. 用户明确说“工作日 7:30 提醒我出发”才可建立带条件的通知 Preference/配置候选，且提醒的实际启用由上游确认；“经常 7:30 出发”只支持建议。用户换车或改通勤路线时，历史行程保留，统计窗口和 Profile 版本更新，不抹掉旧事实。
5. 第一版行程聚合用 PostgreSQL 时间与分组查询，不为每次行程调用 Qwen 或生成 BGE 向量；自然语言地点召回只在需要时为简短 Trip 摘要建可选向量。JSON Mirror 对应 `trip_events` 和习惯候选表（若落表）同步。

**例**：U1 的 8 次工作日行程中有 6 次在 7:20–7:40 驾驶 V1 出发，系统可以回答“最近常在这个时段使用 V1”；不能据此回答“你最喜欢 V1”或静默安排 7:30 远程控车。下周改为 V2 后，旧行程仍可查询，近期习惯候选逐步更新。

### 2026-09-14：充电、泊车与远程控车的记忆边界（待确认）

**参考与改造**

- 延续 AWS Episodic Memory 的“真实交互与结果先保存”、AWS User Preference 的“偏好从证据中提取并合并”、MIRIX/FluxMem 的“步骤与来源 Episode 分开”。这些项目不提供特定车辆的充电/泊车/远程权限策略；我们把上游车控能力、实时状态和明确授权隔离在记忆系统之外。
- 三类业务复用同一 Operation Event 契约与结果状态机，避免每项单独设计一套事实层；差异由工具注册表、参数 Schema、权限等级和业务条件约束表达，而非让 LLM 自由生成可执行动作。

**第一版建议**

1. **充电**：历史 Event 保存选择充电站、设目标电量、开始/停止充电等各次请求与成功/失败回执；Preference 可保存用户明确要求的目标电量、常用站点/时段（条件含工作日、车辆、电量区间等）。反复在某站充电只是统计观察，不等于“总要导航到该站”。“到站→开始充电”可作为候选 Skill 流程，但支付、充电桩可用性和电池状态须由上游实时检查。
2. **泊车**：Trip/Operation Event 保存实际停车地点、停车时间、自动泊车请求与回执；常用停车区域是观察习惯，可用于推荐，需考虑位置隐私。仅在用户明确偏好时才保存“优先某停车场/某类车位”的 Preference；自动泊车能力、车位可用性和环境安全由上游判定，Skill 不授权泊车执行。
3. **远程控车**：`source=mobile_app`、`trip_id=null` 正常保存空调、车窗、锁车、香氛等请求与回执；“出发前常开空调”可以形成有时间/天气/车辆条件的候选习惯或 Skill。网络失败/回执 unknown 不算成功。手机登录身份、车主授权、车辆在线状态和安全确认必须由上游提供/判定，不能从历史成功记录继承权限。
4. 三类都保留“历史事实 / 条件 Preference / Skill 候选 / 明确权限配置”的分层。运行时精确查询现有工具参数和 Preference，Skill 只提供候选步骤；无法满足实时条件或有冲突就请求确认/不执行。对充电、泊车、车窗等后果不同的动作，具体确认/禁止策略应由上游 Tool Registry/Policy 声明，不把风险等级硬编码进记忆模型。
5. 资源上按统一 Operation Event 表和相应索引存结构化记录；长位置/遥测信息留上游或受控短期存储，仅保存必要摘要和来源 ID。偏好/Skill 按批次更新，普通车控回执不新增 Qwen 调用；JSON Mirror 与新增数据库行一致。

**例**：过去两周 U1 在 V1 上多次通过手机提前 10 分钟成功开启空调，可生成“出发前预冷”的观察/Skill 候选，条件限定车辆与时段；用户从未明确授权自动化时，只能提醒或请求确认，不能每天自动远程启动。一次手机请求失败不算成功证据。

### 2026-09-14：泛化性澄清——例子不是固定存储类型

- 空调、座椅、充电、泊车、车窗等在本记录中**仅是样例**，不得写成独立记忆类、固定表或硬编码的识别规则。新增车控能力时不应因没有预设“充电偏好”字段就无法记录。
- 通用 Operation Event 保存 `tool_name + typed args + request/result + time + scope + context + evidence`；具体工具和参数 Schema 由上游可扩展的 Tool Registry 声明。记忆系统只验证输入契约与来源，不自行维护一套写死的车型/设备清单。
- 通用 Preference Item 保存 `attribute + typed value + conditions + scope + evidence + validity`；执行相关字段要通过可扩展 Attribute Registry 做单位/范围校验，开放兴趣与新领域可以先作为不可直接执行的候选记忆，而非为了覆盖所有语义扩充固定字段。
- 通用 Skill Item 保存 `intent + ordered tool references + parameter bindings + conditions + evidence + version/status`；所有步骤必须能映射到当前 Tool Registry，映射不了就只保留为待审阅文本建议，绝不能伪造可执行工具名。
- Qwen/embedding负责自然语言的语义理解与候选发现；代码负责结构校验、身份/时间/证据/权限边界。不是依靠“出现空调关键词→写空调表”这类规则。通用性与安全性的平衡是“**数据模型通用，执行契约受控且可配置**”。
- 对上一节的修正：所谓“充电、泊车、远程控车分域”指业务策略可以分别配置，不是给每个领域造独立数据库表或专用 LLM Prompt。后续新增工具主要更新 Registry/测试，不改通用记忆流水线。

### 2026-09-14：主动服务、通知与免打扰偏好是否纳入记忆系统（待确认）

- **应该纳入偏好表达与检索，不应由记忆系统直接主动发送通知**。沿用同一个通用 Preference Item：`attribute/value_json/conditions/scope/validity/evidence`，可表达通知渠道、时间窗、频率、免打扰、某类服务是否允许主动建议等；不为“通知”另造一套记忆存储。
- 用户明确设置“工作日晚 22:00 后勿打扰”属于强约束，应由上游通知策略持有可执行配置；记忆系统保留可追溯的偏好与查询投影。一次忽略通知或偶尔关掉提醒只能作为弱观察，不能自动推断用户永久关闭服务或改变免打扰开关。
- 上游主动服务引擎接收实时触发条件（如车辆状态/行程/预约）后向记忆系统查询当前用户、车辆、时间及渠道适用的偏好；再结合显式授权、法定/安全必要通知优先级、频率限制和实时送达状态决定是否发出。记忆系统不能绕过这些策略。
- 第一版先实现**被动查询接口与偏好沉淀**，不在记忆系统内建设调度器、推送通道或主动服务决策引擎；可通过现有 Profile 批次更新显式偏好，不为每次通知事件额外调用 Qwen。未来若需从通知反馈学习，应把送达/打开/关闭/纠错作为结构化 Event，仍不等于直接授权。
- 参考 AWS User Preference 的独立偏好提取/合并与本地车载画像调研的条件、证据、有效期设计；“记忆只提供约束与证据、主动触发与发送留上游”是我们的车载系统边界。

### 2026-09-14：自动执行、确认与记忆偏好的权限边界（待确认）

**为什么单独讨论**

- Mem0/MIRIX/FluxMem/Hermes 的程序性记忆主要表达“如何做”或从轨迹改进流程，不提供具体车控操作的授权判断。我们复用它们的 Skill 存储/演进思想，但必须额外建立“Skill 不是权限”的边界。
- 通用 Preference 可表达用户的明确限制（例如“不要自动开窗”）或服务方式（例如“操作前先问我”），但一条自然语言记忆不能成为上游执行系统的授权凭证。

**第一版最小决策契约**

1. 记忆系统返回匹配的 Skill/Preference 及 `scope/conditions/evidence/version`，另标明是否只是 `observed`、用户明确表述或已确认；不返回“已授权执行”的结论。
2. 上游 Policy/Permission 服务是自动执行授权的唯一事实源，按 `user + vehicle + tool/capability + conditions + valid_from/to` 管理同意、确认要求、撤销和审计。某辆车支持哪些工具、当前车况是否安全也由上游实时校验。Tool Registry 以可扩展配置提供能力与参数 Schema，而不是在记忆代码写死某类设备的风险名单。
3. 本轮用户明确指令不等于“今后都允许自动执行”；若用户说“以后可以自动预冷”，记忆系统可保存显式意向，但必须走上游授权/确认流程才产生可执行配置。用户撤销时上游立即失效权限，并通知记忆侧使对应 Preference/Skill 不再被错误推荐。
4. 执行决策顺序：上游先验证身份、车辆、实时状态及授权/安全限制，再看记忆给出的条件偏好和 Skill 候选；缺身份、缺可信条件、参数不全、授权不明或策略冲突时返回建议/询问确认/拒绝，不“默认允许”。必要通知及紧急情况的例外由上游策略单独定义，记忆不得覆盖。
5. 每次决策和工具回执以 `operation_id` 追溯到使用的 Skill/Preference ID 与版本；这样可回放“为何推荐/为何确认/为何拒绝”，也能在纠错或删除后定位受影响的版本。

**例**：用户连续多次成功在手机上预冷，Skill 进入 `published`，仍只有“可推荐”。用户若明确授权某车、某条件下自动预冷，上游权限服务记录授权后才可自动调用；若用户撤销，权限立即失效，历史成功 Episode 仍是历史事实，但不能恢复授权。

### 2026-09-14：用户纠错、撤销与删除的证据传播（待确认）

**参考与差异**

- Mem0 的历史记忆操作支持新旧内容的新增/更新/删除或关联；MIRIX 的 Procedure 可按 ID 更新/删除，Hermes Skill 可保留版本并审核改动。我们借鉴“受限变更与版本”，但车载还需要将纠错传到条件 Preference、Skill 候选/发布状态及上游权限，而不修改已经真实发生的车控回执。

**第一版三类请求分开处理**

1. **偏好纠正**：“以后冬季把 V1 空调设 24℃，不是 22℃”→ 旧的同 scope/条件 Preference 标记失效，新值建立版本与当前消息证据；其他车辆或夏季 24℃不受影响。历史“曾设 22℃”的 Operation Event 保持真实，但不得继续作为“当前明确偏好 22℃”的有效证据。依赖旧值的 Skill 参数绑定应重新解析或暂停。
2. **撤销授权/自动化**：“不要再自动预冷”→ 上游 Policy/Permission **立即**撤销或要求确认；记忆系统同步失效相应主动服务/执行意向并暂停相关 Skill 推荐，不能让后台统计再次授予权限。过去成功 Episode 仍可查询，权限状态不能从它们重建。
3. **隐私删除**：“删除我的行程和车控记录”→ 按确认的用户/车辆/时间范围定位 Trip、Operation、相关证据及其衍生习惯、Preference、Skill/Profile；先阻止在线召回，再执行数据库/JSON Mirror/缓存/可控备份的删除或重建流程并审计。与“仅撤销自动化”不同，删除请求不应留可检索原始历史。具体留存义务与备份清除时限需要部署方确认，不在记忆模块擅自定义。

**传播与资源控制**

- 每个衍生项保留源 `event_ids/operation_ids/trip_ids`，纠错/删除时按证据反向索引找受影响项；可先标记 `stale/suspended` 防止误用，再后台重算 Profile/Snapshot 与 Skill 版本，Checkpoint/Job 保证幂等。修正不需要对全用户历史逐条调用 Qwen；优先局部增量处理，只有开放文本 Profile 需要受影响范围内批次重写。
- 同一自然语言纠错可进入原有 Topic→Fact 抽取并标明 `SUPERSEDE`；如果上游有结构化设置/撤销 API，直接按明确字段处理，避免额外 LLM。权限撤销与隐私删除必须走有身份验证的专用入口，不以普通含糊对话直接执行不可逆操作。

### 2026-09-14：车控记忆第一版的验收闭环（待确认）

**参考与区别**

- 本地 `VehicleMem-Eval/README.md` 描述 VehicleMemBench 的多人偏好冲突、条件约束、指代、纠错、状态变化和工具调用精确匹配；`qa_data/*.json` 当前仍为 Git LFS 指针，需获取完整数据才能跑官方金标。FluxMem 的回放/PEMS 借鉴“从真实 Episode 验证 Skill”，Hermes Self-Evolution 借鉴“候选新版过测试和约束再发布”。我们不以文本看着合理作为车控上线标准。

**第一版最小端到端用例**

1. 对话与结构化操作同时到达：同一用户/车辆、请求与成功回执只计一次，几秒后能查到准确结果；失败/unknown 不说成已完成。
2. 条件 Preference：冬季 22℃、夏季 24℃、V1 与 V2、工作日与周末可共存；条件未知、多人冲突时返回缺失/歧义而不是猜值。
3. Skill：跨独立日重复成功的多步操作可形成 candidate，失败和撤销影响候选；同一天重复点击不虚增支持；Hermes v2 不通过回放时 v1 保持发布。
4. 执行边界：无权限、权限撤销、车况不满足、参数缺失时，记忆只返回建议/需确认，上游不得把已发布 Skill 当授权；对用户明确本轮参数不使用旧偏好覆盖。
5. 历史和纠错：可回答上周行程及操作是否成功；纠正偏好后旧值不再作为当前值，历史真实 Event 仍可查；隐私删除后数据库、JSON Mirror、缓存和衍生画像/Skill 不再召回对应数据。

**指标与资源预算**

- 正确性分开测：Fact/Event/Trip/Operation 提取与归属、条件匹配、Skill 步骤及参数、工具调用 exact match、错误执行/越权数、用户确认遗漏、纠错/删除传播成功率。安全相关失败不得被总体平均分掩盖。
- 效率测：每 100 条对话/操作的 Qwen 调用数、input/output tokens、BGE 编码数、PG 查询和 p95 延迟、每用户每年数据库与 JSON Mirror 体积。结构化操作/行程与常规条件检索的目标是 0 次额外 Qwen；Hermes 自进化离线单列成本，不混入在线均值。
- 基线至少包含现有 DesayMem_light、关闭 Skill/只用 Preference、开启 Skill 三组；之后再扩至 VehicleMemBench、CarMem/LoCoMo/LongMemEval 和一年模拟数据。第一版先以可复现实例和回放把身份、条件、授权边界验证清楚，再调阈值。

### 2026-09-14：V1 方案收口为评审基线

- 新建 [DESAYMEM_V1_DESIGN_BASELINE.md](DESAYMEM_V1_DESIGN_BASELINE.md)，将散落在上述讨论中的来源、双输入契约、存储/检索、权限边界、资源目标、P0–P3 实施顺序及未决项压缩为单一评审入口；技术图新增“图 0 双输入收口”。
- 明确当前代码只有对话写入 `/v1/messages` 和记忆搜索 `/v1/memories/search`；结构化 Operation/Trip、条件 Preference、Skill、Hermes 插件尚未实现。原图 1/2 主要展示对话链，不再把车控 JSON 误画成必须经过 Topic/Fact。
- 此基线仍是**评审草案，不是已实现验收报告**。下一步先逐条确认结构化输入、Tool/Attribute Registry 归属和上游权限接口，再做 P0 既有主链修复和 P1 新增结构化事实层；不得直接跳到 Hermes 自进化。

### 2026-09-14：V1 结构化车控 JSON 字段评审（第一项）

- 对之前“请求和回执都必须有用户/乘员等全量字段”的表述作修正：`phase=requested` 有 `operation_id/tenant_id/user_id/vehicle_id/source/tool_name/args/requested_at`；`phase=completed` 只需 `operation_id/tenant_id/vehicle_id/result_status/completed_at`，结果详情、失败码可选。`occupant_id/interaction_id/trip_id/context` 仅在可信且确有数据时提供。
- `operation_id` 关联请求与回执；第一版默认每个操作仅有一条逻辑请求和一个最终回执，按 `(tenant_id, operation_id, phase)` 与内容摘要幂等，`event_id` 可选。若上游会多次修订同阶段状态，则需增加事件 ID/版本；同阶段内容冲突不盲覆盖。`request_id/trace_id` 若有，只做链路追踪。回执可比请求早到，先暂存未关联状态；匹配且通过租户/车辆校验前不对外声称执行成功。
- 用户身份以认证和上游授权校验为准，不接受客户端自由填的 user_id 作为授权证据。工具名/参数用可扩展 Tool Registry 校验，时间须有时区；状态冲突不能按到达顺序盲覆盖，需对账。失败与 unknown 不计入成功 Skill。
- 当前仓库没有该 API 或表，字段只是待审设计契约。下一轮分别评审行程字段、Tool Registry 归属及上游 Permission 接口。

### 2026-09-14：上游发送失败的兜底与字段修正

- 区分两类失败：①车控指令未送达车端，仍用发送前生成的 `operation_id` 报 `phase=result, result_status=failed, failure_stage=dispatch, result_at`，不得填写伪造的车辆完成时间；②上游向记忆服务上报失败，需由上游可靠 Outbox 持久化待上报事件，按相同操作 ID/阶段重试，记忆端幂等接收并支持对账补报。
- 若车端收到指令但执行失败，`failure_stage=execution` 并保留真实回执；始终没有最终回执则记忆侧超时标 `unknown`，不是确定成功或失败。故前一节的 `phase=completed` 修正为 `phase=result`，通用结果时间为 `result_at`；仅车端确有回执时补 `vehicle_completed_at`。
- 物理车控重发或补偿仍由上游按实时车况和权限决定，记忆系统绝不因事件上报失败而重发车控命令。由于上游 Outbox 未在当前仓库，不能声称只靠记忆端就能弥补上游丢失的上报。

### 2026-09-14：V1 行程 JSON 字段评审（第二项）

- 修正前面把 `user_id/driver_id/end_at` 都列为行程必填的提案：最少 `trip_id/tenant_id/vehicle_id/source/started_at/status`，`ended_at` 仅在结束后提供；`driver_user_id` 只有可信识别才提供。位置与起终点标签可选且需符合隐私/保留要求，不能从坐标或频率推断“家/公司”。
- 同一 trip_id 从 `in_progress` 补充到 `completed/aborted`，重复报告幂等，晚到旧状态不可倒退；无终态可标 `unknown`，冲突需对账。若上游没有 trip_id，应在开始时取得服务端生成 ID 或使用稳定的上游记录 ID，不能靠时间邻近合并两趟行程。
- 未识别驾驶员的行程只保存车辆级历史，不能偷偷归入车主/某个乘员的通勤 Profile。仅可信归属且已完成的行程进入个人习惯统计。缺目的地标签时可以答时间/车辆，不能编造去哪。每趟入库不调用 Qwen/BGE；通用 Trip 表与 JSON Mirror 一一对应。
- 这是拟新增接口/表，当前仓库未实现。下一轮评审 Tool Registry 和 Permission 的上游归属及最小对接字段。

### 2026-09-14：Tool Registry 与 Permission 归属评审（第三项）

- 上游车控平台拥有可执行工具目录，建议以版本化只读清单给记忆系统：`tool_name/schema_version/args_schema`，可选结果 Schema/能力标签；记忆侧缓存用于参数校验和 Skill 步骤审核，避免每次操作入库再远程请求。记忆侧 Attribute Registry 仅负责将已验证参数映射为通用条件 Preference，不定义车辆执行能力。
- 已认证、身份与时间合格但工具目录尚未同步的新操作，不丢历史：受限存 `validation_status=unverified`，限制 payload 和隐私内容，目录同步后重验。未验证工具/参数**不能**蒸馏为当前 Preference 或可执行 Skill；正式验证失败需隔离/审计，不能靠 LLM 填一个类似工具名。这修正此前“未知工具一律拒收”的隐含假设。
- 上游 Policy/Permission 是 `allow/confirm/deny`、自动执行授权、撤销、实时车况和车型能力的唯一决策方；记忆只回传 Skill/Preference 候选和证据。上游可把 permission decision ID/版本与 operation_id 关联审计，但记忆入库不要求同步查询权限服务，避免高并发耦合。旧成功轨迹不能覆盖新撤销。
- 待上游确认的是 Registry 交付格式/刷新周期、真实工具参数 Schema、Permission 接口及决策审计字段。当前仓库没有这些接口，文档仅定义边界。

### 2026-09-14：条件来源、时区与缺失值评审（第四项）

- 车控/行程的 `context` 采用可扩展 JSON，不给空调/充电/泊车各设计固定字段。能够影响精确车控匹配的条件须携带 `value/source/observed_at`，并通过受控字段定义检查类型和有效期；未知扩展条件可作历史附加信息，但不能直接驱动可执行 Skill。
- 事件时间必须有时区。工作日/周末可从可信时区确定性计算；季节依赖可信地区/定义，天气必须来自有时间戳的上游，乘员/驾驶员必须来自可信身份系统。不能让模型从一句话或车主身份猜天气、实际驾驶员或可执行条件。
- 缺失条件时照常保存真实 Operation/Trip；对“雨天关窗”等受限 Preference/Skill 不把缺失当作匹配，返回 missing/need_confirmation。未知驾驶员的车辆级行程不能进入个人画像。常规条件处理 0 次额外 Qwen。
- 需与上游确认可提供哪些上下文、更新时间与时区表示。第一版不要求收集所有字段，只对真实有来源的条件作匹配。

### 2026-09-14：本地核对真实上游接口的结果与阻断点

- 搜索当前项目 API、测试和配置后，只有对话写入/记忆搜索，未发现真实车控/行程上报 payload、Tool Registry/Permission 客户端或样例；现有文档中的 JSON 均为我们讨论形成的草案，不能当作上游已承诺接口。VehicleMemBench 是评测数据，也不能替代生产上游契约。
- 当前 `src/desaymem_light/api/app.py` 未见应用层鉴权和认证身份与请求体 scope 对照；云端是否由 API 网关承担，单凭本地仓库无法确认。新结构化入口上线前必须解决认证与租户/用户/车辆作用域可信来源，不能直接相信请求体字段。
- 代码可以预留可插拔 `OperationInputAdapter/TripInputAdapter`，把真实上游格式规范化为通用 DTO，保留 source/schema_version/raw_event_ref；不得靠模型补造缺失 ID、时间、真实回执或驾驶员身份。
- 需要部署/上游团队给脱敏的请求、成功/失败回执、行程开始/完成、工具清单和网关/Permission 说明，才能完成字段映射与数据库迁移冻结。详见 [V1 收口文档第 8 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

## 自建合成样例供上游对齐（2026-09-14）

本轮决定不等待上游数据，先在 [docs/examples/vehicle_memory_v1/README.md](examples/vehicle_memory_v1/README.md) 制作模拟对话、工具目录、车控两阶段事件、行程上报。借鉴现有对话入口与前述结构化车控/行程契约；我们的改动是把设计转为可逐条回放的接口夹具，并明确它**不是** PostgreSQL JSON Mirror。理由是先验证状态机、身份归属、候选 Skill 证据与失败兜底，再拿上游真实脱敏数据做 Adapter 映射。三日重复操作不能自动推出可执行两步 Skill；无交互 ID 时序列关联只能作为候选。

### 继续讨论：车控结果的可信状态（暂定）

 借鉴事件上报的幂等/状态机思想；我们为车载增加“上报成功不等于车辆执行成功”的边界。`requested`、`result` 两阶段不变，内部区分待关联、待结果、成功、发送失败、执行失败、未知、取消和冲突；超时只是未知，迟到回执可修正并审计。多动作拆独立 operation_id，避免部分成功误算。真正可计入 Skill 的成功必须有可信车端/上游终态证据，不能由记忆系统猜测。详细列于 [V1 基线第 10 节](DESAYMEM_V1_DESIGN_BASELINE.md)。这些是讨论草案，不是当前代码能力。

继续收口成功语义：记忆收到上报、平台发送/受理、车端确认完成是三层不同事实。仅第三层可标车控 `success`；平台受理但无车端终态继续 pending/unknown。拟由可追溯 `result_source` 与 `upstream_result_id` 判断证据来源，且 `actual_result` 不能从请求参数复制。若车端仅确认完成、没回传实际温度，只能说“请求 22°C、执行完成”，不能声称“实际温度确认 22°C”。这是我们的车载安全/证据约束，不声称源自 Mem0/StructMem 的原始设计。

### 继续讨论：条件偏好来源与多值（暂定）

参考 Mem0 的 Topic/Fact 证据和 TiMEM 的高层画像思路；我们的车载改造把用户明确表达的设置与车控操作统计分开。显式语句可形成有条件候选偏好，车端确认成功只能形成观察证据，不能伪称为用户声明。多值按 scope/conditions/time 共存，短时 23°C 不能覆盖冬天工作日上车前 22°C；条件缺失不从邻近操作补造。当前样例座椅请求缺 context，因而不能仅靠三日成功操作证明其“冬天”条件，但原始对话可作为独立显式证据。详见 [V1 基线第 11 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 继续讨论：偏好生效与纠正（暂定）

借鉴 Mem0 的新旧记忆操作、TiMEM 的画像蒸馏；我们在车载条件偏好层增加简单版本化状态。明确表达且已核对身份/条件的偏好不必等三次操作即可 active；操作重复只能 observed。用户明确改成新值时新增版本，只在重叠 scope/conditions/time 上使旧版失效；单次相反操作不是纠正。画像 Snapshot 是派生摘要，不是真值源；不能只改摘要不改偏好和证据。具体见 [V1 基线第 12 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 继续讨论：条件偏好检索（暂定）

参考当前项目 QueryPlanner 的多路检索、Memos 的时间过滤、TiMEM/Profile 的高层阅读；我们的车载改造把回答证据与控车参数建议分开。先过滤可信身份/车辆、属性、有效期与实际条件，再检查证据和冲突；精确属性不跑向量/LLM，模糊提问才可用可选 BGE/Qwen。显式 active 可在匹配条件下建议，observed 只能描述习惯，不变成默认执行参数；本轮明确命令优先，缺失关键条件/多值冲突则返回不确定并请 Agent 追问。即使匹配也只给可追溯建议，真实车控须由上游 Policy/Permission 重新核验。详见 [V1 基线第 13 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 继续讨论：Skill 候选从单步开始（暂定）

参考 Mem0 procedure_type、memU Skill、MIRIX Procedure、FluxMem Skill 的过程性记忆思路；我们的车控改造是仅用可信车端成功、Registry 验证和 operation_id 证据生成候选，不让记忆直接执行。模拟三日空调与座椅可分别形成单步候选，但无 interaction_id 时不能只凭时间邻近合成两步 Skill；多步待可信任务关联或真实数据支持后再做。阈值配置化，三次只是模拟初值；candidate 不等于 published，published 也不等于自动执行许可。详见 [V1 基线第 14 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

用户提出通勤时导航、音乐、邮件的多步习惯，修正上一段过严表述：没有 interaction_id 不代表不能发现多步流程。我们新增可追溯 Episode 层，优先由可信 trip_id/同一身份车辆归并，缺 trip_id 才用受限时间窗形成待核验 Episode；多个独立工作日反复共现、顺序相近才汇成多步 candidate。各步参数由实际操作证据支持，不把打开邮件界面扩大为读/发邮件权限；上游 Policy 按步授权。借鉴 MIRIX Episode→Procedure、memU/FluxMem Skill，车载 Trip 锚点、可信回执与安全分级是我们的改造。详见 [V1 基线第 14 节多步小节](DESAYMEM_V1_DESIGN_BASELINE.md)。

继续确定音乐为可变参数：多步 Skill 保存 `play_music(music_slot)` 这样的步骤模板，不固定歌曲；播放内容由当前条件、用户明确要求和有证据的音乐偏好/播放历史解析。条件缺失或多值冲突返回 unresolved_slot，不由模型臆造；打开音乐 App 不等于确知播放内容。Episode 优先用可信 trip_id/驾驶员/车辆，缺 trip 才用可配置时间窗形成待核验片段，切换身份/车辆或新行程即断开。借鉴过程性 Skill 与条件偏好，但槽位、可信上下文、用户隔离是我们的车载改造。详见 [V1 基线第 14 节 Episode/槽位小节](DESAYMEM_V1_DESIGN_BASELINE.md)。

补充修正结果来源：`vehicle_ack` 只适合空调/座椅等车控。导航、音乐、邮件界面分别接受 Registry 所声明的可信导航/媒体/应用服务结果；仍需认证、可追溯且语义精确，不能把“打开应用”当作“播放歌曲/读邮件”。

### 继续讨论：多步 Skill 的稳定步骤与可选步骤（暂定）

借鉴 MIRIX Episode/Procedure、memU/FluxMem Skill；我们的改造是跨独立通勤 Episode 统计每步支持数、共现与顺序，而不是凭一次相邻事件定流程。歌曲内容可变但仍计 `play_music` 步骤；音乐内容的条件映射由另一条 Preference/slot 证据支持。低支持的邮件步骤只能是 optional/观察，不声称每趟都做。阈值参数化、候选附样本量和失败/反例，详见 [V1 基线第 15 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 继续讨论：通勤 Skill 如何被使用（暂定）

借鉴过程性 Skill 的检索与调用思想；我们的车载改造是把“用户主动调用”和“上游主动服务”分开。记忆只返回 published Skill 的可追溯建议、槽位解析和缺失信息，不监听车辆主动下发命令；主动提示受上游通知/免打扰策略约束，每步执行受 Tool Registry/Policy 实时约束。本轮用户明确要求优先，拒绝建议是负反馈，不能由频率覆盖。部分失败停止依赖步骤，不自动重试敏感动作。详见 [V1 基线第 16 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 继续讨论：通勤 Skill 随新旧习惯更新（暂定）

借鉴 Mem0 的新旧记忆处理、memU/MIRIX 的 Skill/Procedure 生命周期；我们的车载改造是 Skill 版本不直接覆盖，持续变化生成新 candidate，对显式拒绝立即停止相应范围推荐。区分永久纠正与“今天”的短时例外，也区分用户拒绝、Policy 拒绝和设备失败。音乐换歌只更新条件 Preference/槽位，不因此重建整条通勤 Skill；不听音乐才改变流程步骤。旧版保留有效期和证据供历史问答，详见 [V1 基线第 17 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 继续讨论：同车多用户归属（暂定）

参考现有 DesayMem 的 tenant/user/vehicle scope；我们的车载改造进一步区分发起者、驾驶员、乘员和只知车辆的匿名事件。手机远程控车属于发起者历史但不证明其在通勤；乘客播放音乐不能算驾驶员偏好；同趟 Trip 也不代表每一步均由驾驶员操作。个人 Episode/Preference/Skill 仅用可信归属的证据，身份不明退回车辆级历史，不能用时间近或 LLM 猜身份。见 [V1 基线第 18 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 三日通勤案例落地为模拟推演

在 [commute_skill_walkthrough.json](examples/vehicle_memory_v1/commute_skill_walkthrough.json) 放入三日导航/音乐/邮件及乘客播放，预期候选为导航→播放音乐两个稳定步骤；邮件一次仅观察，天气与歌单对应仅作条件观察，乘客播放不计驾驶员证据。该文件是派生推演而非已实现接口/数据库镜像，用于后续真实上游字段对齐。见 [V1 基线第 19 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 设计收口还需四步；先讨论存储关系

余下按表/证据、输入状态机与幂等、检索契约、端到端回放验收四步。现有 memory_items 仅 fact/event/cross，profile_items 必填文本 value 和向量且 vehicle_id/occupant_id 必填，不能声称车控条件偏好已由现有表直接支持。拟分原始 operation_events 与聚合 vehicle_operations，Trip 与 Episode 分表，episode_operations 存关联，Skill/偏好以版本与证据边连接。每表对应 JSON Mirror 实际行，不把模拟输入当镜像；详细见 [V1 基线第 20 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 第 1 步决策：条件偏好独立轻量表

现有 profile_items 的文本 value、强制 embedding、必填车辆/乘员及 Event-only 证据与结构化条件车控不符，因此修正早期“直接扩展 profile_items”设想：新增 conditional_preferences + 类型化证据边，支持 JSON 值/条件、多值、版本和观察/显式来源；旧 profile_items/profile_snapshots 继续做开放画像。这样每条操作目标 0 次额外 BGE/Qwen，减少兼容改造与存储；Profile Snapshot 可批次汇总偏好但不是真值源。此为方案决策，未实现。见 [V1 基线第 21 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 双轨与其他表的收口

双轨=既有开放 Profile（LLM 自然语言蒸馏）+新增条件 Preference（结构化低成本真值），不是每个领域再建画像表。Operation 一套通用表覆盖车控/导航/音乐，Trip、Episode、Skill 因生命周期与关系不同各自独立；Fact/Event/Cross、Tag/时间、Session 复用原有表。不建车控 Fact/音乐画像/通勤统计专表。原始事件表只用于幂等和对账，须受限保存与保留期；现有 memory_audit_events 无法无改造承接匿名车控结果流。见 [V1 基线第 22 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 第 2 步：写入事务与镜像（暂定）

Adapter 校验身份/作用域、Schema、时间与大小，按 tenant/operation_id/phase + 内容摘要幂等；相同重试 NOOP，冲突隔离对账。原始事件和当前操作态在同一 PostgreSQL 事务；回执先到待关联，超时 unknown 可被迟到可信回执修正。JSON Mirror 复用已有注册表、触发器、Outbox、MirrorWorker，不另写同步双写代码；这是最终一致而非 API 响应即同步。现有 file_projector 的 reconcile() 是零计数占位，云端上线前须实现实际对账修复，特别验证乱序/删除/多 Worker。见 [V1 基线第 23 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 第 3 步：检索契约（暂定）

现有 SearchResult 只含 Fact/Event/Cross、短期消息和 Profile Snapshot，QueryPlan.intent 也不覆盖结构化车控。为不破坏云端对话 API，先提出独立车载检索入口；purpose 分 answer 与 suggestion，前者可描述历史 observed，后者只取有效 active Preference/published Skill 并返回证据、版本、条件、缺失槽位，绝不直接控车。精确场景 SQL 查询为主，模糊请求才可能用 BGE/Qwen；上游逐步 Policy/Registry 复验。见 [V1 基线第 24 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 第 4 步：合成数据纸面回放

核对当前样例：18 条车控事件对应 9 个不同 operation_id，预期 7 success/1 dispatch failed/1 pending→unknown；末尾回执重复不增加独立成功。5 条行程上报归并 4 趟，未知驾驶员不进个人通勤；三趟派生 Episode 支持导航/音乐候选，邮件一次不足，乘客播放排除。发现通勤推演天气缺来源/观测时间，不能用于可执行条件；导航/媒体/邮件也只是派生案例而非可回放 Operation API 输入。故本轮只完成纸面设计核验，端到端代码、镜像对账、调用量/延迟仍待实施。详见 [V1 基线第 25 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

继续修补第一处缺口：通勤模拟天气、工作日、时区现带模拟 `value/source/observed_at`，但 source 字符串并不证明实际可信；生产仍需认证来源、地域/时区与新鲜度校验。历史天气不能代替当前天气来解析歌曲槽位；缺当前条件时流程可命中，歌曲 unresolved。导航/媒体/邮件的独立请求/可信结果仍未建模拟接口样例，不能声称端到端闭环。见 [V1 基线第 26 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 泛化性修正：样例不是能力穷举

用户指出导航、音乐、邮件的反复举例可能误导成固定分支。修正为统一 `Operation + Tool Registry + Episode + 参数槽位`：任意注册工具走同一写入/归并/检索算法，差异在上游 Schema、结果来源和必要映射配置，不为每类动作写 if/else 或建表。前述 `vehicle_operations` 命名覆盖不了媒体/应用，拟改为通用 `activity_operations`，入口拟 `/v1/operations`；旧草案保留供追溯，以第 27 节为当前决定。未知工具先 unverified 历史，不参与 Skill。见 [V1 基线第 27 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 通用 Operation 契约：两类输入（暂定）

为兼容真实上游，通用操作分 command（同 operation_id 的 requested/result）与 observed_action（可信来源只报已发生动作，单次 observed + source_event_id）；后者不能伪造不存在的请求，前者也不能把平台受理当执行完成。两类由可插拔 Adapter 进入同一 activity_operations 事实层，按各自稳定 ID 幂等，Registry 验证 Schema/可信结果来源；未经验证只存受限历史，不进入个人 Skill。传感器原始遥测不当 Operation，作为可信 context。详见 [V1 基线第 28 节](DESAYMEM_V1_DESIGN_BASELINE.md)。

### 三项收口一次完成（2026-09-14）

按用户要求，将身份/结果可信边界、通用输入/表/证据/镜像契约、模拟回放与 V1 实施范围集中写入 [DESAYMEM_V1_CONTRACT_ACCEPTANCE.md](DESAYMEM_V1_CONTRACT_ACCEPTANCE.md)。新增 [contract_cases.json](examples/vehicle_memory_v1/contract_cases.json) 覆盖 command、observed_action、身份错配、乘客归属、仅受理、未知工具、过期天气、重复与冲突。来源核对纠正：本地 Mem0 是 `memory_type="procedural_memory"`，非 `procedure_type`。新文档明确原项目做法与我们车载改造及 P0–P3 边界。上述仅设计与合成向量；新 API、迁移、真 Reconciler、权限链和真实上游均未验证。检视当前投影器还发现删除后迟到旧 UPDATE 的潜在复活风险，纳入故障验收。

### 四张详细流程图（2026-09-14）

用户反馈原总图过抽象，要求分别展示对话 Add/Search、车机操作 Add/Search。已在 [HTML 技术流程报告](DESAYMEM_TECHNICAL_FLOW_REPORT.html) 总图前加入四张 8–12 步子图，按实际输入、判断、表/证据、LLM/BGE、结果与未实现边界展开。对话图参考 LightMem Topic、Mem0 Fact 操作、StructMem Event/Cross、TiMEM 高层画像并展示我们的 Turn/证据/时间/Token 改造；车机图展示我们基于过程性记忆思路增加的通用 Operation、Trip/Episode、双轨 Preference、Skill、Registry/Policy 与 Outbox。未把橙色拟新增节点说成已部署。入口与解释见 [V1 基线第 29 节](DESAYMEM_V1_DESIGN_BASELINE.md)。
