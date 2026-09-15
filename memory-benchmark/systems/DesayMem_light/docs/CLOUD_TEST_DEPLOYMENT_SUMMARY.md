# DesayMem_light 云端测试部署总结

日期：2026-09-09
环境：云服务器（本机），参照 `CLOUD_CODEX_VERIFICATION.md` 的验收流程执行。

## 一、部署环境

| 项 | 值 |
|---|---|
| 代码目录 | `/data/pengshuang/memory-benchmark/systems/DesayMem_light` |
| PostgreSQL | **独立实例** `127.0.0.1:20149`（PG 16.15 + pgvector 0.7.4），库 `bench_desaymem_light`，schema `desaymem_light`（26 表，迁移 v12）。PGDATA 位于 `data/desaymem_light/postgres/`，与 SQLite/JSON 同目录统一管理 |
| SQLite | `/data/pengshuang/memory-benchmark/data/desaymem_light/history.db`（消息缓存） |
| JSON 镜像 | `/data/pengshuang/memory-benchmark/data/desaymem_light/json_mirror/postgres/*.json` |
| LLM | 共享 vLLM `http://127.0.0.1:20140/v1`（model=memory-llm，Qwen3-32B，16384 上下文） |
| Embedding | 共享 TEI `http://127.0.0.1:20141/v1`（bge-m3，1024 维） |
| Tokenizer | 本地 `/data/pengshuang/desaymem/models/Qwen3-32B`（无需下载） |
| API 端口 | 20148（`/health/live`、`/health/ready`、`POST /v1/messages`、`POST /v1/memories/search`） |
| 进程 | API + Memory Worker + Mirror Worker（手动启动，替代 systemd：`PYTHONPATH=src` + `desaymem_light.cli` 各入口） |
| 配置 | `.env`（benchmark 环境变量）+ `config/cloud-test.yaml`（算法参数） |

未触碰 DesayMem_mem0 的代码、数据库与服务（验收红线遵守）。

## 二、失败原因与修复（按发现顺序）

冒烟测试共经历 6 轮失败排查，均为**云端共享服务环境差异**暴露的代码问题，
Windows 开发环境未覆盖。

### 问题 1：pytest 收集失败——模块缺失（阻断级）

- **现象**：`ModuleNotFoundError: No module named 'desaymem_light.adapters.json_mirror'`
- **原因**：`bootstrap/builtins.py` 与 `tests/unit/test_json_projector.py` 都 import
  `adapters/json_mirror/file_projector.py` 的 `AtomicJsonTableProjector`，但该目录
  未随仓库提交（源机器遗漏）。
- **修复**：新建 `src/desaymem_light/adapters/json_mirror/{__init__.py, file_projector.py}`。
  按契约（`contracts/mirror.py`）与测试期望实现：
  - 每表一个 `{root}/{database}/{table}.json`，格式 `{"table_version": n, "rows": {pk: {"data": row, "version": n}}}`
  - `apply()`：按 `row_version` 判新旧，旧事件忽略（乱序/重复 claim 安全）；
    DELETE 移除 key；写文件用临时文件 + `os.replace` 原子替换
  - `reconcile()`：no-op 占位，报告磁盘行数
- **结果**：38 项测试全部通过（验收基线 ≥38 达标）。

### 问题 2：session_segment 任务 dead——BGE 401（阻断级）

- **现象**：`ProviderUnavailableError: BGE-M3 provider request failed`，任务 5 次重试后 dead。
- **排查**：错误被 `except Exception` 包装吞掉了真实原因。用 `build_runtime()` 直连复现，
  拿到真实 traceback：`openai.AuthenticationError: 401 Invalid API key`。
- **原因**：`bootstrap/builtins.py` 注册 `bge_m3_http` 插件时**没有传 `api_key`**
  （Qwen 插件传了），adapter 回退到 `"EMPTY"`；且 `RuntimeSettings` 根本没有
  `bge_api_key` 字段。Windows 开发环境的 BGE 服务无鉴权，所以没暴露。
- **修复**：
  1. `bootstrap/settings.py`：`RuntimeSettings` 增加 `bge_api_key: str = ""` 字段
  2. `bootstrap/builtins.py`：注册时传 `api_key=runtime.bge_api_key`
  3. `.env` / `.env.server.example`：增加 `BGE_API_KEY` 变量

