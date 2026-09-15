# DesayMem Light V1：车载结构化记忆契约与验收基线

状态：**实现状态：P1+P2 薄实现已编码**（`/v1/operations`、`/v1/trips`、`/v1/vehicle-memory/search`；Skill 默认仅 `candidate`，不自动 `published`）。契约仍为验收基线；真实上游尚未交付，样例为合成数据。若后续上游字段不同，由 Adapter 映射，不修改核心归并语义。当前实施以仓库代码与迁移 019–021 为准，不能把本文当部署证明。

## 1. 来源、我们的改造、边界

- [Mem0 本地源码 `memory/main.py`](../../code/mem0/mem0/memory/main.py)：存在 `memory_type=procedural_memory` 的处理和 `_create_procedural_memory()`；它调用 LLM 生成过程性记忆并生成 embedding。**纠错**：未在本地两份 Mem0 源码找到此前口头说的 `procedure_type` 字段，不以此命名引用。我们借鉴过程性记忆独立保存的思路，但车载 Skill 采用有来源的 Tool 步骤、结果校验与可选离线 LLM，普通操作不逐条调用 LLM。
- [LightMem `StructMem.md`](../../code/LightMem-main/StructMem.md)：描述 Event 级时间绑定、窗口缓冲与独立 Cross-Event 总结。我们沿用对话 Topic→Fact→Event→Cross 主链；结构化工具动作不强行过对话 Topic/Fact，而用 Trip/Episode 作为可追溯流程边界。
- [FluxMem `graph/edges.py`](../../code/LightMem-main/src/fluxmem/graph/edges.py)、[stage3_consolidation.py](../../code/LightMem-main/src/fluxmem/stages/stage3_consolidation.py)：Episode→Procedural 的蒸馏边与技能归纳/验证。我们借鉴证据边和流程候选，但 V1 在线只做确定性聚合，不自动执行或让 LLM 自改工具链。
- [memU README](../../code/memU/README.md)：其 agent 将历史工作蒸馏为可复用 Skill，决定新建/修订并索引；我们借鉴版本与复用，区别是车机动作必须经上游 Tool Registry/Policy 校验，不能直接使用任意 Markdown Skill 作为命令。
- [MIRIX 过程记忆管理](../../code/MIRIX/mirix/services/procedural_memory_manager.py)：过程条目含 summary、steps、entry_type 的检索/管理。我们借鉴独立 Procedure 视图，另增 Trip 锚点、身份归属和逐步授权。
- [TiMEM 画像提示](../../code/TiMEM/config/prompts.yaml)：历史周报与旧画像参与高层画像迭代。我们保留现有开放 Profile 批次蒸馏，同时新增结构化条件 Preference，不让自由文本画像作为车控参数真值。
- [Memos PostgreSQL schema](../../code/memos/store/migration/postgres/LATEST.sql)：Memo 的创建/更新时间与 tags 组织是 Tag/时间检索的启发；不能把 Memos 的协作笔记 Schema 宣称为我们的车载条件模型。
- DesayMem 既有 [对话模型](../src/desaymem_light/domain/models.py)、[Profile 迁移](../migrations/postgres/005_profile.sql) 与 [JSON Mirror 投影](../src/desaymem_light/adapters/json_mirror/file_projector.py)：复用对话链、作用域、证据和 Outbox 思路。Operation/Trip/Episode/Preference/Skill、可信结果和权限分离是**我们的车载改造**，本地尚无新入口/迁移。以上链接均指本机参考副本，不保证上游仓库未来版本不变。

## 2. V1 实施范围

保留已部署 `/v1/messages`、`/v1/memories/search` 的行为；新结构化模块走可插拔 Adapter/Repository，不在现有对话 LLM 流程中增加逐操作调用。V1 拟新增 `/v1/operations`、`/v1/trips` 和 `/v1/vehicle-memory/search`。名称与字段为本项目规范化契约，不要求所有上游原生照此发送。一个 PostgreSQL 数据库，逻辑独立表；每个表对应同名 JSON Mirror 文件。Qwen3-32B 用于既有 Topic Fact、窗口 Cross、批次 Profile 和必要时的模糊 QueryPlanner；BGE-M3 不用于每条结构化操作，只有可选模糊检索/按版本 Skill 摘要。

V1 **不做**：实际控车指令发送、自动权限裁决、通知调度、自由文本 Skill 直接执行、Hermes 在线自进化、全量遥测/GPS 存储、为每类工具定制表/算法。复杂多步 Skill 可做 `candidate` 与证据回放；自动发布/执行默认关闭。

