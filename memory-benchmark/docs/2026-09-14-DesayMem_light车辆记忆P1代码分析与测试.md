# DesayMem_light 车辆记忆 P1 代码分析（2026-09-14）

> 对象：`systems/DesayMem_light`（远程 psile/DesayMem_light）
> 本次拉取提交：`3752574 Add vehicle memory P1 PostgreSQL path and design docs`（本地 e044e93 → 3752574，69 文件 +6652/-98）
> 相关文档：仓库内 `docs/DESAYMEM_V1_DESIGN_BASELINE.md`、`docs/DESAYMEM_V1_CONTRACT_ACCEPTANCE.md`、`docs/examples/vehicle_memory_v1/`

---

## 1. 本次更新概览

相比上一版，最大的新增是**车载结构化记忆（Vehicle Memory）双轨系统**：在既有对话记忆流水线（Topic → Fact）之外，新增车控操作（Operation）、行程（Trip）、条件偏好（Preference）、技能候选（Skill）的持久化与检索。

### 新增核心模块

| 模块 | 路径 | 作用 |
|---|---|---|
| 领域模型 | `src/desaymem_light/domain/vehicle.py` | OperationEventIn / TripIn / ConditionalPreference / SkillItem 等 DTO 与状态机 |
| 车控摄入 | `application/vehicle_ingest.py` | 三阶段摄入（requested/result/observed_action），`(tenant_id, operation_id, phase)` 幂等，ToolRegistry 参数校验 |
| Episode 构建 | `application/episode_builder.py` | Operation/Trip 聚合成 Episode |
| 技能蒸馏 | `application/skill_distiller.py` | 确定性聚合（不调 LLM）：同工具+归一化参数 ≥3 次独立成功 → candidate Skill |
| 偏好蒸馏 | `application/preference_distiller.py` | 从成功操作按 tool→attribute 映射提取条件偏好，≥3 次观测 → candidate |
| 车辆搜索 | `application/vehicle_search.py` | answer（历史操作/Trip/偏好）与 suggestion（active 偏好 + skill）两类检索 |
| PG 适配层 | `adapters/postgres/vehicle_memory.py` | `PostgresVehicleMemoryFacade`，DB 事务 + advisory lock 并发安全 |
| 工具注册表 | `modules/vehicle/tool_registry.py` | JSON 加载 ToolSpec，args_schema 类型/枚举/范围校验 |

### 数据库迁移 017–022

- `topic_fact_links`（对话链 fact 间 ADD/CONFIRM/COEXIST/SUPERSEDE 关系）
- `activity_source_events` / `activity_operations`（车控去重审计 + 聚合态）
- `trip_events` / `activity_episodes` / `episode_operations`
- `conditional_preferences` / `conditional_preference_evidence` / `skill_items` / `skill_evidence`
- 所有新表注册 JSON Mirror + capture_json_outbox 触发器

### 对既有流水线的修改

- `hybrid_retriever.py`：关闭默认 `persist_short_term`；按 turn 分组 token 裁剪
- `lightmem_segmenter.py`：embedding 缓存（LRU+TTL）、`topic_max_messages` 上限、idle_flush
- `structmem_synthesizer.py`：Prompt 版本化（CBUF/HISTORY 拆分），强化覆盖合约校验
- `memory_pipeline.py`：Fact 扩展 CONFIRM/COEXIST/SUPERSEDE 三模式
- `api/app.py`：新增 `/v1/operations`、`/v1/trips`、`/v1/vehicle-memory/search` 路由

## 2. 测试结果（2026-09-14，评测机）

- **单元测试**：`82 passed`（含新增 vehicle operations/search、skill distiller、session idle flush、postgres mapping、migrations）
- **集成测试**：`1 passed` — `test_operation_and_trip_survive_new_facade_and_emit_outbox`，验证 Operation 三阶段幂等摄入、Trip 摄入、跨 facade 搜索、JSON outbox 产出（source_events=2 / operations=2 / trips=1）

## 3. 数据库实例状态（评测机 127.0.0.1:20149）

| 库 | 状态 |
|---|---|
| `bench_desaymem_light` | 原评测库（26 表，数据为空），**尚未应用 017–022** |
| `bench_desaymem_light_test` | 本次新建，已应用全部 22 个迁移，可用于 API/worker 验证 |
| `bench_vehicle_it` | 集成测试专用空库（fixture 自建表），测试已通过 |

迁移命令：`POSTGRES_DSN=... SQLITE_PATH=... python -m desaymem_light.cli migrate_main`（入口 `desaymem-migrate`），输出 `postgres migrations applied=22; sqlite migrations applied=4`。

## 4. 关键发现：Skill/偏好沉淀与用户画像未关联

**已确认的缺口**：`skill_items`、`conditional_preferences` 仅有 `user_id` 字段，**没有任何指向 `profile_items`/`profile_snapshots` 的关联表**。车控链沉淀的 skill/偏好是孤岛：

- 用户画像（profile）不会因 skill/preference 沉淀而更新
- 画像检索也看不到这些沉淀
- 对话链有 `topic_fact_links`，车控链缺对应的 profile bridge

这是设计文档 P2/P3 阶段尚未实现的部分，**明日待办：设计并实现 skill/preference ↔ profile 的关联**。

## 5. 如何模拟车机操作验证系统

仓库自带合成回放数据（`docs/examples/vehicle_memory_v1/`）：

- `vehicle_operations.json`：车控操作事件（契约验收：18 条模拟事件 → 9 个逻辑操作）
- `trips.json`：4 趟行程
- `messages.json` / `commute_skill_walkthrough.json`：对话与完整通勤技能 walkthrough
- `tool_registry.json`：工具 schema；`contract_cases.json`：契约验收用例

验证路径（待做）：写回放脚本，用 `PostgresVehicleMemoryFacade` 依次 ingest operations/trips → `EpisodeBuilder` + `SkillDistiller`/`PreferenceDistiller` → `VehicleSearchService` 查 suggestion，对照 walkthrough 预期产出（如通勤 skill candidate）。

## 6. 待办（2026-09-15）

1. 设计 skill/preference ↔ 用户画像（profile_items/profile_snapshots）的关联表与更新链路
2. 编写车辆操作回放脚本（基于 `docs/examples/vehicle_memory_v1/`），端到端验证 skill 蒸馏
3. 对 `bench_desaymem_light` 正式库应用 017–022 迁移
