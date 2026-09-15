# 设计 Step 7：JSON Mirror

## 1. 目标与边界

目标：每张业务表的每一行都有一个对应 JSON 文件；数据库 INSERT、UPDATE、DELETE 后，JSON 执行相同变化。

JSON Mirror 用于可视化、核对和导出，不是业务事实源，也不是独立灾备。PostgreSQL/SQLite 与文件系统不能共享 ACID 事务，因此准确承诺是：

```text
数据库变更与 Outbox 强一致
JSON 文件可恢复的最终一致
同步延迟和失败状态可观测
```

不能宣称数据库与 JSON 文件在任意微秒都强一致。

## 2. 镜像范围

必须镜像的 PostgreSQL 业务表：

```text
session_messages
topic_segments
topic_segment_messages
memory_items
memory_evidence
memory_entities
memory_entity_links
memory_relations
tag_definitions
memory_tag_links
profile_items
profile_item_evidence
profile_item_relations
profile_snapshots
profile_snapshot_items
cross_event_checkpoints
profile_checkpoints
memory_jobs
memory_audit_events
llm_usage
```

必须镜像的 SQLite 业务表：

```text
history
messages_cache
```

不镜像的基础设施表：

```text
json_mirror_registry
json_outbox
json_mirror_checkpoint
schema_migrations
数据库锁表
```

否则 Outbox 自身的变更会再次产生 Outbox，形成递归。

## 3. json_mirror_registry

每个数据库各自维护注册表：

| 字段 | 类型 | 说明 |
|---|---|---|
| `table_name` | TEXT | 主键 |
| `primary_key_columns` | JSON/JSONB | 按顺序记录主键列 |
| `enabled` | BOOLEAN | 是否启用 |
| `schema_version` | INTEGER | 序列化契约版本 |
| `created_at/updated_at` | 时间 | 时间戳 |

迁移新增业务表时必须同时注册并创建 Outbox trigger。启动检查发现“业务表未注册”或“已注册表缺 trigger”时，写服务进入 not-ready。

## 4. json_outbox

PostgreSQL 与 SQLite 分别维护自己的 Outbox：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGINT/INTEGER | 单调递增主键 |
| `table_name` | TEXT | 来源业务表 |
| `pk_json` | JSONB/TEXT | 完整主键对象 |
| `operation` | TEXT | `INSERT/UPDATE/DELETE` |
| `row_data` | JSONB/TEXT | INSERT/UPDATE 为 NEW，DELETE 为 OLD |
| `row_version` | BIGINT | 同一行的版本号 |
| `created_at` | 时间 | 入队时间 |
| `applied_at` | 时间 | 成功投影时间，可空 |
| `attempts` | INTEGER | 尝试次数 |
| `last_error` | TEXT | 最近错误，可空 |

业务表触发器在原数据库事务内写 Outbox：

- INSERT 保存完整 NEW；
- UPDATE 保存完整 NEW；
- DELETE 保存完整 OLD，用于确定应删除的文件；
- 回滚业务事务时 Outbox 一起回滚。

`row_data` 会短期增加数据库占用，成功投影的记录默认保留 24 小时后批量清理；长期追溯由 `memory_audit_events` 承担。

## 5. 为什么使用触发器

- API、worker、迁移脚本或运维 SQL 修改业务表时都能产生镜像任务。
- 不依赖每个 repository 开发者记得调用 mirror 方法。
- Outbox 与业务数据天然处于同一数据库事务。

PostgreSQL 使用一个通用 trigger function；SQLite 由迁移为每张表生成固定 trigger。SQLite 表字段改变时必须同步更新 trigger，并由契约测试检查 JSON 键集合。

## 6. 目录与文件名

```text
JSON_MIRROR_ROOT/
  postgres/<table>/<encoded-primary-key>.json
  postgres/<table>/_manifest.json
  sqlite/<table>/<encoded-primary-key>.json
  sqlite/<table>/_manifest.json
```

- 单列 UUID/整数主键直接作为文件名。
- 文本或联合主键使用 canonical `pk_json` 的 SHA-256 作为文件名。
- JSON 文件内部仍保存完整原始主键。
- 路径不得直接拼接用户输入，防止目录穿越。

## 7. Canonical JSON

每个行文件只包含该数据库行的全部列，不加入 `_mirror_*` 等展示字段：

- UTF-8，无 BOM；
- 对象键按字典序排序；
- UUID、TEXT 保持字符串；
- 时间输出 UTC ISO-8601，固定微秒精度；
- JSON/JSONB 递归排序对象键，数组保持原顺序；
- BOOLEAN/NULL 保持原类型；
- PostgreSQL vector 转为完整浮点数组；
- 浮点禁止 NaN 和 Infinity；
- 文件末尾保留一个换行。