## 3. 统一信任契约

信任不是一个 `trusted=true` 字段。四项分别核验并存来源：

1. **调用方**：网关或服务间认证确定 tenant 与来源服务；body 里的 tenant/user/vehicle 只是待核验声明。来源无权访问该 tenant/vehicle 则拒绝，不能降格保存为他人历史。
2. **人车归属**：`requester_user_id`、`driver_user_id`、`occupant_user_id`、`vehicle_id` 分开。个人 Skill 只取可证明的行为人证据；未知行为人仅可在授权范围内做车辆级历史，不能推给驾驶员/车主。手机远控请求者不等于驾驶员。
3. **动作结果**：Registry 按工具版本声明参数 Schema、结果 Schema、允许的结果来源和动作语义。`accepted/dispatched` 不是完成；车控可用可信 vehicle ack，媒体/导航/应用要用各自可信服务结果。只报告打开界面不能证明已播放/读取/发送。成功但没有实际参数时只证明动作完成，不证明请求参数已实际生效。
4. **条件**：天气、时区、乘员等每项有 value/source/observed_at；真正可信来自认证来源、地区/车辆匹配和新鲜度校验，不是字符串自称。历史条件用于解释当时，不可代替当前条件。无可信当前条件则返回未解析槽位/待确认。

错误处理：身份/跨租户/越权拒绝；未知工具或缺 Registry 暂受限存为 `unverified` 历史，不能喂给 Preference/Skill；Schema 不符拒绝或隔离并可审计；结果与请求车/工具不一致隔离冲突；超时为 `unknown`，不推断失败或成功。Policy 拒绝、用户拒绝、物理执行失败是三种不同反馈。记忆系统绝不重发物理指令。

## 4. 规范化输入与幂等

`command`：核心为 `requested` 与**终态** `result` 两阶段，同一 `operation_id`；平台若另报 `accepted/dispatched`，只作可选 `progress` 事件，不占用最终 `result` 的幂等槽。请求至少含 tenant、来源、请求者（可信时）、目标 vehicle/device、tool_name、schema_version、args、requested_at；终态至少含同作用域、operation_id、result_status、result_source、result_at，完成时间/actual_result 可选。上游可附 source_event_id、interaction_id、trip_id、context。结果先到暂存，关联前不对外显示成功。同终态阶段相同 payload hash 幂等，同键不同内容隔离对账，不能按到达顺序覆盖；未来修订需显式 revision/source_event_id，V1 不猜。`progress` 可按稳定 source_event_id 幂等并限量保存，绝不计成功。

`observed_action`：上游仅可信地报告已发生的工具动作时，只需稳定 `(tenant_id, source, source_event_id)`、行为人归属、目标、tool_name/schema_version、actual_args、occurred_at、结果来源。它没有虚构 `requested`，也不走命令超时状态机。未给稳定来源事件 ID 时不能安全去重/计 Skill；可隔离，不能用近似文本或时间凑 ID。普通环境遥测不走此入口。

`trip`：稳定 trip_id、tenant/vehicle/source、started_at/status；可信驾驶员可选，结束时 ended_at 与目的地标签可选。开始/完成幂等合成同一行；终态冲突对账。无可信驾驶员只保留车辆级。未经上游确认的“公司/家”标签不可由模型猜。

所有时间须带时区；时间顺序异常保留原值并隔离，允许配置小幅设备时钟偏差。所有 JSON 参数、上下文、回执和来源引用有限长与隐私字段白名单，不保存完整邮件正文/歌词/原始 GPS 轨迹。

## 5. 存储与证据：最小分层

