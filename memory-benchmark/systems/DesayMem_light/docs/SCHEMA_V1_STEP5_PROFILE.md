# 数据表设计 Step 5：L3 Profile

## 1. 定义

Profile 表示相对稳定、可复用的用户特征，例如长期偏好、习惯和约束。

V1 借鉴 TiMEM 的递增更新方式：

```text
当前画像摘要 + 当前有效画像项 + 新 Cross-Event/Event
  -> 一次 Qwen3-32B
  -> 画像操作 + 新自然语言摘要
```

结构化 `profile_items` 是事实源；`profile_snapshots.summary` 是提供给 Agent 的文本投影。

## 2. 更新触发

默认触发：

- 生成新的 Cross-Event 后；或
- 每日低峰存在尚未处理的 Event；或
- 管理接口手动重算。

同一 scope 同时只能有一个 Profile 更新任务：

```text
tenant_id + user_id + vehicle_id + occupant_id
```

V1 先按车辆隔离画像，避免不同车辆和座位环境相互污染。后续如需跨车画像，再增加显式的 `vehicle_id=null` 聚合任务。

## 3. profile_items

不预先定义“音乐只能一个值”或“地点可以多个值”。每一条画像都是一个独立命题。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 主键 |
| `tenant_id` | TEXT | 非空 |
| `user_id` | TEXT | 非空 |
| `vehicle_id` | TEXT | V1 非空 |
| `occupant_id` | TEXT | 非空 |
| `attribute` | TEXT | 开放属性，例如 `喜欢的歌手` |
| `value` | TEXT | 一个原子值，例如 `周杰伦` |
| `normalized_hash` | TEXT | attribute + value 的归一化哈希 |
| `status` | TEXT | `active/superseded/deleted` |
| `first_observed_at` | TIMESTAMPTZ | 首次证据时间 |
| `last_confirmed_at` | TIMESTAMPTZ | 最近确认时间 |
| `valid_from` | TIMESTAMPTZ | 生效时间 |
| `valid_to` | TIMESTAMPTZ | 失效时间，可空 |
| `confirmation_count` | INTEGER | 去重后的原始 Event 数 |
| `model` | TEXT | 生成模型 |
| `prompt_version` | TEXT | Prompt 版本 |
| `created_at/updated_at` | TIMESTAMPTZ | 数据库时间 |

不使用固定 category 枚举；`attribute` 和 `value` 由模型输出短文本。程序只进行 Unicode、空格和大小写归一，不维护领域规则表。

同一 scope 下，`normalized_hash + status=active` 唯一。

## 4. 多值与新旧记忆

默认规则只有三条：

1. 相同 attribute、不同 value：共存；
2. 相同 attribute、相同 value：确认已有项并增加证据；
3. 只有新证据明确表达否定、停止或替代时：新项 supersede 指定旧项。

因此：

```text
喜欢的歌手 = 周杰伦  active
喜欢的歌手 = 陶喆    active
```

可以同时存在，不需要人为配置该字段是不是多值。

## 5. profile_item_evidence

画像证据必须指向原始 Event，而不是把 Cross-Event 再算一份独立证据。

| 字段 | 类型 | 说明 |
|---|---|---|
| `profile_item_id` | UUID | 画像项 |
| `event_id` | UUID | 原始 Event |
| `via_cross_event_id` | UUID | 若通过 Cross-Event 得到则记录，可空 |
| `evidence_role` | TEXT | `support/contradict` |
| `created_at` | TIMESTAMPTZ | 时间戳 |

主键：`(profile_item_id, event_id, evidence_role)`。这样同一 Event 即使被多个 Cross-Event 引用，也只计一次 confirmation。

## 6. 一次 LLM 输出

输入只包含：

- 当前自然语言画像摘要；
- 与新证据相关的当前有效画像项，使用整数编号；
- 新 Cross-Event 及其原始 Event，使用整数编号；
- 当前绝对时间。

默认输入最多 6000 tokens、输出最多 1000 tokens。输出固定 JSON：

```json
{
  "operations": [
    {
      "action": "ADD",
      "attribute": "喜欢的歌手",
      "value": "陶喆",
      "evidence_event_numbers": [2]
    },
    {
      "action": "CONFIRM",
      "profile_item_number": 0,
      "evidence_event_numbers": [1]
    }
  ],
  "summary": "用户通常喜欢周杰伦和陶喆，驾驶时偏好较舒缓的歌曲。"
}
```

允许动作：

- `ADD`：新增独立画像项；
- `CONFIRM`：为已有项增加证据；
- `COEXIST`：显式确认新值与旧值共存；
- `SUPERSEDE`：明确关闭指定旧项，并可新增替代项；
- `NOOP`：证据不足。

模型只能引用提供的整数编号。程序校验动作、引用范围、原子值和证据数量，失败则整个任务重试，不接受部分结果。

## 7. profile_snapshots

每次成功更新追加一个不可变快照：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 主键 |
| `tenant_id/user_id/vehicle_id/occupant_id` | TEXT | scope |
| `version` | INTEGER | scope 内递增 |
| `summary` | TEXT | 给 Agent 的自然语言画像 |
| `model` | TEXT | 生成模型 |
| `prompt_version` | TEXT | Prompt 版本 |
| `created_at` | TIMESTAMPTZ | 时间戳 |

scope + version 唯一。摘要引用关系使用 `profile_snapshot_items(snapshot_id, profile_item_id, ordinal)` 保存，避免 UUID 数组缺少外键。

查询画像时直接读取该 scope 最大版本，不执行向量检索、不调用 LLM。

## 8. 画像项候选控制

为避免把全部历史画像发送给模型：

1. 使用 BGE-M3 对新证据生成批量向量；
2. 从 active profile items 中召回相关 Top-20；
3. 加上最近更新的 10 项；
4. 去重后连同当前 summary 输入模型。

因此需给 `profile_items` 增加 `embedding VECTOR(1024)`。Embedding 不产生 LLM 调用。

## 9. 事务边界

Qwen 调用和 embedding 在事务外完成；校验后同一 PostgreSQL 事务写入：

```text
profile_items 新增/确认/失效
profile_item_evidence
profile_snapshots / profile_snapshot_items
profile_item_relations(supersedes/coexists_with，如有)
memory_audit_events
profile checkpoint
memory_jobs 状态
llm_usage
json_outbox
```

画像更新使用 scope 级 advisory lock，并检查快照版本，防止两个 worker 并发覆盖。

## 10. Profile checkpoint

增加 `profile_checkpoints`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `tenant_id/user_id/vehicle_id/occupant_id` | TEXT | 联合主键 |
| `last_event_occurred_at` | TIMESTAMPTZ | 已处理游标 |
| `last_event_id` | UUID | 稳定排序游标 |
| `last_snapshot_version` | INTEGER | 最近画像版本 |
| `updated_at` | TIMESTAMPTZ | 更新时间 |

NOOP 也推进游标并记录 Audit，防止重复调用。

## 11. 给 Agent 的最终内容

检索接口只传一段简短画像：

```text
用户通常喜欢周杰伦和陶喆，驾驶时偏好较舒缓的歌曲；空调温度倾向 23℃ 左右。
```

同时在内部返回 `snapshot_id/version`，用于排查来源，但不把全部画像项和证据塞入 Agent 上下文。

## 12. 本步明确不做

- 不预定义行业画像字段和单值/多值规则。
- 不根据一次普通表达立即形成高层画像；证据不足返回 NOOP。
- 不使用第二次 LLM 单独润色摘要。
- 不物理覆盖或删除旧画像项。
- 不让 Cross-Event 和其来源 Event 重复计数。
