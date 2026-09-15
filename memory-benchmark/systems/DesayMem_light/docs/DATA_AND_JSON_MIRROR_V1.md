# 数据模型与 JSON 镜像 V1

## 1. PostgreSQL 业务表

| 表 | 作用 | 关键字段 |
|---|---|---|
| `session_messages` | 原始会话消息 | scope、session_id、sequence_no、role、content、occurred_at、request_id |
| `topic_segments` | LightMem Topic 批次 | scope、message_ids、token_count、status、boundary_score |
| `memory_items` | Fact/Event/Cross-Event 统一主体 | scope、type、content、embedding、occurred_at、valid_from/to、status、metadata |
| `memory_evidence` | 记忆到来源的多对多证据 | memory_id、source_type、source_id、observed_at |
| `memory_entities` | Mem0 实体结果 | memory_id、entity、entity_type |
| `memory_relations` | Event/Cross-Event 来源关系 | from_memory_id、relation_type、to_memory_id、ordinal |
| `tag_definitions` | 可复用的轻量 Tag | tenant_id、normalized_name、display_name、source_type |
| `memory_tag_links` | 记忆与 Tag 关联 | memory_id、tag_id、source_id |
| `profile_items` | 结构化画像项 | scope、attribute、value、status、valid_from/to、confirmation_count |
| `profile_snapshots` | 给 Agent 的画像文本 | scope、summary、version、model、prompt_version |
| `profile_item_evidence` | 画像项原始事件证据 | profile_item_id、event_id、evidence_role |
| `profile_item_relations` | 画像新旧及共存关系 | source_profile_item_id、relation_type、target_profile_item_id |
| `profile_snapshot_items` | 快照与画像项关系 | snapshot_id、profile_item_id、ordinal |
| `profile_checkpoints` | 画像增量更新游标 | scope、last_event_occurred_at、last_event_id、version |
| `memory_jobs` | 异步任务与重试 | job_type、scope、payload、status、attempts、next_run_at |
| `cross_event_checkpoints` | Cross-Event 增量游标 | scope、last_event_occurred_at、last_event_id |
| `memory_audit_events` | 增删改和模型决策审计 | entity_table/id、operation、before/after、model、prompt_version |
| `llm_usage` | 调用与 token 账单 | purpose、model、input/output_tokens、latency_ms、status |

所有 scope 字段至少包含 `tenant_id、user_id、vehicle_id、occupant_id`；可跨车生效的用户记忆以 `vehicle_id=null` 表示，而不是丢弃车辆上下文。

## 2. SQLite 兼容表

- `history`：保持 Mem0 变更历史兼容，增加 scope 和 source reference。
- `messages_cache`：只保存抽取所需的短期缓存，不作为原始消息事实源。

SQLite 只允许单个写进程。后续若需要多副本，将这两表迁入 PostgreSQL，不改变上层 repository 接口。

## 3. 镜像目录

```text
json_mirror/
  postgres/<table>/<primary-key>.json
  postgres/<table>/_manifest.json
  sqlite/<table>/<primary-key>.json
  sqlite/<table>/_manifest.json
```

每行一个 JSON 文件，避免更新一行时重写整个表。文件名只使用经过编码的主键；联合主键使用稳定哈希，原始主键仍完整保存在文件内容中。

## 4. 一一对应契约

- INSERT：数据库事务同时写业务行和 `json_outbox`；projector 创建对应 JSON。
- UPDATE：projector 以临时文件写入、fsync 后 `os.replace` 原子替换。
- DELETE：projector 删除对应 JSON；若文件不存在也视为幂等成功。
- JSON 内容恰好包含数据库该行全部列，不加入展示专用字段。
- UUID/text 原样字符串；时间统一 UTC ISO-8601；JSONB 保留结构；`null` 保持 null；vector 使用浮点数组完整保存。
- 对象键排序、UTF-8、稳定浮点序列化，以便计算 SHA-256。
- `_manifest.json` 记录 row_count、last_outbox_id、校验摘要和更新时间，但不是业务行。

`json_outbox`、projector checkpoint、数据库迁移锁等基础设施表不镜像，否则会产生递归镜像。除此之外，所有业务表必须注册镜像策略；迁移新增业务表但未注册时 CI 和启动检查失败。

## 5. 一致性语义

数据库事务与文件系统无法组成真正的单一 ACID 事务，因此 V1 的可靠保证是：

1. 数据库业务行与 Outbox 原子提交，数据库永不出现“已提交但无同步任务”。
2. projector 至少一次消费，通过表名、主键和版本号幂等覆盖。
3. API 写成功前可等待 projector ACK；超时返回 `committed_mirror_pending`，不得用补偿删除回滚已提交业务数据。
4. reconcile worker 定期比较主键集合、行数和哈希，自动补写、修正或删除孤儿 JSON。
5. `/health/mirror` 暴露 lag、pending、failed、last_reconcile_at；镜像异常不允许静默。

## 6. 磁盘约束

BGE-M3 的 1024 维 float32 向量原始约 4 KiB/条，转成 JSON 数字数组通常明显更大。因本项目要求严格一一对应，V1 镜像仍保存完整向量；部署前必须按真实样本测量放大倍数，并配置：

- PostgreSQL/SQLite 数据卷配额；
- JSON 镜像独立卷与容量告警；
- 原始消息、作业和审计的保留周期；
- 禁止把 JSON mirror 当作第二份检索索引。

删除业务记录时 JSON 同步删除；审计记录按独立保留策略保存，因此仍可追溯谁在何时删除了什么。