### 问题 3：TEI 硬限制——1024 token/文本、32 文本/请求（阻断级）

- **现象**：修完 401 后 smoke 仍死在 session_segment，同样 BGE 错误。
- **排查**：直连 TEI 服务测试——单条/32 条以内 OK，100 条返回
  `Provide 1 to 32 texts per request`；smoke 消息（内容重复 180 遍 ≈6480 字）
  返回 `Each text must be at most 1024 tokens`。
- **原因**：共享 TEI 部署有两个硬限制，而代码按无限制服务写的。
- **修复**：
  1. `adapters/embedding/bge_m3.py`：`embed()` 前对每条文本截断到 **1200 字符**
     （实测二分：中文 2800/2000/1500 均超限，1200 安全。中文 ≈0.85 token/字）
  2. `config/cloud-test.yaml`（及 example）：`bge_m3_http.batch_size` 100 → **32**

### 问题 4：fact_extract 任务 dead——pgvector 查询类型不匹配（阻断级）

- **现象**：`operator does not exist: vector <=> double precision[]`。
- **原因**：psycopg 把 Python float 列表参数绑定为 `double precision[]`，
  pgvector 的余弦距离算符要求右侧显式为 `vector` 类型。
- **修复**：`adapters/postgres/repositories.py` 中全部 5 处 `<=> %s` 改为 `<=> %s::vector`。

### 问题 5：fact 提取返回空——RECENT/NEW 重复（逻辑级）

- **现象**：fact_extract 任务 completed，但 0 条 fact、topic 标记
  `skipped_no_fact`，`llm_usage` 显示输出仅 7 token（即 `{"memory":[]}`）。
- **排查**：复现 LLM 调用——不带 RECENT_MESSAGES 能提出 fact；带上完全相同的
  RECENT + NEW 时模型返回空。worker 的 input 9865 token（消息内容重复出现两次）。
- **原因**：单条会话场景下，`recent()`（最近 10 条消息）与 topic 内容是同一批
  消息，模型判断"没有新信息"。多轮对话不触发，但单条消息/首轮会话必触发。
- **修复**：`application/memory_pipeline.py` 的 `process_fact` 中，按
  `topic.message_ids` 过滤 recent，不再把 topic 自身包含的消息作为上下文重复喂给 LLM。
- **附带发现**：该问题只影响事实条数为 0 的判定路径，不影响已有数据的正确性。

### 问题 6：`'Vector' object is not iterable`（阻断级）

- **现象**：fact 提取成功后 `similar_facts` 查询返回的行映射崩溃。
- **原因**：pgvector 的 `Vector` 对象只有 `to_list()`（无 `tolist()`、无 `__iter__`），
  `_memory_from_row` 只检查了 `tolist`，然后 `list(embedding)` 报错。
  DesayMem_mem0 之前也遇到过同一坑。
- **修复**：`repositories.py` 3 处 row 映射（`_memory_from_row` + 2 处 profile 查询）
  统一为先查 `to_list()`（pgvector），再查 `tolist()`（numpy）。

### 附带问题：JSON 镜像残留不一致（非代码 bug）

- **现象**：管线打通后全表审计，8 张表 JSON 行数 > DB 行数。
- **原因**：排查期间 mirror worker 多次随代码修复重启，其中一段窗口
  （file_projector 缺失时期）内手工 SQL 清理测试数据产生的 DELETE outbox 事件
  被标记 applied 但文件未实际更新，留下陈旧行。
- **处理**：写了全量重投影脚本（从 DB 当前状态重建每表 JSON，按
  information_schema 真实主键生成行键——注意复合主键表如
  `topic_segment_messages`/`memory_tag_links` 的键是多列 JSON）。
  之后做了**实时探针验证**：直接 INSERT/DELETE 一行 tag_definitions，
  镜像均在 1 秒内同步（worker 正常运转时无任何问题）。

## 三、修复后的验证结果

### 官方 smoke test 通过

```
{"status": "ok", "marker": "SMOKE-6b2a4de37bea", "facts": 1, "events": 1, "llm_calls_on_search": 0}
```

### 验收抽查（CLOUD_CODEX_VERIFICATION.md 第七节）

