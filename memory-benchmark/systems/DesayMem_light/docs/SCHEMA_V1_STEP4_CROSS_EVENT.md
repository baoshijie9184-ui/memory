# 数据表设计 Step 4：Cross-Event

## 1. 定义

Cross-Event 是多个相关历史 Event 的跨事件总结，用于表达重复模式、变化或连续经历。

借鉴 StructMem：以当前事件窗口为种子，检索语义相关的历史 Event，调用一次 LLM 形成独立记忆，并保留来源 Event ID。

Cross-Event 不是画像，也不直接产生稳定用户属性。

## 2. 触发方式

按完整 scope 独立累计：

```text
tenant_id + user_id + vehicle_id + occupant_id
```

默认触发条件：

- 新增 10 个尚未处理的 Event；或
- 每日低峰对剩余 Event 执行一次 flush；或
- 管理接口手动触发。

触发只负责创建一个 `memory_jobs(job_type=cross_event)`，不会阻塞消息写入和 L1。

## 3. 候选 Event 检索

### 3.1 当前窗口

按 `occurred_at, id` 读取本次尚未处理的 Event，默认最多 10 个，作为 seed events。

### 3.2 历史扩展

使用 seed events 的平均 embedding 检索历史 Event：

1. 强制相同 tenant/user/vehicle/occupant；
2. 仅检索 `memory_type=event AND status=active`；
3. `occurred_at` 必须早于本次窗口结束时间，禁止未来信息；
4. 默认回看 90 天，可配置；
5. BGE-M3 向量 Top-K 默认 10；
6. 去除当前窗口中已经存在的 Event ID。

Tag 仅作为可选过滤或加分信号，不调用 LLM 生成检索条件。V1 不跨车辆聚合，避免把不同驾驶环境混入同一模式；跨车信息由后续 Profile 层处理。

## 4. Token 控制

输入不传原始消息，只传 Event：

```text
事件编号 + occurred_at/occurred_end + Event content
```

默认限制：

- 最多 20 个 Event；
- Qwen 输入最多 6000 tokens；
- 输出最多 500 tokens；
- 超限时优先保留 seed events，再按向量相关度保留历史 Event。

一次窗口最多一次 Qwen3-32B 调用。如果模型失败，任务重试；不降级为多次逐事件调用。

## 5. LLM 输出

要求 Qwen3-32B 返回固定 JSON：

```json
{
  "should_create": true,
  "summary": "用户在多次通勤中倾向播放周杰伦的舒缓歌曲。",
  "supporting_event_numbers": [0, 2, 4]
}
```

- `should_create=false` 表示事件之间没有足够联系，记录 NOOP 后结束。
- Prompt 只暴露连续整数编号，不暴露可伪造的 UUID。
- 返回编号必须是输入子集，否则本次结果判为失败。
- 至少需要 2 个支持 Event，避免把单个 Event 重写成 Cross-Event。
- 不让 LLM 输出 Tag、用户画像或数据库操作。

## 6. 数据存储

Cross-Event 继续写入 `memory_items`：

| 字段 | Cross-Event 值 |
|---|---|
| `memory_type` | `cross_event` |
| `content` | LLM 返回的 summary |
| `embedding` | summary 的 BGE-M3 向量 |
| `occurred_at` | 支持 Event 最早发生时间 |
| `occurred_end` | 支持 Event 最晚发生时间 |
| `observed_at` | Cross-Event 生成时间 |
| `model` | Qwen3-32B 实际模型名 |
| `prompt_version` | Cross-Event Prompt 版本 |
| `derivation_key` | scope + 有序来源 ID + prompt_version 的 SHA-256 |

需要在 `memory_items` 增加 `derivation_key TEXT NULL`，并对非空值建立唯一索引，保证相同来源和 Prompt 的任务重试不会重复生成。

来源关系写入 `memory_relations`：

```text
source_memory_id = Cross-Event ID
target_memory_id = supporting Event ID
relation_type    = summarizes
ordinal          = Event 在模型输入中的顺序
```

只关联模型明确引用的支持 Event，不把所有候选都记为证据。

## 7. Event 消费游标

增加 `cross_event_checkpoints`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `tenant_id/user_id/vehicle_id/occupant_id` | TEXT | 联合主键范围 |
| `last_event_occurred_at` | TIMESTAMPTZ | 已处理到的事件时间 |
| `last_event_id` | UUID | 同时间下的稳定游标 |
| `updated_at` | TIMESTAMPTZ | 更新时间 |

即使 LLM 返回 NOOP，窗口中的 seed events 也推进 checkpoint，防止每日重复消耗相同输入。手动重算使用独立管理接口，不倒退在线游标。

## 8. 事务边界

LLM 和 embedding 在事务外完成；校验通过后，一个 PostgreSQL 事务写入：

```text
Cross-Event memory_items（若 should_create=true）
memory_relations(summarizes)
memory_audit_events（ADD 或 NOOP）
cross_event_checkpoints
memory_jobs 状态
llm_usage
json_outbox
```

如果数据库事务失败，任务可按 `derivation_key` 安全重试。所有业务表的增删改继续同步到逐行 JSON。

## 9. 更新与删除

- Cross-Event 默认不可原地修改；Prompt 升级或重算产生新版本，并通过关系标记旧版本 superseded。
- 来源 Event 被删除时，不立即级联删除 Cross-Event；将其标记 `status=stale`，异步重算。
- 物理清理只由保留策略执行，并同步删除 JSON；Audit 仍保留。

## 10. 本步明确不做

- 不构建图数据库或全局事件图。
- 不跨车辆、跨用户或跨 occupant 聚合。
- 不逐 Event 调用 LLM。
- 不在 Cross-Event 阶段更新画像。
- 不用 Cross-Event 作为独立证据重复增加画像 evidence count。
