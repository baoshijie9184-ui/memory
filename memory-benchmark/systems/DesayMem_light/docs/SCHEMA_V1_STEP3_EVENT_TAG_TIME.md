# 数据表设计 Step 3：L2 Event、Tag 与时间

## 1. Event 定义

V1 将一个已经完成 L1 抽取的 `topic_segment` 转成一个 `Topic Event`。

它表示“一段连续对话中形成的一组事实”，不宣称已经识别出完整现实事件。设计借鉴 StructMem 的 Event 组织方式，但不使用其事实抽取和关系抽取两次 LLM。

## 2. 生成流程

```text
topic_segment
  -> 查询该 Topic 产生的有效 Fact
  -> 聚合消息时间、实体和上游 metadata
  -> 程序生成 Event content
  -> BGE-M3 生成一次 Event embedding
  -> 写 Event、来源关系、Tags、Audit、JSON Outbox
```

Qwen3-32B 调用次数：0。

没有抽取出 Fact 的闲聊 Topic 默认不生成 Event，只在 Topic 上记录 `event_status=skipped_no_fact`。

## 3. Event 在 memory_items 中的字段

复用 `memory_items`，其中：

| 字段 | Event 值 |
|---|---|
| `memory_type` | `event` |
| `topic_segment_id` | 来源 Topic ID，并设置唯一约束 |
| `content` | 确定性生成的检索文本 |
| `embedding` | BGE-M3 1024 维向量 |
| `occurred_at` | 来源消息最早发生时间 |
| `occurred_end` | 来源消息最晚发生时间 |
| `observed_at` | Topic 完成抽取时间 |
| `valid_from/to` | Event 通常为空，不用于偏好有效期 |
| `metadata` | 仅保存语言、来源类型等扩展字段 |

需要在 `memory_items` 增加 `occurred_end TIMESTAMPTZ NULL`。

Event `content` 使用固定模板，保证稳定和可复现：

```text
[2026-09-09T10:00:00+08:00 ~ 2026-09-09T10:02:00+08:00]
- 用户希望播放周杰伦的歌曲
- 用户当前不想听节奏过快的音乐
```

Fact 按 `occurred_at、created_at、id` 排序。V1 不让 LLM润色，不保存重复的完整会话原文。

## 4. Event 与 Fact 的关系

复用 `memory_relations`：

```text
source_memory_id = Event ID
target_memory_id = Fact ID
relation_type    = contains
ordinal          = Fact 在 Event 中的顺序
```

`UNIQUE(source_memory_id, relation_type, ordinal)` 保证顺序；`UNIQUE(source_memory_id, target_memory_id, relation_type)` 保证重试幂等。

从 Event 可以追溯到 Fact，再通过 `memory_evidence` 追溯到 Topic 和原始 Message。

## 5. Tag 设计

Tag 借鉴 memos 的轻量标签方式，不建立固定领域枚举，也不调用 LLM 判断标签。

### tag_definitions

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 主键 |
| `tenant_id` | TEXT | 租户隔离 |
| `normalized_name` | TEXT | 归一化标签，例如 `周杰伦` |
| `display_name` | TEXT | 原始展示名称 |
| `source_type` | TEXT | `metadata/entity/user` |
| `created_at/updated_at` | TIMESTAMPTZ | 时间戳 |

约束：`UNIQUE(tenant_id, normalized_name)`。

### memory_tag_links

| 字段 | 类型 | 说明 |
|---|---|---|
| `memory_id` | UUID | Fact/Event/Cross-Event ID |
| `tag_id` | UUID | Tag ID |
| `source_id` | UUID/TEXT | 产生该标签的 metadata/entity 来源 |
| `created_at` | TIMESTAMPTZ | 时间戳 |

主键：`(memory_id, tag_id)`。

Tag 只来自三种可追溯来源：

1. 请求中明确传入的 `metadata.tags`；
2. Mem0 已生成的实体展示值；
3. 用户明确维护的标签。

V1 只做通用文本标准化：去首尾空格、Unicode 规范化、大小写归一。不会写死“音乐、导航、空调”等领域判断规则。

`vehicle_id、occupant_id、session_id、memory_type` 是结构化字段，不重复做成 Tag。

## 6. 时间模型

统一区分四类时间：

- `occurred_at/occurred_end`：事情实际发生区间，用于用户时间查询。
- `observed_at`：系统何时观察到该信息。
- `created_at/updated_at`：数据库记录生命周期。
- `valid_from/valid_to`：偏好、状态等事实的有效期，不等同于发生时间。

规则：

- API 接收到带时区时间后统一存为 PostgreSQL `TIMESTAMPTZ`；JSON 输出 UTC ISO-8601。
- 消息未提供发生时间时，使用 `ingested_at`，同时在 metadata 标记 `time_inferred=true`。
- Event 时间由消息确定，不让 LLM猜测。
- 相对时间解析沿用 `DesayMem_mem0` 已有能力；保存解析后的绝对时间和原始表达。

索引：

```text
(tenant_id, user_id, occupant_id, occurred_at DESC)
(tenant_id, user_id, vehicle_id, occupant_id, occurred_at DESC)
GiST(tstzrange(occurred_at, occurred_end, '[]'))  -- 仅在范围查询证明需要后启用
```

V1 先使用普通 B-tree；GiST 保留为扩展项，避免初版索引过重。

## 7. 事务与幂等

同一 PostgreSQL 事务写入：

```text
Event memory_items
Event -> Fact memory_relations
tag_definitions / memory_tag_links
memory_audit_events
topic_segments.event_status
json_outbox
```

`topic_segment_id + memory_type=event` 唯一，任务重试只返回已有 Event。Embedding 在事务前生成。

## 8. 本步明确不做

- 不进行跨 Topic 合并；交给下一步 Cross-Event。
- 不使用 LLM 生成 Event 摘要或 Tag。
- 不建立领域 Tag 本体、层级标签或图数据库。
- 不把 Topic 名称自动推断成业务领域。
