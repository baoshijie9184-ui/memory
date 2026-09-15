# DesayMem_light 云端 Codex 验收任务说明

请在云服务器上检查并验证本项目。目标是验证云端测试部署，不要直接用于生产流量。

## 一、项目背景

项目路径预计为：

```text
/opt/desaymem-light
```

系统采用：

- Qwen3-32B OpenAI-compatible接口；
- BGE-M3 Embedding接口，固定1024维；
- PostgreSQL及pgvector作为主数据库；
- SQLite作为兼容历史及小型缓存；
- PostgreSQL Outbox逐表同步JSON文件；
- API、Memory Worker、JSON Mirror Worker三个进程。

记忆链路：

```text
SessionBuffer
  -> LightMem风格Topic切分
  -> Mem0 ADD-only Fact抽取
  -> 确定性Event
  -> StructMem风格Cross-Event
  -> TiMEM风格Profile
  -> Tag、时间与向量联合检索
```

## 二、安全边界

开始前必须遵守：

1. 不修改或删除现有 `DesayMem_mem0` 的代码、数据库表、数据卷和服务。
2. 确认新系统使用独立的 `desaymem_light` PostgreSQL schema。
3. 确认SQLite和JSON使用独立目录。
4. 执行迁移前先确认数据库已有可恢复备份。
5. 不在日志或回复中输出数据库密码、API Key、完整用户消息或画像内容。
6. 未经明确许可，不清理数据库、不删除数据、不开放公网端口。
7. 发现迁移版本或校验和冲突时立即停止，不要手工修改 `schema_migrations`。

## 三、先做静态检查

检查以下文件是否存在且内容相互一致：

```text
pyproject.toml
config/cloud-test.yaml
migrations/postgres/001...011
migrations/sqlite/001...004
.env.server.example
deploy/server_install.sh
deploy/server_validate.sh
deploy/smoke_test.py
deploy/systemd/*.service
```

重点检查：

- 配置中的插件名均已注册；
- `hybrid_v1` 检索插件装配正确；
- Qwen Tokenizer本地路径存在；
- PostgreSQL DSN指向预期测试数据库；
- BGE维度为1024；
- API端口不与 `DesayMem_mem0` 冲突；
- systemd用户对SQLite和JSON目录有写权限；
- JSON Mirror Worker只能启动一个实例；
- 代码不存在明显语法、导入、SQL参数顺序和异步事务问题。

先执行：

```bash
cd /opt/desaymem-light
python3 -m compileall -q src deploy/smoke_test.py
```

如服务器允许安装开发依赖，再运行：

```bash
python3 -m venv .verify-venv
.verify-venv/bin/pip install -e '.[tokenizer,dev]'
.verify-venv/bin/pytest -q
```

预期基线：至少38项测试通过。不要因为数量变化直接判失败，应确认新增测试是否合理且全部通过。

## 四、检查环境配置

环境文件建议位于：

```text
/etc/desaymem-light/desaymem-light.env
```

仅报告变量“已配置/未配置”，不要输出变量值。需要检查：

```text
DESAYMEM_CONFIG_PATH
DESAYMEM_HTTP_PORT
QWEN_BASE_URL
QWEN_MODEL
QWEN_API_KEY
QWEN_TOKENIZER_PATH
BGE_BASE_URL
BGE_MODEL
EMBEDDING_DIMS
POSTGRES_DSN
SQLITE_PATH
JSON_MIRROR_ROOT
```

确认：

- `QWEN_BASE_URL`和`BGE_BASE_URL`包含服务要求的正确API前缀；
- Tokenizer与部署的Qwen系列相匹配；
- SQLite父目录和JSON目录位于持久化磁盘；
- JSON磁盘剩余空间不低于配置阈值；
- 环境文件权限建议为0600。

## 五、数据库检查与迁移

先进行只读检查：

- PostgreSQL版本；
- pgvector扩展是否可用；
- 当前Mem0旧系统使用的schema；
- `desaymem_light` schema是否已存在；
- 当前最高迁移版本及迁移校验和；
- 应用数据库账号权限是否限制合理。

确认备份和目标数据库无误后，才执行：

```bash
sudo ENV_FILE=/etc/desaymem-light/desaymem-light.env \
  bash /opt/desaymem-light/deploy/server_validate.sh
```

该脚本会：