| 检查项 | 结果 |
|---|---|
| 消息接口 202 | ✅ |
| 四段 pipeline（session→fact→event→cross_event） | ✅ 全部 completed，无重试 |
| Fact/Event 生成 | ✅ fact=1（"用户在测试车辆中偏好较低的音乐音量"）、event=1 |
| 检索接口 200 | ✅ 返回 facts+events，vehicle_id 全链路一致（smoke_vehicle） |
| 检索 llm_calls=0 | ✅（成本目标：检索一次 embedding、零 LLM） |
| dead job | ✅ 0 |
| JSON 目录生成 | ✅ 20 张业务表全部镜像 |
| JSON 与 DB 一致 | ✅ 全表一致（重投影修复后） |
| JSON 修改/删除同步 | ✅ 实时探针 1 秒内 |
| outbox 积压 | ✅ 0 |

### 整链路 LLM 成本

写入一条消息 → 1 次 LLM 调用（fact_extract，40 output tokens）；
检索 → 0 次 LLM、1 次 embedding。符合文档第八节成本目标。

### 修改文件清单

| 文件 | 类型 | 内容 |
|---|---|---|
| `src/desaymem_light/adapters/json_mirror/__init__.py` | 新增 | 包初始化 |
| `src/desaymem_light/adapters/json_mirror/file_projector.py` | 新增 | 原子 JSON 投影器（问题 1） |
| `src/desaymem_light/adapters/embedding/bge_m3.py` | 修改 | 文本截断 1200 字符（问题 3） |
| `src/desaymem_light/adapters/postgres/repositories.py` | 修改 | `<=> %s::vector` ×5（问题 4）；`to_list()` ×3（问题 6） |
| `src/desaymem_light/application/memory_pipeline.py` | 修改 | recent 去重（问题 5） |
| `src/desaymem_light/bootstrap/builtins.py` | 修改 | BGE api_key 透传（问题 2） |
| `src/desaymem_light/bootstrap/settings.py` | 修改 | `bge_api_key` 字段（问题 2） |
| `config/cloud-test.yaml` + `cloud-test.example.yaml` | 修改 | batch_size 32（问题 3） |
| `.env` | 新增 | benchmark 环境配置（端口 20148、共享服务 DSN、数据目录） |
| `.env.server.example` | 修改 | 补 `BGE_API_KEY=EMPTY` |

## 四、还需要测的

### 必测（进入正式 benchmark 前）

1. **L3 Profile 画像链路（未验证）**
   触发条件是事件计数不是时间：攒够 `trigger_event_count`（默认 **10 个 event**）
   才跑 cross-event LLM 摘要，之后才入队 `profile_update`（TiMEM 增量更新，
   有新事件才调 LLM，否则 idle）。smoke 只产生 1 个 event，停在 `pending`
   属正常设计。**测法**：连发 ≥10 条消息（含偏好陈述与偏好变更），验证
   `profile_items`/`profile_snapshots` 产出、`valid_from/valid_to` 演化、
   `profile_checkpoints` 推进。

2. **偏好变更 SUPERSEDE 语义**
   前期发"喜欢爵士乐"、后期发"改听古典音乐"，验证旧 profile_item 的
   valid_to 被置、新 item active，检索时只返回新值。

3. **多租户/多乘员隔离**
   不同 tenant/user/occupant 互相检索不到对方数据（scope 过滤 SQL 已有，
   但未实测）。

4. **Conflicting 历史表/SQLite 镜像路径（低优先）**
   本机流量全走 PG outbox，SQLite 侧（messages_cache、sqlite json_outbox）
   未产生数据；若 benchmark 不用 SQLite 历史兼容层可暂缓。

### 建议测

5. **并发写入**：多个 session 同时 ingest，验证 advisory lock + 幂等键
   （`idempotency_key` 去重）不产生重复 job/fact。
6. **长对话话题切分**：LightMem 分段器在多话题长会话上的切分质量
   （similarity_threshold=0.55、topic_batch_tokens=2000）。
7. **检索质量**：tag/时间过滤（`time_from/time_to`、`tags`）与
   `vehicle_only` 的组合查询行为。
8. **故障恢复**：kill worker 后重启，锁定超时（lock_timeout 300s）内任务
   被其他 worker 接管；outbox 积压后 mirror worker 追平。

### 已知限制（继承自 KNOWN_LIMITATIONS_V1.md，无需修）