例如 `memory_items.embedding` 必须完整写入，不能为了节省空间省略，否则不满足一一对应。

## 8. Projector 执行

V1 只运行一个 `mirror_worker`，按 Outbox `id` 严格顺序消费，避免同一行 UPDATE/DELETE 乱序。

每条任务：

```text
读取 Outbox
 -> registry 校验表与主键
 -> canonical 序列化
 -> 写同目录临时文件
 -> flush + fsync
 -> os.replace 原子替换（INSERT/UPDATE）
 -> 或幂等删除目标文件（DELETE）
 -> 标记 applied_at
```

进程在文件替换后、标记 Outbox 前崩溃时会重复执行；覆盖和删除均为幂等操作。

`row_version` 防止旧事件覆盖新文件。即使未来改成多 worker，projector 也只能应用大于等于文件已知版本的事件；版本信息保存在独立 checkpoint，不写入行业务 JSON。

## 9. Manifest

每张表维护 `_manifest.json`：

```json
{
  "table": "memory_items",
  "schema_version": 1,
  "row_count": 128,
  "last_outbox_id": 3201,
  "content_digest": "sha256:...",
  "updated_at": "2026-09-09T03:00:00.000000Z"
}
```

Manifest 是镜像元数据，不对应数据库业务行。每批 Outbox 完成后原子更新。

## 10. 写接口返回语义

云端测试默认启用 `mirror_wait=true`：数据库提交后等待对应 Outbox ACK，等待上限例如 2 秒。

- 已提交且镜像完成：`status=committed`；
- 已提交但等待超时：`status=committed_mirror_pending`，同时返回 `outbox_id`；
- 数据库事务失败：`status=failed`。

镜像失败后不能通过删除数据库业务行进行“补偿回滚”，否则可能破坏已经成功的记忆链。客户端可通过 `/mirror/status/{outbox_id}` 查询最终状态。

## 11. Reconcile

`reconcile_worker` 每日低峰运行，也支持手动触发：

1. 记录数据库一致性快照和当前 Outbox 高水位；
2. 等待该高水位以前的事件处理完毕；
3. 比较数据库主键集合与 JSON 文件集合；
4. 比较每行 canonical SHA-256；
5. 补写缺失文件、覆盖错误文件、删除孤儿文件；
6. 重建 Manifest 并输出报告。

修复动作写入结构化日志和指标，不写业务 Outbox，避免循环。连续两次校验失败才触发告警，临时扫描错误不直接删除文件。

## 12. PostgreSQL 与 SQLite

- 两个数据库拥有独立 Outbox 和 checkpoint，不尝试构造跨数据库事务。
- PostgreSQL 是业务事实源。
- PostgreSQL 提交后，兼容 worker 把历史投影到 SQLite；SQLite 自己再通过本地事务产生 JSON Outbox。
- SQLite 开启 WAL、busy timeout，并保持单写进程。
- SQLite 投影失败不回滚 PostgreSQL，只进入重试和告警。

## 13. 健康检查与指标

`GET /health/mirror` 至少返回：

```text
postgres_pending
sqlite_pending
oldest_pending_age_seconds
failed_count
last_applied_outbox_id
last_reconcile_at
last_reconcile_mismatch_count
mirror_volume_free_bytes
```

readiness 建议条件：数据库可用、镜像目录可写、无超龄失败事件。短暂 pending 不应导致 API 反复重启。

## 14. 安全与容量

- JSON 含原始消息、画像和完整 embedding，权限必须与数据库备份同级。
- 镜像目录不可由 Web 服务直接静态公开；可视化通过受权 API 读取。
- 云盘启用加密、容量告警和备份策略。
- 不对 JSON 做脱敏，否则会破坏严格一一对应；展示层单独脱敏。
- 1024 维向量在 JSON 中显著大于数据库二进制表示，部署前必须用真实数据测量容量。

## 15. 验收标准

- 每个注册业务表完成 INSERT/UPDATE/DELETE 契约测试。
- 随机抽取数据库行和 JSON，canonical hash 完全一致。
- 模拟 projector 在写文件前后崩溃，重启后最终一致。
- 模拟同一行连续多次 UPDATE 后 DELETE，最终不存在对应 JSON。
- 模拟 SQLite 锁和磁盘满，PostgreSQL 数据不丢失且健康接口明确报警。
- 新增业务表但未注册镜像时，CI 必须失败。

## 16. 本步明确不做

- 不从 JSON 回写数据库。
- 不把 JSON 用作向量或全文检索库。
- 不运行多个 mirror worker。
- 不镜像 Outbox 等基础设施表。
- 不承诺数据库与文件系统跨介质强事务。