- 现有 `memory_items` 继续 Fact/Event/Cross；现有 `profile_items/profile_snapshots` 继续开放画像，不改其强制向量/文本契约。
- 新原始阶段事件表：规范化请求、结果、观察上报及内容摘要/来源/受理状态；保留期限可配置。新 `activity_operations` 保存一个动作当前状态与原始事件引用。若最终迁移仍叫 `operation_events`，须另给 Trip 版本审计；不能丢掉对账所需来源。具体 DDL 在编码前冻结。
- 新 `trip_events` 保存 Trip 当前态；新 `activity_episodes` 保存归并边界/归属/置信原因；`episode_operations` 以 ID 关联步骤、顺序、行为人，避免复制完整操作负载。可信 trip_id 优先，无 trip 时受限时间窗仅形成待核验 Episode。
- 双轨中的新 `conditional_preferences` 与类型化证据边保存 attribute/value_json/conditions/scope/source_kind/status/family/version/validity。`observed` 可描述习惯；显式且校验通过的 `active` 可供匹配建议；条件冲突/未知不强选。旧自然语言 Snapshot 可批次概述，不反写结构化真值。
- 新 `skill_items` 与 `skill_evidence` 保存步骤模板、参数槽位、条件、来源 Episode/Operation、支持/反例、family/version/status。单步或多步 `candidate` 不等于 `published`；published 不等于执行授权。歌曲等值在槽位解析，不因换歌重建整个流程。
- 通用表按 tenant/actor/vehicle/time/tool/status/trip 建索引；精确检索不用向量。新增表迁移时在现有 `json_mirror_registry` 注册主键/触发器。数据库事务内写事实+Outbox；MirrorWorker 异步投影。API 成功不等于镜像即时完成；必须实现真正的数据库↔文件全量 Reconciler，因为目前 `file_projector.reconcile()` 是零计数占位。软删/硬删、崩溃重放、乱序和多 Worker 都要验。

### 拟议表键与最小列（DDL 编写依据）

- `activity_source_events`：`id` 主键，tenant、source、source_event_id（可空，仅 command 核心阶段无上游事件 ID 时）、dedup_key、payload_hash、kind/phase、受限 normalized_payload_json、received_at、disposition。`dedup_key` 对 command 的 requested/终态 result 为 operation_id+phase，对可选 progress 为 source+source_event_id，对 observed_action 为 source+source_event_id；唯一的**已受理**阶段按 tenant+dedup_key 限制，冲突 payload_hash 另留 disposition=conflict 的审计事件，不覆盖已受理行。原始大型回执截断/白名单化并记录 raw_event_ref，不在 DB/镜像重复敏感原文。
- `activity_operations`：`id` 主键，tenant、operation_kind、source、operation_id 或 source_event_id、requester/actor/driver/vehicle（可分别为空）、tool_name/schema_version、requested_args_json/actual_args_json、requested/occurred/completed/result 时间、status、validation_status、request_event_id/result_event_id/observed_event_id、context_json、updated_at。command 在 tenant+operation_id 唯一；observed_action 在 tenant+source+source_event_id 唯一。阶段事件外键指回原始表；请求/结果工具与 scope 匹配才更新聚合态。
- `trip_events`：`id` 主键，tenant+trip_id 唯一，vehicle、driver_user_id 可空、source、started_at/ended_at、status、可信地点标签和 context、version、updated_at。仅按稳定 trip_id 合并；错误终态不上覆盖，冲突走审计。
- `activity_episodes`：`id` 主键，tenant、user_id 可空、vehicle、trip_id 可空、start/end、boundary_kind、link_reason、status/version。`episode_operations`：episode_id+operation_id 主键、ordinal、actor_attribution、inclusion_reason；一个 Operation 可在车辆时间轴与个人视图中关联，但个人 Skill 计数只读取归属可信的边。
- `conditional_preferences`：`id` 主键，tenant/user/vehicle 可空、family_id+version、attribute、value_json、conditions_json、source_kind、status、valid_from/to、updated_at。`conditional_preference_evidence`：`id` 主键，preference_id、互斥的 message_id/fact_id/operation_id、evidence_role/observed_at；数据库 CHECK 保证恰有一个来源，尽可能用外键，不使用无类型 source_id。active/observed 与冲突值允许并存，唯一约束只约束同 family/version，不能按 attribute+scope 强制单值。
- `skill_items`：`id` 主键，tenant/user/vehicle 可空、family_id+version、steps_json、slots_json、conditions_json、status、support_counts、created/updated_at。`skill_evidence`：skill_id、episode_id/operation_id/feedback_id 中恰一来源、evidence_role、created_at；版本切换只更改当前指针/状态，不删旧证据。每表都注册 Outbox 与同名 JSON Mirror。

上述列是**最小逻辑 DDL 草案**；SQLite 若仅用于本地测试需与 PostgreSQL 契约相同语义，云端以 PostgreSQL 为准。建表前须审核具体 CHECK/外键、软删策略、tenant 复合外键、防跨租户引用及 schema migration 顺序。尤其只凭单列 UUID 外键不能自动保证证据与 Skill 同租户，应用校验之外优先建立 tenant 复合约束。

### JSON Mirror 容量与顺序风险

