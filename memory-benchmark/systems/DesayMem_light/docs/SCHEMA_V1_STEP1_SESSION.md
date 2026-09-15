# 数据表设计 Step 1：SessionBuffer

## 1. 统一身份范围

所有会话和记忆表统一使用：

```text
tenant_id    租户，必填
user_id      用户，必填
vehicle_id   当前车辆，必填
occupant_id  当前座位/乘员，必填
session_id   会话，Session 层必填
```

`vehicle_id` 在原始消息中必须存在；只有后续确认可跨车辆生效的长期记忆，才允许保存为 `vehicle_id = null`。

## 2. session_messages

PostgreSQL 中的原始消息事实表，不修改原文。

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| `id` | UUID | 主键 |
| `tenant_id` | TEXT | 非空 |
| `user_id` | TEXT | 非空 |
| `vehicle_id` | TEXT | 非空 |
| `occupant_id` | TEXT | 非空 |
| `session_id` | TEXT | 非空 |
| `sequence_no` | BIGINT | 会话内递增 |
| `request_id` | TEXT | 接口幂等键 |
| `role` | TEXT | `user/assistant/system` |
| `content` | TEXT | 原始内容，非空 |
| `content_tokens` | INTEGER | 本地 tokenizer 统计 |
| `occurred_at` | TIMESTAMPTZ | 消息实际发生时间 |
| `ingested_at` | TIMESTAMPTZ | 云端接收时间 |
| `metadata` | JSONB | 车型、语言等非核心扩展字段 |
| `buffer_status` | TEXT | `pending/segmented/failed` |
| `created_at` | TIMESTAMPTZ | 数据库创建时间 |
| `updated_at` | TIMESTAMPTZ | 数据库更新时间 |

关键约束：

- `UNIQUE(tenant_id, request_id)`：请求重试不重复写入。
- `UNIQUE(tenant_id, user_id, vehicle_id, occupant_id, session_id, sequence_no)`：保证会话顺序唯一。
- 常用索引：完整 scope + `session_id, sequence_no`；`buffer_status, ingested_at`。

## 3. topic_segments

LightMem Topic 切分结果，也是一次 Mem0 抽取批次。

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| `id` | UUID | 主键，同时作为 `topic_id` |
| `tenant_id` | TEXT | 非空 |
| `user_id` | TEXT | 非空 |
| `vehicle_id` | TEXT | 非空 |
| `occupant_id` | TEXT | 非空 |
| `session_id` | TEXT | 非空 |
| `start_sequence_no` | BIGINT | 起始消息序号 |
| `end_sequence_no` | BIGINT | 结束消息序号 |
| `content` | TEXT | 按顺序拼接的抽取输入 |
| `token_count` | INTEGER | 批次 token 数 |
| `boundary_reason` | TEXT | `semantic/token_limit/session_end/manual` |
| `boundary_score` | REAL | 相邻消息语义边界分数，可空 |
| `status` | TEXT | `ready/processing/completed/failed` |
| `event_status` | TEXT | `pending/completed/skipped_no_fact/failed` |
| `retry_count` | INTEGER | 默认 0 |
| `extract_model` | TEXT | 实际使用的模型，可空 |
| `prompt_version` | TEXT | 抽取提示版本，可空 |
| `created_at` | TIMESTAMPTZ | 创建时间 |
| `completed_at` | TIMESTAMPTZ | 完成时间，可空 |

关键约束：

- `start_sequence_no <= end_sequence_no`。
- 同一个消息在 V1 只能属于一个正式 Topic；重试复用原 Topic，不重新切分。
- `content` 是可审计的抽取输入快照，避免原始消息后续处理变化导致无法复现。

消息归属单独保存在 `topic_segment_messages`，避免 UUID 数组无法建立外键：

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| `topic_segment_id` | UUID | 外键到 `topic_segments` |
| `message_id` | UUID | 外键到 `session_messages`，并设唯一约束 |
| `ordinal` | INTEGER | Topic 内消息顺序 |

联合主键为 `(topic_segment_id, ordinal)`；这样既能保证顺序，也能从 Topic 严格追溯原始消息。

## 4. 处理流程

```text
写 session_messages
  -> 加载同 scope/session 的 pending 消息
  -> BGE-M3 相邻消息相似度进行 Topic 边界判断
  -> 约 2000 tokens、语义切换、session_end 或 manual 时封包
  -> 写 topic_segments(status=ready)
  -> 投递 L1 extraction job
```

Topic 边界阈值属于配置和模型版本，不写死在业务规则中。V1 不使用生成式 LLM 判断 Topic。

## 5. JSON 镜像示例

数据库每一行对应一个完整 JSON 文件，例如：

```json
{
  "buffer_status": "pending",
  "content": "播放周杰伦的歌",
  "content_tokens": 8,
  "created_at": "2026-09-09T02:00:00.000Z",
  "id": "01817d69-4e36-7b21-8000-000000000001",
  "ingested_at": "2026-09-09T02:00:00.000Z",
  "metadata": {},
  "occupant_id": "driver",
  "occurred_at": "2026-09-09T01:59:59.000Z",
  "request_id": "req-001",
  "role": "user",
  "sequence_no": 1,
  "session_id": "session-001",
  "tenant_id": "desay",
  "updated_at": "2026-09-09T02:00:00.000Z",
  "user_id": "user-001",
  "vehicle_id": "vehicle-001"
}
```

路径：`json_mirror/postgres/session_messages/<id>.json`。

## 6. 暂不加入

- 不保存 SensoryBuffer 独立表：它是可从 pending 消息恢复的运行时视图。
- 不在本层生成 Tag、Event 或画像。
- 不把完整原始消息重复写入 SQLite；SQLite 仅保存 Mem0 所需短期缓存。