- Event 是确定性模板拼接（零 LLM），表达自然度弱于 StructMem 双维度提取
- Cross-Event/Profile 异步最终一致（202 返回后秒级延迟，非立即可查）
- 无 LLM rerank
- JSON 镜像保存完整 embedding，磁盘开销大（本目录当前很小，benchmark 期间观察）

## 五、运维备忘

```bash
/data/pengshuang/frontend_code/cockpit-frontend# 启动顺序：先 PG 实例，再三个应用进程（工作目录 = 仓库根）
su -s /bin/bash desaymem -c "/data/pengshuang/desaymem/envs/postgres/bin/pg_ctl -D /data/pengshuang/memory-benchmark/data/desaymem_light/postgres -l /data/pengshuang/memory-benchmark/data/desaymem_light/postgres/pg.log -w start"

cd /data/pengshuang/memory-benchmark/systems/DesayMem_light
(PYTHONPATH=src nohup python -m uvicorn desaymem_light.api.main:app --host 0.0.0.0 --port 20148 > /tmp/desaymem_light_api.log 2>&1 &)
(PYTHONPATH=src nohup python -c "from desaymem_light.cli import worker_main; worker_main()" > /tmp/desaymem_light_worker.log 2>&1 &)
(PYTHONPATH=src nohup python -c "from desaymem_light.cli import mirror_worker_main; mirror_worker_main()" > /tmp/desaymem_light_mirror.log 2>&1 &)

# 停止 PG 实例
su -s /bin/bash desaymem -c "/data/pengshuang/desaymem/envs/postgres/bin/pg_ctl -D /data/pengshuang/memory-benchmark/data/desaymem_light/postgres stop"

# 冒烟（隔离租户 desaymem_smoke）
PYTHONPATH=src python deploy/smoke_test.py --base-url http://127.0.0.1:20148 --wait-seconds 240

# 重跑测试
PYTHONPATH=src python -m pytest -q   # 38 passed

# 迁移状态
# psql: SELECT max(version) FROM desaymem_light.schema_migrations;  → 12
```

端口规划现状：20140 LLM / 20141 Embedding / 20143 共享 PG（DesayMem_mem0、
StructMem 用）/ 20144 DesayMem benchmark API / 20147 DesayMem_light 前端（server.py）/
**20148 DesayMem_light API / 20149 DesayMem_light PG 实例**。

## 六、v12 更新（2026-09-10）：L0 短期记忆 + fact prompt v2

针对"指令式请求（播放周杰伦/空调调温）不被召回"的两项修复：

1. **L0 短期记忆层**（迁移 `012_short_term_memories.sql`）
   - 新表 `short_term_memories`：pgvector `VECTOR(1024)` + HNSW（cosine），
     关联 `session_messages(id)`；每 scope（tenant+user+occupant）滚动窗口 100 条，跨 session
   - 写入路径：`IngestService.ingest()` 同步 embed（TEI ~20ms）→ 与消息同事务写入 + trim → 返回 202
   - 检索路径：`FilteredVectorRetriever` 第 4 路召回 `unit.short_term_memories.search()`，
     agent_context 新增 `[近期对话]` 段（`short_term_top_k` 默认 5）
   - **202 后立即可检**，不依赖 worker 切段/蒸馏进度
2. **fact 抽取 prompt v2**（`prompts/fact/v2.txt`，`config/cloud-test.yaml` `prompt_version: v2`）
   - v1 只抽 durable facts，指令式请求被丢弃 → 蒸馏链断链
   - v2 明确"请求表达的偏好要抽取"（播放周杰伦→喜欢周杰伦的歌；空调调到22度→偏好22度），
     泛指令（打开空调，无参数）仍跳过

E2E 验证（tenant-e2e/user-l0-test）：消息 "为我播放周杰伦的音乐…空调调到22度" →
立即检索 short_term 命中（score 0.71）→ 连发 5 条触发切段 → fact v2 产出
"用户喜欢听周杰伦的歌" + "用户偏好空调温度22度"（prompt_version=v2）→ 检索三路召回齐全。

注意：api/main.py readiness 检查 `row["version"] >= 12`；前端 20147（index.html）
已在 TABLE_GROUPS/doSearch/sendMessage 中接入 short_term 展示。

注意：worker 日志默认无输出（poll 循环安静运行），排查任务失败看
`memory_jobs.last_error` 与 `memory_audit_events`，不要只盯进程日志。
