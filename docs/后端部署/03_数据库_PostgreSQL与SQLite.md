# PostgreSQL、pgvector 与 SQLite

## 1. 为什么有两个数据库

PostgreSQL 和 SQLite 不是二选一：当前 DesayMem 使用三类存储职责。

| 存储 | 内容 | 特点 |
|---|---|---|
| PostgreSQL + pgvector | L1 记忆、向量、实体、Episode、画像信念 | 主记忆库，可做向量检索与结构化查询 |
| SQLite `history.db` | 最近消息、Mem0 风格历史/last-k messages | 单文件、轻量、仅后端内部访问 |
| 模型服务 | 不持久保存业务记忆 | 只负责推理 |

## 2. 当前 PostgreSQL

- PostgreSQL 16.15
- pgvector 0.7.4
- 监听：`127.0.0.1:20143`
- 数据库：`desaymem`
- 用户：`desaymem`
- 数据目录：`/data/pengshuang/desaymem/data/postgres`
- Socket：`/data/pengshuang/desaymem/run/postgres`
- 日志：`/data/pengshuang/desaymem/logs/postgres.log`

检查：

```bash
/data/pengshuang/desaymem/envs/postgres/bin/pg_isready \
  -h 127.0.0.1 -p 20143
```

## 3. 启停 PostgreSQL

启动：

```bash
runuser -u desaymem -- \
  /data/pengshuang/desaymem/envs/postgres/bin/pg_ctl \
  -D /data/pengshuang/desaymem/data/postgres \
  -l /data/pengshuang/desaymem/logs/postgres.log \
  start
```

停止：

```bash
runuser -u desaymem -- \
  /data/pengshuang/desaymem/envs/postgres/bin/pg_ctl \
  -D /data/pengshuang/desaymem/data/postgres \
  stop -m fast
```

不要直接杀死 postgres 进程，不要再次对已有数据目录执行 `initdb`。

## 4. 表和扩展

当前迁移后主要表：

- `memory_items`
- `session_messages`
- `memory_entities`
- `profile_beliefs`
- `user_profile_snapshots`

查看扩展和表：

```bash
/data/pengshuang/desaymem/envs/postgres/bin/psql \
  -h 127.0.0.1 -p 20143 -U desaymem -d desaymem \
  -c "SELECT extversion FROM pg_extension WHERE extname='vector';"

/data/pengshuang/desaymem/envs/postgres/bin/psql \
  -h 127.0.0.1 -p 20143 -U desaymem -d desaymem -c '\dt'
```

## 5. 安全查询示例

```bash
/data/pengshuang/desaymem/envs/postgres/bin/psql \
  -h 127.0.0.1 -p 20143 -U desaymem -d desaymem
```

进入后：

```sql
SELECT id, tenant_id, user_id, content, memory_type, created_at
FROM memory_items
ORDER BY created_at DESC
LIMIT 20;

SELECT tenant_id, user_id, count(*)
FROM memory_items
GROUP BY tenant_id, user_id
ORDER BY count(*) DESC;
```

正式操作尽量通过 API，不要直接修改向量或业务表。

## 6. 数据库迁移

```bash
cd /data/pengshuang/desaymem/apps/DesayMem_mem0
source /data/pengshuang/desaymem/envs/backend/bin/activate

desaymem-migrate apply
desaymem-migrate check
```

迁移脚本具有版本顺序。更新 GitHub 后如新增 `migrations/*.sql`，必须执行 `apply`。

## 7. SQLite 检查

```bash
python - <<'PY'
import sqlite3
p = "/data/pengshuang/desaymem/data/history.db"
con = sqlite3.connect(p)
print("SQLite版本:", sqlite3.sqlite_version)
print("表:", con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
con.close()
PY
```

SQLite 无需独立服务。它由 Python 标准库访问，不需要启动 `sqlite` 进程。

## 8. 备份

PostgreSQL：

```bash
runuser -u desaymem -- \
  /data/pengshuang/desaymem/envs/postgres/bin/pg_dump \
  -h 127.0.0.1 -p 20143 -U desaymem -d desaymem -Fc \
  -f /data/pengshuang/desaymem/data/desaymem_backup.dump
```

SQLite 最好先停记忆后端再复制：

```bash
cp /data/pengshuang/desaymem/data/history.db \
   /data/pengshuang/desaymem/data/history.db.backup
```

