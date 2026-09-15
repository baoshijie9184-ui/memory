# 数据表设计 Step 2：L1 Fact

## 1. 写入流程

每个 `topic_segment` 只执行一次 L1 抽取任务：

```text
1. 读取 Topic 内容和最近会话上下文
2. 按完整身份范围召回相关已有 Fact
3. Qwen3-32B 一次性抽取新增事实
4. BGE-M3 批量生成 embedding
5. 内容哈希去重
6. 批量写 Fact、Evidence、Entity、Audit、JSON Outbox
7. 标记 Topic 完成
```

这是对 Mem0 当前代码 Phase 0~8 的业务合并表达；实现仍应保留原阶段日志，不能在汇报中声称原文件只有七个编号阶段。

V1 沿用当前仓库的 ADD-only 思路：新观察优先追加，不让 LLM直接修改历史记录。一次 Topic 正常只调用一次 Qwen3-32B。

## 2. memory_items：Fact 主表

该表后续也承载 Event/Cross-Event，通过 `memory_type` 区分；本步只写 `fact`。

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| `id` | UUID | 主键 |
| `tenant_id` | TEXT | 非空 |
| `user_id` | TEXT | 非空 |
| `vehicle_id` | TEXT | 可空；空表示用户跨车记忆 |
| `occupant_id` | TEXT | 非空 |
| `session_id` | TEXT | 来源会话，可空 |
| `topic_segment_id` | UUID | Fact 来源 Topic，外键 |
| `memory_type` | TEXT | 本步固定 `fact` |
| `content` | TEXT | 标准化事实文本 |
| `content_hash` | TEXT | 标准化文本哈希 |
| `embedding` | VECTOR(1024) | BGE-M3 向量 |
| `status` | TEXT | `active/superseded/stale/deleted` |
| `occurred_at` | TIMESTAMPTZ | 事实发生时间，可空 |
| `observed_at` | TIMESTAMPTZ | 系统观察到事实的时间 |
| `valid_from` | TIMESTAMPTZ | 生效时间，可空 |
| `valid_to` | TIMESTAMPTZ | 失效时间，可空 |
| `model` | TEXT | 抽取模型 |
| `prompt_version` | TEXT | Prompt 版本 |
| `metadata` | JSONB | 非核心扩展信息 |
| `created_at` | TIMESTAMPTZ | 创建时间 |
| `updated_at` | TIMESTAMPTZ | 更新时间 |

索引：

- scope + `memory_type/status/occurred_at`。
- `content_hash` + scope，用于候选去重，不做跨用户唯一约束。
- pgvector ANN 索引；具体 HNSW 参数在真实数据评测后确定。
- PostgreSQL FTS 索引用于关键词召回；名称统一称 FTS，不误称数据库原生 BM25。

## 3. memory_evidence：重复观察不丢失

相同 Fact 不重复建立 `memory_items`，但必须追加证据：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 主键 |
| `memory_id` | UUID | 外键到 Fact |
| `source_type` | TEXT | V1 为 `message/topic` |
| `source_id` | UUID | 来源 ID |
| `observed_at` | TIMESTAMPTZ | 本次观察时间 |
| `created_at` | TIMESTAMPTZ | 创建时间 |

`UNIQUE(memory_id, source_type, source_id)` 保证任务重试不会重复计数。

## 4. memory_entities

沿用 Mem0 Phase 7 的批量实体关联，不新增生成式 LLM：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 主键 |
| `tenant_id` | TEXT | 非空 |
| `user_id` | TEXT | 非空 |
| `vehicle_id` | TEXT | 非空 |
| `occupant_id` | TEXT | 非空 |
| `normalized_text` | TEXT | 实体归一化值 |
| `display_text` | TEXT | 展示值 |
| `entity_type` | TEXT | 人物、地点、音乐等；允许 `unknown` |
| `embedding` | VECTOR(1024) | BGE-M3 向量 |
| `created_at/updated_at` | TIMESTAMPTZ | 时间戳 |

实体与记忆使用 `memory_entity_links(memory_id, entity_id)` 多对多关联，不在 JSONB 中塞 ID 数组。实体按完整 scope 隔离，避免实体召回跨用户或跨车辆泄漏。

## 5. memory_relations：时间冲突关系

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 主键 |
| `source_memory_id` | UUID | 新记忆 |
| `target_memory_id` | UUID | 关联的旧记忆 |
| `relation_type` | TEXT | `related_to/supersedes` |
| `reason` | TEXT | 决策原因 |
| `ordinal` | INTEGER | 有序关系使用，可空 |
| `created_at` | TIMESTAMPTZ | 创建时间 |

时间冲突直接复用 `DesayMem_mem0` 的语义：只有同一用户、同一 `memory_key`、明确更新且值不同，新的事实才 `supersedes` 旧事实；模糊相关或多值偏好只建立 `related_to`。

V1 默认在检索阶段执行冲突消解并过滤被替代项，历史 Fact 保留。若关系被持久化，则写入 `memory_relations`，不物理覆盖旧内容。

## 6. memory_audit_events

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 主键 |
| `entity_table` | TEXT | 被操作表 |
| `entity_id` | UUID/TEXT | 被操作主键 |
| `operation` | TEXT | `ADD/UPDATE/SUPERSEDE/DELETE/NOOP` |
| `before_data` | JSONB | 修改前数据，可空 |
| `after_data` | JSONB | 修改后数据，可空 |
| `source_topic_id` | UUID | 来源 Topic，可空 |
| `model` | TEXT | 参与决策的模型，可空 |
| `prompt_version` | TEXT | Prompt 版本，可空 |
| `created_at` | TIMESTAMPTZ | 时间戳 |

审计表只追加，不随业务数据删除；它自身也是业务可观测表，因此需要 JSON 镜像。

## 7. 事务边界

一个 Fact 批次在同一 PostgreSQL 事务内写入：

```text
memory_items
memory_evidence
memory_entities / memory_entity_links
memory_relations（如有）
memory_audit_events
topic_segments.status
json_outbox
```

Embedding 与 LLM 在事务外先完成，避免长事务。事务失败时整个批次回滚；`topic_segment_id + prompt_version` 作为任务幂等依据。

SQLite `history` 在 PostgreSQL 提交后由兼容投影任务写入，不参与主事务，也不作为成功判定依据。

## 8. 本步明确不做

- 不生成 Event、Cross-Event 或画像。
- 不调用第二次 LLM 做冲突判断。
- 不因相同主题就覆盖历史事实。
- 不把重复观察直接丢弃。
- 不物理删除被 supersede 的 Fact。