当前投影器 `AtomicJsonTableProjector` 每处理一条 Outbox 事件，都会加载并**重写整份表级 JSON**。用户要求的一表一文件可用于受限云端测试，但在高并发/大表时会带来近似随表大小增长的单次 I/O、积压与额外磁盘空间；不能未经测量宣称适合生产规模。要记录单表行数/字节、投影吞吐、Outbox lag 与 95/99 分位更新时间，超界则在不改变表级逻辑一一对应的前提下设计批量投影/压缩快照或用户批准的分片方案。还需防止 DELETE 投影后迟到旧 UPDATE 把已删行重建，记录 tombstone/每键最高版本或严格顺序并做故障测试。

## 6. 检索与返回契约

新 `/v1/vehicle-memory/search` 输入认证身份/车辆、`purpose=answer|suggestion`、文本问题或明确属性/工具、当前时间及可信 context、可选历史时间范围。`answer` 可查询 Operation/Trip 原始历史、observed 习惯与当前偏好，必须标来源和条件；`suggestion` 只给适用条件下的 active Preference/published Skill，返回步骤/槽位/证据/版本/缺失条件，不返回可直接执行的命令。现有对话 search 可由上游 Agent 另行调用并合并，不破坏既有 API。

返回最少有 `status=matched|needs_clarification|no_match`、类型化 matches、evidence_ids、scope、occurred/valid 时间、条件匹配/缺失、conflicts、partial/degraded、usage、建议生成时刻/TTL。当前用户明确参数优先；多值同条件冲突或条件缺失时不猜。精确属性/时间过滤走 PostgreSQL，模糊语句才可用现有 BGE→Qwen Planner。上游每一步重新验证 Tool Registry、Policy/Permission 与车况；记忆只推荐。

## 7. 模拟回放与验收门槛

合成输入见 [样例说明](examples/vehicle_memory_v1/README.md)。已备 18 条车控事件：9 个命令请求、9 次结果投递（其中一次是相同结果重投，故只有 8 个不同结果）；预期 9 个逻辑操作＝7 可信成功、1 dispatch failed、1 pending→unknown。先到的 result 不提前曝光，重复结果不加支持。5 次 Trip 上报归并 4 趟，未知驾驶员那趟不进个人通勤。三日派生 Episode 中导航/音乐各 3 次、邮件 1 次、乘客音乐 1 次被排除；天气/歌曲相关次数是观察，不自动激活偏好。

新增 [输入契约边界样例](examples/vehicle_memory_v1/contract_cases.json) 覆盖 command、observed_action、跨人/跨车、未知工具、仅平台受理、条件过期、重复与冲突。该文件也是设计测试向量，尚不是 API 测试运行结果。将来测试至少断言数据库态、证据边、JSON Mirror、answer/suggestion 的差异和不越权；再做并发重试、崩溃恢复、删除传播与一年的模拟记录容量测试。生产验收需要真实脱敏上游样例和网关/Policy/Registry 契约。

资源验收必须实际记录 Qwen/BGE 调用次数、输入/输出 token、每事件存储字节、镜像文件大小/Outbox 积压、p95/p99 延迟。目标：普通结构化事件与精确检索额外 Qwen/BGE 为 0；不承诺总流程为 0（现有对话抽取/批次画像仍调用 Qwen）。先给各表保留期/容量上限与日志隐私白名单，未测得数值不写“已达标”。还需验证当前 JSON 投影器删除后遇到迟到旧 UPDATE 时不会复活已删行；现有逐行版本逻辑在删除时移除行，单看代码无法保证这一顺序安全。

## 8. 可编码顺序与退出条件

P0：补现有主链已知缺口，尤其 Profile 事务/证据、作用域、Mirror Reconciler；新入口前落实身份链。P1：通用输入 Adapter、Registry 缓存、命令/观察幂等、Trip/Operation 表与镜像、历史 answer 检索。P2：Episode、条件 Preference、Skill candidate/证据、suggestion 检索；默认不自动发布。P3：真实上游映射、模拟/公开基线回放、成本容量与故障测试。每阶段保持 Protocol/Repository 可替换，模型/阈值/TTL/保留期做配置。

本轮只完成**设计与合成样例**，不声称 P0–P3 已实现。与整套对话记忆架构联审时，重点确认：对话显式偏好进入条件 Preference 的证据链、Cross/Profile 批次与结构化习惯的更新时机、跨入口检索去重、隐私删除和总体 Qwen/token 预算。