1. 检查Python导入；
2. 执行PostgreSQL 001—011迁移；
3. 执行SQLite 001—004迁移；
4. 检查schema版本和pgvector；
5. 检查JSON Mirror registry；
6. 调用一次BGE验证1024维；
7. 对Qwen执行一次极小JSON响应测试。

如果任何一步失败，停止启动服务并报告准确原因。

## 六、启动与运行检查

Preflight通过后执行：

```bash
sudo systemctl enable --now desaymem-light-mirror.service
sudo systemctl enable --now desaymem-light-worker.service
sudo systemctl enable --now desaymem-light-api.service
```

检查：

```bash
set -a
source /etc/desaymem-light/desaymem-light.env
set +a
sudo systemctl status desaymem-light-api.service --no-pager
sudo systemctl status desaymem-light-worker.service --no-pager
sudo systemctl status desaymem-light-mirror.service --no-pager
curl -fsS http://127.0.0.1:${DESAYMEM_HTTP_PORT}/health/live
curl -fsS http://127.0.0.1:${DESAYMEM_HTTP_PORT}/health/ready
```

确认日志中不存在持续重启、数据库连接错误、Embedding维度错误、Tokenizer加载错误或任务持续重试。

## 七、端到端验收

运行：

```bash
python3 /opt/desaymem-light/deploy/smoke_test.py \
  --base-url http://127.0.0.1:${DESAYMEM_HTTP_PORT} \
  --wait-seconds 180
```

验收脚本使用隔离的 `desaymem_smoke` tenant，并产生一次小规模LLM抽取调用。

必须验证：

1. 消息接口返回202；
2. Session任务被Worker领取并完成；
3. Fact与Event成功生成；
4. 检索接口返回200；
5. 检索统计中 `llm_calls=0`；
6. PostgreSQL不存在该测试链路产生的dead job；
7. JSON目录生成对应表文件；
8. JSON中的测试记录与数据库当前记录一致；
9. 对一条专用测试记录执行修改和删除验证时，JSON也对应修改和删除；若没有安全的专用测试数据，不要在已有数据上测试删除；
10. `vehicle_id` 在消息、Fact、Event、任务和JSON中保持一致。

同时抽查：

```sql
SELECT job_type, status, attempts, last_error
FROM desaymem_light.memory_jobs
WHERE tenant_id = 'desaymem_smoke'
ORDER BY created_at;

SELECT memory_type, vehicle_id, status, occurred_at
FROM desaymem_light.memory_items
WHERE tenant_id = 'desaymem_smoke'
ORDER BY created_at;

SELECT count(*) AS pending_mirror_events
FROM desaymem_light.json_outbox
WHERE applied_at IS NULL;
```

不要在报告中输出 `content`、Embedding或敏感字段。

## 八、资源与调用检查

确认第一版符合以下成本目标：

- Topic切分：Embedding调用，无LLM调用；
- Fact抽取：每个Topic最多一次Qwen调用；
- Event：零LLM调用；
- Cross-Event：默认累计10个Event后最多一次Qwen调用；
- Profile：每批最多一次Qwen调用，新值一次批量Embedding；
- 检索：一次Embedding，默认零LLM调用；
- JSON镜像：零模型调用。

检查数据库连接池、Worker CPU/内存、模型调用延迟、LLM Token记录、JSON文件大小和Outbox积压。

## 九、验收通过标准

只有同时满足以下条件，才回复“可以进入云端测试”：

- 静态检查没有阻断问题；
- 所有可运行测试通过；
- 迁移版本达到11且无校验和冲突；
- pgvector可用，BGE返回1024维；
- Qwen JSON调用成功；
- 三个systemd服务稳定运行；
- Smoke Test成功；
- 没有dead job或持续retry；
- PostgreSQL到JSON的新增、修改、删除同步正确；
- 未影响现有 `DesayMem_mem0`。

## 十、云端Codex需要返回的报告

请用简洁中文返回：

```text
结论：通过 / 有条件通过 / 不通过

已验证：
- ...

发现的问题：
- 严重级别、文件或服务、原因、建议修改

云端实测：
- PostgreSQL migration版本
- API/Worker/Mirror状态
- BGE维度
- Qwen连通性
- Smoke Test结果
- JSON同步结果
- dead/retry job数量

是否可以进入云端测试：是 / 否
```

如果发现代码问题，可以修改项目代码并补测试，但不要自行更改现有云数据库数据或旧系统服务。修改后列出全部文件、测试结果和仍未验证的项目。
