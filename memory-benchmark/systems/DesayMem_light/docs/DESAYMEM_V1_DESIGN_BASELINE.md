# DesayMem Light V1 方案收口（评审草案）

> 2026-09-14。本文是后续实现的最小设计基线，不表示车控扩展已编码。已完成的集中收口见 [车载结构化记忆契约与验收基线](DESAYMEM_V1_CONTRACT_ACCEPTANCE.md)；逐轮推演见 [设计讨论记录](DESIGN_DISCUSSION_LOG.md)，图示见 [技术流程报告](DESAYMEM_TECHNICAL_FLOW_REPORT.html)。改动此基线须在讨论记录写明原因。下文早期暂名与第 27–28 节不一致时，以集中收口文档和后续节为准。

## 1. 一句话定位

同一用户/车辆身份边界下，并行接收**自然语言对话**与**结构化车控/行程事件**；把真实发生的事、当前条件偏好、可复用操作步骤分开存储和检索。记忆系统提供事实、参数候选和证据，不管理车辆执行权限，也不直接发车控指令。

## 2. 参考与我们的改造

- LightMem：借鉴 Session/Topic 的异步压缩；我们保留未沉淀对话的即时召回。仅对话进入 Topic，车控 JSON 不强制伪装成对话。
- Mem0：借鉴对话 Fact 抽取与历史关联；`action/state/explicit_preference/personal_fact` 是 DesayMem 新增的 Fact 分类。结构化车控不需每条再调用 Qwen 抽 Fact。
- StructMem：借鉴 Event/Cbuf/历史 Sk 的跨事件归纳；我们显式存 Topic Event，车控 Operation/Trip Event 可与对话 Event 通过证据关联但不受 Topic 边界切断。
- 既有 Tag/时间设计、TiMEM、AWS User Preference：借鉴标签时间索引、画像蒸馏与偏好合并；我们采用开放 Profile Snapshot + 结构化条件 Preference 双轨，并保留 user/vehicle scope、有效期与证据。注意本地 `code/memos` 是 usememos/memos，不能将其直接称作 MemOS 来源。
- MIRIX、memU、FluxMem：借鉴独立 Procedure/Skill、旧 Skill 更新、Episode→Skill 来源边与回放；我们只发布受 Tool Registry 校验的步骤 JSON，不直接执行自由文本 Skill。
- Hermes Agent/Self-Evolution：借鉴离线候选改进、评测和审核；第一版仅预留插件，不把它放入在线车控请求链。

## 3. 双输入与最小契约

### 对话入口：当前代码已有

`POST /v1/messages` 接收 `request_id/message_id/scope/sequence_no/role/content/occurred_at/metadata`，沿 Session→Topic→Fact→Event→Cross-Event→Profile 既有流水线处理。当前对话搜索入口为 `POST /v1/memories/search`。现有 Profile/检索行为不能视为本草案的双轨条件偏好已实现。

### 结构化入口：第一版拟新增

- `POST /v1/vehicle-operations`（路径为拟定）：采用两个受限事件类型，`phase=requested` 与 `phase=result`。同一次车控两阶段共享**必填** `operation_id`；上游若有稳定的单次上报 `event_id` 可附加，但不强迫上游新增。`request_id/trace_id` 只做链路追踪。请求至少有 `tenant_id/user_id/vehicle_id/source/tool_name/args/requested_at`；结果至少有 `tenant_id/vehicle_id/operation_id/result_status/result_at`，车辆确有完成回执时另带 `vehicle_completed_at`；成功可附 `actual_result`，失败可附 `failure_stage/error_code`。`occupant_id/interaction_id/trip_id/context` 可选且仅在可信时填入。
- 示例请求：`{"operation_id":"op1","phase":"requested","tenant_id":"T1","user_id":"U1","vehicle_id":"V1","source":"mobile_app","tool_name":"set_climate","args":{"temperature_c":22},"requested_at":"2026-01-12T07:30:00+08:00"}`。示例回执：`{"operation_id":"op1","phase":"result","tenant_id":"T1","vehicle_id":"V1","result_status":"success","actual_result":{"temperature_c":22},"result_at":"2026-01-12T07:30:03+08:00","vehicle_completed_at":"2026-01-12T07:30:03+08:00"}`。
- 服务端只信任已认证调用方与经过授权校验的 scope，不能仅凭 JSON 中的 `user_id` 声称操作者身份。入库时校验带时区时间、请求/回执租户车辆一致性和负载边界；已注册工具再校验参数 Schema，未知工具先按下文 `unverified` 受限保存；不要求车端回执自行携带或猜测 user_id。默认一个 `operation_id` 只有一条逻辑请求和一个最终回执，按 `(tenant_id, operation_id, phase)` 与内容摘要幂等；若上游支持多次状态修订，应另提供版本/事件 ID。单 `operation_id` 最终汇成一条 `vehicle_operations` 当前行并留下状态变更审计。同阶段重复内容无副作用，内容冲突不按到达顺序覆盖，需对账。回执先到可暂存为未关联状态，匹配请求前不对外展示为“已成功”；无回执超时为 `unknown`，迟到回执可更正并审计。失败/unknown 不计入成功 Skill 证据。
- **发送失败兜底**：上游应在尝试发送车控指令前创建 `operation_id`。若指令根本未送达车端，回传 `result_status=failed, failure_stage=dispatch` 和 `result_at`；这不是“车辆执行失败”，也没有 `vehicle_completed_at`。若已送达但执行失败，标 `failure_stage=execution`，可附车端失败时间；无最终回执维持 `unknown`。若是“向记忆服务上报失败”，上游需持久化待上报事件（Outbox）并按相同 operation_id/phase 重试；记忆 API 幂等接收并支持对账补报。**记忆系统不因上报失败自动重发物理车控指令**，也不伪造车辆完成时间。
- 行程入口拟为 `POST /v1/trips`（路径待上游确认），一次行程按 `trip_id` 幂等更新同一行。最小字段：`trip_id/tenant_id/vehicle_id/source/started_at/status`；`ended_at` 仅在已结束时提供，`driver_user_id` 仅在可信识别驾驶员时提供。可信且获准保留时，可附 `origin_label/destination_label` 和 `context`。不要求上游提供对话 Topic、乘员名单或完整 GPS 轨迹。若没有稳定 trip_id，上游可在开始时使用服务端返回的 ID，或者提供已有行程/记录 ID；仅凭“时间相近”不能安全合并开始和结束两条记录。
- 行程示例：`{"trip_id":"trip-1","tenant_id":"T1","vehicle_id":"V1","driver_user_id":"U1","source":"vehicle_trip_service","started_at":"2026-01-12T07:20:00+08:00","ended_at":"2026-01-12T07:53:00+08:00","status":"completed","destination_label":"公司"}`。地点标签只按可信上游原样保存，不从经纬度或常去地点推断“家/公司”。没有可信 driver_user_id 时只保留车辆级行程，不纳入任何个人通勤 Profile；没有 destination_label 时可回答出发/结束时间，但不能编造目的地。
- 允许 `in_progress → completed/aborted` 的填充更新；无结束回执经过可配置时间可标 `unknown`。重复相同上报无副作用、完成后的旧状态不能倒退、互相矛盾的终态或时间需审计/对账。仅可信归属且已完成的行程进入个人通勤统计；每趟不调用 Qwen 或 BGE。具体用户可见范围、地点隐私和保留期由部署方/上游确认。
- 两路均需认证与作用域校验。上游若无 `topic_id` 或 `interaction_id`，单条 Operation Event 仍可入库和检索；后续通过重复序列而非邻近时间本身发现 Skill。`trip_id=null` 支持手机远程控车。

### 条件上下文：第一版拟采用通用带来源字段

- `context` 为可扩展的 JSON 对象；可能包括天气、当地日期/工作日、季节、车辆状态和当前乘员，但**不要求每次请求都带齐**。每个可用于车控参数匹配的条件必须有 `value/source/observed_at`，并由上游 Context Registry 或字段定义校验类型与有效期；未经验证的扩展键仍可保留为历史附加信息，但不得参与可执行 Skill 匹配。
- `requested_at/result_at/started_at` 等事件时间必须带时区；另可提供 IANA `timezone` 标识以处理跨时区与夏令时。工作日/周末可在时区确定后由代码计算；季节需要可信地区/定义，天气来自有时间戳的上游服务，乘员与驾驶员来自已认证的身份系统。没有可靠来源就保持 unknown，不让 Qwen 补造。
- `scope` 至少隔离 tenant/user/vehicle；用户身份不明的车辆事件不能写入个人偏好。条件缺失不妨碍保存真实 Operation/Trip，但特定条件 Preference/Skill 不应被当作当前可执行匹配。上下文不触发每条事件的额外 LLM 调用。

## 4. 写入分层和存储

1. **历史事实**：对话 Fact/Event/Cross-Event 保持现有 PostgreSQL 表；车控新增通用 `vehicle_operations`，行程新增 `trip_events`。每条车控请求与最终回执对齐为一条操作记录，可保留状态变更审计和 `validation_status`。通用字段是工具名、受约束参数、身份、时间、结果与可信上下文；空调/充电/泊车只是样例，不新建领域专表。
2. **当前偏好**：经第 20–21 节核对，V1 改为新增轻量 `conditional_preferences`，用 `attribute + value_json + conditions + scope + valid_from/to + evidence` 表达多值、条件和版本；现有 `profile_items/profile_snapshots` 保留开放自然语言画像，不把每条车控偏好强制向量化。操作频率、常用车辆和通勤时段先作为有样本/来源的观察，不直接当成用户明确偏好或授权。首版可按需计算统计，不必为每种习惯造表。
3. **Skill**：拟新增 `skill_items`（每个版本一行，含 family_id、intent、summary、steps_json、conditions、scope、status/version）和 `skill_evidence`（连到来源操作/行程/纠错）。`observed` 是统计，不是 Skill；多次独立成功形成 `candidate`，验证审核后 `published`，失败或纠错可 `suspended`。三次独立成功只是测试初值，不是固定真理。
4. **独立检索、共用物理库**：上述新增表放在同一个 PostgreSQL schema/数据库中，各有 Repository、索引、JSON Mirror 表级镜像。未来隔离或吞吐确有要求再拆物理库，第一版不引入跨库事务。

## 5. 检索和执行边界

- **Tool Registry 归属**：上游车控平台是可执行工具的唯一定义方，向记忆系统提供版本化只读目录：`tool_name/schema_version/args_schema`，可选 `result_schema/capability`。记忆侧本地缓存目录以验证参数与 Skill 步骤，不在每次上报时远程询问 Registry；记忆侧自己的 Attribute Registry 只负责把已验证操作参数映射为可查询的偏好属性，不创造新车控能力。
- **未知工具兜底**：若 Registry 暂不可用或新工具尚未同步，已认证的原始操作可作为受限历史事件持久化并标 `validation_status=unverified`，留待目录同步后重验；它不进入当前 Preference/Skill 蒸馏，也不作为可执行 Skill 步骤。这样既不丢真实历史，也不让未受控工具流入执行链。字段大小、来源和隐私约束仍要校验。
- **Permission 归属**：上游权限/安全策略服务持有按用户、车辆、工具能力、条件和有效期的授权、确认与撤销。记忆系统只返回匹配的偏好/Skill 及来源，不生成 `allow` 判定；上游执行前实时决定 `allow/confirm/deny` 并把决策 ID/版本与 `operation_id` 一起留审计。记忆入库不依赖每条操作回查权限服务；历史成功也不能恢复被撤销的授权。
- 历史问题按身份、车辆、时间、trip_id、tool_name 精确查 Operation/Trip；对话问题走现有分层搜索，必要时关联结构化证据。
- Preference 按属性、scope、当前条件和有效期读取；同条件冲突返回歧义与证据，条件缺失不猜。本轮用户明确参数优先于历史偏好。
- Skill 仅检索 `published`，先按 intent/scope/conditions 精确筛，再按需用 BGE 短摘要做模糊定位；返回步骤、参数来源、证据、版本和缺失信息，不直接发工具调用。自然语言模糊请求复用现有 Planner，不新增每次必跑 Qwen。
- 上游 Policy/Permission 是自动执行授权和撤销的唯一事实源，实时核验身份、车况、工具能力、确认要求。`published` 只表示可推荐，不表示可自动执行。主动服务、通知和免打扰偏好可查询，但推送/调度留上游。

## 6. 资源、生命周期和验收

- 普通结构化 Operation/Trip 入库、条件检索和确定性 Skill 序列归并目标为 **0 次额外 Qwen**；对话维持 Topic 级 Fact 调用、窗口级 Cross-Event 和批次 Profile 调用。Hermes 离线成本单列。向量主要用于模糊搜索，精确车控不依赖向量 Top-K。
- 纠错只使对应 scope/conditions 的当前 Preference 失效/换版；历史真实操作不篡改。授权撤销上游立即生效；隐私删除按证据反向索引停止召回并处理派生项、JSON Mirror 和缓存。版本化 Skill 发布保留旧版可回滚。
- 验收最小回放：请求/回执/超时，跨人跨车，条件共存/冲突，失败与撤销，Skill 来源与回滚，历史时间查询，删除一致性；单独报告工具参数正确率、错误执行/漏确认、Qwen/BGE 成本、p95 延迟和每用户存储量。本地 VehicleMemBench QA 为 Git LFS 指针，取得金标前不能声称通过官方评测。

## 7. 实施阶段与未决项

1. **P0 修复现有主链**：Profile 事务生命周期、Evidence 覆盖、user-global/vehicle-specific 作用域、Checkpoint/JSON Mirror 一致性等已知问题；补回归测试。
2. **P1 结构化事实**：Operation 请求/回执状态机、Trip 输入、PostgreSQL 表/索引/JSON Mirror、历史检索；先不生成 Skill。
3. **P2 当前态与流程**：条件 Preference、统计观察、Skill candidate/发布、专用检索、证据与版本回滚；所有模块通过 Protocol/Repository 可插拔。
4. **P3 评测与可选进化**：真实 VehicleMemBench/一年模拟数据回放；拿到同事 Hermes 代码后再决定 Evolver 插件接入，默认不进入 V1 在线链。

**实施前还需明确**：真实上游的操作/行程字段是否匹配草案、可信 context 的来源与时区、Tool Registry 目录交付方式及刷新周期、Attribute Registry 首批映射、Skill 测试阈值、上游权限/撤销 API、历史与备份保留期。这些是配置/接口确认项，不应由模型暗自假设。

## 8. 本地代码与真实上游的核对结果

- 当前仓库 `src/desaymem_light/api/app.py` 只有 `/v1/messages`、`/v1/memories/search` 和健康检查；`api/models.py` 的 `MessageIn` 是对话契约。仓库内未发现车控请求/回执、行程、Tool Registry 或 Permission 的真实上游 payload/客户端。本文所列 JSON 是**拟议规范化格式**，不是已验证的上游格式。
- 当前 FastAPI 入口未看到应用层认证及“认证调用方与 body.scope 一致”的校验；云端网关是否承担这一职责，需部署方提供配置或接口说明才能确认。在身份链未落实前，不应开放新的车控/行程写入或跨人检索入口。
- 可插拔上游 Adapter 应把真实来源字段映射为本基线的 Operation/Trip DTO，保留 `source/schema_version/raw_event_ref` 便于追溯；没有稳定操作/行程 ID、最终状态或可信身份的字段，不能靠 LLM 补造。Adapter 不改变通用存储与 Skill 算法。
- 需要上游提供一组已脱敏真实样例：车控请求、成功回执、发送失败/执行失败、行程开始/完成，以及 Tool Registry 字段和网关/Permission 的身份契约。取得后逐项标注“可直接映射 / 需要 Adapter / 缺字段”，再冻结接口和数据库迁移。

## 9. 自建模拟数据与后续对齐

- 已先制作[合成接口样例](examples/vehicle_memory_v1/README.md)：对话、模拟 Tool Registry、车控请求与回执、行程开始与完成。覆盖成功、发送失败、无回执超时、回执先到、重复回执、驾驶员未知等情况。它是讨论与未来回放的输入夹具，不是已实现接口，也不是数据库 JSON Mirror。
- 上游可按此契约适配；若真实系统字段不同，则在 Adapter 做规范化映射，并保留原始事件引用。收到真实脱敏样例后逐字段对照，再冻结 DTO、迁移和评测预期。

## 10. 车控操作状态机：下一轮讨论草案

- 将**上报接收状态**与**车辆执行状态**分开：记忆 API 收到 `requested` 只证明上报成功，不证明指令已发送；收到 `result=success` 也需标明是车端确认还是上游推断。拟增加可选 `result_source`/`result_confidence`（字段名待上游对齐）。只有可信终态回执才能作为成功操作与 Skill 证据。
- 最小状态：`unmatched_result`（回执先到）／`pending`（请求已见）→ `success`、`failed_dispatch`、`failed_execution`；`pending` 到期暂记 `unknown`，迟到的可信回执可以转终态并留审计。`unknown` 不是失败。重复相同事件幂等，矛盾终态进入 `conflict` 待对账，不按接收顺序决定真相。
- 取消/用户撤回若上游能提供可信终态，作为 `cancelled` 保存，不计成功；仅有“请求取消”而无车端结果时仍是 `unknown`。若一次请求包含多个动作，首版要求拆成多个 `operation_id`；否则部分成功无法准确归因，不进入 Skill 证据。
- 同一属性连续相反操作（如 22°C 随后改 24°C）保留两条历史，不当作重试或覆盖；偏好蒸馏要结合纠错/短时撤销证据。`operation_id` 必须跨上报重试稳定，真实车控重试若重新执行应有新的执行标识，不能与记忆上报重试混淆。
- 状态超时、结果可信来源和终态冲突对账策略均做可配置项。这里仍是设计草案，尚未实现；下一步需决定 `result=success` 的可信证据具体由谁提供。

### 成功证据的最小契约（讨论草案）

- 区分三件事：①记忆服务**收到上报**（HTTP 接收）；②车控平台**已发送/受理命令**；③**车端确认完成**。①②都不能转成 `success`。首版 `result_status=success` 专指③；若上游只能提供②，规范化为 `accepted`/`pending`，待终态或超时后为 `unknown`，不得用于 Skill 成功计数。
- 在已有 `result_at`、`vehicle_completed_at` 之外，拟增加 `result_source`（例如 `vehicle_ack`、`platform_inferred`）和 `upstream_result_id`（若有）。只有经过身份链验证、可追溯到对应 `operation_id` 的 `vehicle_ack`，且结果字段与 Registry 的 result_schema 一致，才进入可信成功；`platform_inferred` 可存历史但标 `unverified`。字段名和来源枚举由真实上游样例最终确定，不让模型判断成功与否。
- `actual_result` 表示车端回传的实际参数，不能直接复制请求 `args` 冒充反馈。若上游只给“执行完成”而没有实际值，记录成功但把实际值标为 unknown；可以说明用户请求过 22°C，不能回答“车端确认最终温度为 22°C”。对于参数类 Preference/Skill，应额外要求实际值已验证，或者明确其证据只是请求值，不能混作实际执行值。
- 必须校验 tenant、vehicle、operation_id 对齐；收到矛盾终态、车端时间早于请求时间、跨车回执或缺少可信来源时隔离对账。可配置小幅时钟偏差容忍，但不改写原始时间。此契约先用于我们自己的模拟与测试，上游真实 payload 到来后以 Adapter 映射。

## 11. 条件偏好如何从对话和操作产生（讨论草案）

- **显式偏好**：用户对话“冬天工作日上车前，我希望空调 22°C、座椅加热 2 档”经现有 Topic→Fact 提取后，可分别形成 `climate.temperature_c=22`、`seat_heating.level=2` 的候选偏好，证据连到原始 message/fact；条件为用户明确说出的冬天、工作日、上车前，scope 为该用户与当时车辆。是否推广到用户所有车要另有证据/用户确认，不能默认扩大。
- **观察到的操作习惯**：三天车端确认的空调 22°C 可增加该条件下的使用统计，证据是各自 operation_id 和可信回执；它支持但不替代显式偏好。座椅操作虽然三天成功，但其请求没有可信季节/时区 context，不能从邻近空调操作继承冬天条件。单次 23°C 操作不应全局覆盖 22°C；保留时间、条件和可能纠错关系，只有明确纠正或足够的新证据才更新当前态。
- **写入结构**：一条偏好对应 `attribute/value_json/conditions/scope/source_kind/evidence_ids/valid_from/valid_to/status`。`source_kind` 至少区分 `explicit_statement` 与 `observed_operation`；不要把后者改写成“用户说过”。多个值可并存，条件重叠而证据冲突时输出歧义，不强选一个。样例：`{"attribute":"climate.temperature_c","value_json":22,"conditions":{"season":"winter","day_type":"weekday","phase":"before_boarding"},"scope":{"user_id":"user-demo","vehicle_id":"vehicle-demo"},"source_kind":"explicit_statement","evidence_ids":["msg-20260112-1"],"status":"candidate"}`。这是逻辑 DTO 示例，尚非数据库表行/JSON Mirror。
- **资源边界**：显式偏好复用 Topic 级 Fact 抽取，不因每次车控再调 Qwen；结构化操作的统计、证据链接和条件筛选确定性处理。开放 Profile 仍由既定批次蒸馏更新，不随每条操作更新；当前条件偏好单独可检索。阈值与冲突裁决保留配置和人工评测，不在 V1 硬编码成某一车控属性规则。

## 12. 偏好生效、纠正与换版（讨论草案）

- 最小状态分三层：`observed`（从操作统计得到，只回答“常这样做”）、`candidate`（提取到偏好但身份/指向/条件或证据待核验）、`active`（明确表达且证据与作用域通过校验，可用于偏好回答与推荐）。这不是执行许可；上游 Policy 仍决定确认/执行。显式偏好经 Topic→Fact 后可直接成为 `active`，不必等待三次重复操作；沉淀前最近对话仍靠 Session/Last-K 即时召回。单条操作即使成功也只进 `observed`。
- 纠正例：“以后冬天工作日上车前改成 24°C”不是改写旧事实，而是新增 `24°C` 版本，旧 `22°C` 在**重叠作用域和条件**上设 `valid_to`/`superseded`，保留原文和证据。若新话只针对“今天”或“这辆车”，只缩小到相应时间/车辆，其他条件下 22°C 保留。若纠正内容的指向不清，则 `candidate` 并在检索时返回歧义，不做覆盖。
- 一次临时操作与旧偏好不同，只是 `observed` 的反例，不能自动视为纠正；重复反例可使旧偏好标记“可能过时/待确认”，但 V1 不自动删除显式偏好。用户明确撤销时停止当前检索，历史保留到隐私删除请求另行处理。
- `active` 的含义是“可在相关条件下作为当前偏好候选返回”；检索时仍需核对用户/车辆、条件有效期、证据、冲突与本轮明确要求。条件缺失时不能把条件偏好当成无条件偏好。条件重叠且证据无法排序时返回多个值和来源，请 Agent 询问，不让系统猜。
- 数据层建议追加版本行与来源证据，当前状态可在同一 PostgreSQL 事务内更新，并让表级 JSON Mirror 同步；不可只改画像自然语言摘要。Profile Snapshot 在后续批次由有效偏好重建/蒸馏，不作为偏好真值源。可配置参数只包括观察样本门槛、过时提醒窗口、冲突容忍与画像批次，不用属性专属规则。

## 13. 条件偏好检索与控车返回（讨论草案）

- 输入至少含经认证的 `tenant_id/user_id`、可选可信 `vehicle_id`、问题文本或上游已识别的 `attribute/tool_name`、请求时刻与可验证 `context`。检索输出分 `answer_evidence`（用于回答）与 `action_suggestion`（供上游规划/确认），二者不能混同。问“我喜欢多少度？”可展示有条件的多个 active 值和观察记录；问“帮我开空调”只能返回当前条件下匹配、参数经 Registry 校验的建议，绝不由记忆服务发车控指令。
- 检索顺序：认证作用域过滤 → 属性/工具定位（已有 QueryPlanner，简单明确输入走直接属性查询；模糊语句可用可选 BGE/不确定时 Qwen）→ `status=active`、有效期和条件过滤 → 证据与冲突检查 → 组装最小返回。车控精确查询不必做 pgvector 搜索，也不必每次调 Qwen。普通历史操作问题查 Operation/Trip 原始记录，不让 Profile 替代事实。
- 条件只由可信当前上下文满足；例如模拟数据有冬天/工作日/上车前的 22°C 显式偏好：若当前为夏天、车辆不符，或“上车前”状态未知，则不能给出“现在自动设 22°C”的确定建议。问过去偏好时可以返回原文并说明适用条件。工作日可在可靠时区下计算；季节/上车前需可信来源，不从日期或附近消息强行猜。
- 优先级只采用可解释的最小规则：本轮用户明确给的参数覆盖历史建议；然后取当前条件匹配且更具体的有效显式偏好；若多个候选同样适用且值矛盾、或必需条件未知，就返回冲突/缺失条件，交由 Agent 追问。`observed` 只作为“你常设为…”的提示，不自动替代 active；没有 active 时可给观察结果但不能把它称为用户偏好或执行默认值。
- 返回建议至少包含 `attribute/value/conditions/scope/preference_version/evidence_ids/source_kind/matched_context/missing_context/conflict`，并让上游重新核验身份、车辆状态、Tool Registry、Policy/Permission 与确认要求。推荐结果可过期，需带生成时刻与短 TTL；上游执行时重新检查，不把缓存建议当授权。
- 示例：用户问“我冬天工作日上车前习惯把空调设几度？”→“你曾明确说希望设为 22°C，适用于冬天工作日上车前；另有三次模拟车端成功记录。”用户说“现在把空调打开”但未给车/季节/上车前状态→返回 `missing_context` 或请上游确认，不能静默下发 22°C。上述是预期行为，不是当前 API 已实现能力。

## 14. 操作到 Skill 候选：首版从单步开始（讨论草案）

- **来源对比**：本地 Mem0 实际是 `memory_type="procedural_memory"`（未找到 `procedure_type` 字段）；memU 的 Skill、MIRIX 的 Procedure、FluxMem 的 Skill 提供“过程性记忆可独立保存/召回”的参考。我们面向车控增加 Tool Registry 参数验证、可信完成回执、上游 Permission 边界和 operation_id 证据链。Hermes 的自进化只作为后续离线候选验证插件，不进入 V1 在线生成/执行链；不能把我们的状态机说成原项目已有。
- **候选形成**：只看身份归属可信、Registry 已验证、车端终态成功、实际参数可核对、没有短时撤销/纠错的独立操作。按 `user/vehicle/tool/normalized_args/可信条件` 聚合，独立性优先按不同日期/行程/交互 ID 去重；同一次上报重试或同日连点不增加次数。达到可配置 `min_independent_successes`（模拟测试暂取 3）只能形成 `candidate`，不是 `published`，更不是自动执行许可。
- **首版边界**：一个 `operation_id` 对应一个单步候选，如“设置空调到 22°C”；空调和座椅仅一次相近出现，不能凭时间近就生成两步 Skill。多步优先用可信 `interaction_id`/`trip_id`，缺失时允许受限时间窗形成待核验 Episode；须跨独立 Episode 重复共现并保留证据，详见下方多步小节。这样既允许发现真实流程，也避免一次偶然邻近就误造 Skill。
- **证据与条件**：候选 Skill 保存规范化步骤、适用 scope/conditions、证据 operation_id 列表、成功/失败/取消统计、Registry 版本和状态。条件只取各次操作共同拥有且可信的上下文，不能从邻近操作复制。当前模拟样例的空调三次带 winter/timezone，可作为冬天条件的候选证据；座椅三次缺 context，只能形成无季节断言的观察/候选。`trip_id` 缺失不妨碍单步候选，但不能据此断言“通勤前”。
- **发布**：候选经 Schema、证据、冲突和回放验证，可由配置/审核流程升为 `published`，首版建议默认需要人工或上游确认；发布是“可被推荐”，不是“可自动执行”。新失败/撤销/Registry 变更可挂起候选或已发布版本。检索时只向上游提供匹配步骤与证据，上游实时授权/确认后才调用实际工具。
- **成本**：结构化聚合与候选更新走确定性代码和数据库索引，普通车控事件目标 0 次额外 Qwen/BGE；不把每次操作写成自然语言再嵌入向量。只为后续模糊 Skill 搜索对短摘要按版本嵌入一次，且可关闭。

### 多步通勤 Skill：由 Episode 汇成流程候选（修正前述过严边界）

- `interaction_id` 不是唯一关联方式。有可信 `trip_id` 时，以同一用户/车辆的单趟行程为 Episode；出发前短窗口可并入该趟行程，但必须有可信身份/车辆归属与可解释时间边界。无 trip_id 时可按连续操作时间窗形成**待核验 Episode**，不能仅凭邻近时间直接发布多步 Skill。Episode 是我们新加的归并层，独立于现有对话 Topic/Event；来源参考 MIRIX 的 Episode→Procedure、memU/FluxMem 的过程性 Skill，车载 trip 与结果证据是我们的改造。
- 对每趟 Episode 只保留有可信结果的动作序列，例如 `07:30 导航到公司 → 07:31 播放周杰伦歌单 → 07:32 打开邮件界面`。同用户跨不同工作日重复出现相似流程，按工具语义、相对顺序和条件聚合为多步 `candidate`；允许某天缺少非必需步骤，但统计每一步的支持次数和共同出现次数。不要把一次相邻操作认作习惯，也不要把失败/unknown 计入成功支持。
- Skill 保存**参数模板与证据**，不保存死板脚本：导航目标可为经可信来源确认的“常去公司”标签，音乐参数可为具体歌单/艺人或“最近常播”槽位（仅在有对应证据时），邮件只表示打开界面而非读取/发送邮件。每步保留 tool_name、args 模板、前置条件、证据 operation_id、可选/必需标记；如果上游未提供实际音乐曲目或导航目的地，保持未知，不让 LLM 补造。
- 多步候选可用于回答“我早上通勤常做什么”或推荐一键流程；发布前仍须 Registry/权限/隐私审查。导航、播放音乐、打开邮件可能属于不同能力和风险级别；记忆系统只返回候选步骤，**每一步**由上游 Policy 实时决定可执行、需确认或禁止。尤其打开邮件界面不等于获准读取邮件内容或发送邮件。
- V1 先实现可追溯 Episode 与候选统计，不做自由文本自动规划或自进化执行。`episode_gap_minutes`、出发前窗口、`min_independent_episodes`、步骤共现阈值、顺序容忍度都是超参数，经真实车载数据调参。若缺可信 trip/identity 或多次重复证据，退化为若干单步历史/候选而非强造多步 Skill。

### Episode 边界与音乐可变参数（继续讨论）

- **边界优先级**：可信 `trip_id + driver_user_id + vehicle_id` 是最强 Episode 锚点；允许把同一身份/车辆在出发前 `pre_trip_window_minutes` 内的操作关联进来，行程结束后默认停止。若没有 trip_id，用同身份/车辆的连续操作与 `episode_gap_minutes` 形成待核验片段；出现身份/车辆切换、明显长空闲或新行程即断开。身份不明时只能形成车辆级原始片段，不进入个人 Skill。所有关联保留 `link_reason` 与来源 ID，时间窗只提供候选关系，不证明共同意图。
- **稳定流程与可变槽位分离**：通勤 Skill 可以保存步骤模板 `navigate(destination_slot) → play_music(music_slot) → open_mail_ui`，而不是写死某首歌。`destination_slot` 可由可信行程/导航历史给出；`music_slot` 在检索时由独立的条件音乐 Preference/近期播放证据解析。Skill 只表示“此阶段常播放音乐”及步骤顺序，不自带“任何天气都播放周杰伦”的结论。
- **参数解析**：先用本轮用户明确要求；否则按当前用户/车辆、可信天气/季节/时间/工作日等上下文查询适用的音乐偏好或观察记录。多值可以按条件共存（如雨天偏好轻音乐、晴天偏好摇滚），但样例不能凭空预设这种映射：必须来自用户表达或多次可信播放历史。无匹配、条件未知或候选冲突时返回 `unresolved_slot` 与备选/证据，请上游确认或由上游音乐服务按其默认策略处理；记忆系统不暗选歌曲。
- **操作证据要求**：播放事件需有内容标识与可追溯来源（如 `track_id/playlist_id/artist_id` 及目录版本）；“打开音乐应用”只证明打开应用，不证明播放了哪首歌。上游只有文字曲名时先保留原始值及匹配状态，不由 Qwen 猜到曲库 ID。按隐私/授权范围保存听歌历史，不把同车其他人的播放归到驾驶员画像。
- **跨能力结果来源**：前述 `vehicle_ack` 只适用于车控能力；导航、音乐、邮件界面应分别以导航/媒体/应用服务的可信完成事件作证据，Registry 中按工具定义允许的 `result_source` 和结果 Schema。不能要求所有步骤都有车端 ECU 回执，也不能把“已打开应用”升级为“已读取邮件/播放了歌曲”。统一的是“来源经认证、结果可追溯、语义与步骤一致”。
- **数据示意（非表行）**：`{"steps":[{"tool":"navigate","args":{"destination":"${destination_slot}"}},{"tool":"play_music","args":{"content_id":"${music_slot}"}},{"tool":"open_mail_ui","args":{}}],"slots":{"music_slot":{"resolver":"conditional_music_preference","fallback":"unresolved"}},"status":"candidate"}`。模板中 `${...}` 只供记忆检索返回，不能被当成真实工具参数直接执行；上游解析、逐步校验与授权后才调用工具。
- **资源控制**：Episode 构造和参数槽位解析优先确定性索引查询，无每次操作 Qwen/BGE；音乐模糊表达才交由现有 Planner/可选语义路由。上下文条件和槽位解析需要可配置、可观测，不能硬编码“下雨必放某歌”。

## 15. 多步候选的稳定性与可选步骤（讨论草案）

- 一趟 Episode 只给每种实际完成的步骤贡献一次支持，不因连点、重试或多条播放回执虚增次数；一个用户跨不同日期/行程的 Episode 才算独立样本。先按工具能力和经过 Registry 规范化的语义动作对齐，再看相对顺序；歌曲/歌单等槽位值不同不拆散 `play_music` 这个流程步骤，但具体内容另由条件偏好解析。
- 以候选流程为单位记录 `episode_count`、每步 `step_support_count`、步骤对 `cooccurrence_count`、顺序一致性、失败/取消/反例及证据 Episode/operation_id。超过配置的 `min_independent_episodes` 和 `min_step_support_ratio` 才列为稳定步骤；低于稳定阈值但反复出现的可标 `optional`。测试值先由模拟回放决定，不在代码中写死“导航必须有、邮件必须有”。
- 例如 5 次可信工作日通勤：导航 5 次、播放音乐 4 次、打开邮件界面 2 次。首版可提出“导航→音乐”的主候选，并把邮件列为低支持的观察/可选建议，**不能**说用户每次都会打开邮件。若只有 2 次 Episode，则暂不发布稳定多步流程。数字只是演示，不代表已选定阈值。
- 因为音乐内容随环境变化，统计“通勤时播放音乐”与“何时播放哪个内容”是两项独立证据：前者支持流程步骤，后者支持条件 Preference/slot resolver。没有可信天气的 Episode 可支持“播放音乐”步骤，但不能支持“雨天播放某歌”的映射。
- 流程相似却顺序反转或步骤互斥时，不强拼成一条固定顺序 Skill；可拆成不同条件候选，或只保留无序习惯统计。V1 不让 Qwen 为少量样本自动讲出因果解释。Candidate 输出需包含样本量、支持率、条件覆盖和未解决槽位，供审核/上游确认。
- 借鉴 MIRIX 的 Episode/Procedure 和 memU/FluxMem 的 Skill 组织；跨 Episode 统计、可选步骤、条件槽位以及逐步授权是我们为车载场景定义的保守候选算法，尚待真实数据评测。

## 16. 通勤 Skill 的检索、主动建议与执行边界（讨论草案）

- **用户主动调用**：上游传入已认证用户/车辆、当前意图和可信 context，记忆检索匹配的 `published` 通勤 Skill，逐步解析 destination/music 等槽位，返回步骤、证据、缺失槽位、每步所需能力和建议有效期。用户说“开始通勤，播放我今天想听的摇滚”时，本轮明确音乐要求优先于历史音乐槽位。
- **主动服务**：由上游的场景/调度服务判断是否在合适时机请求推荐；记忆系统不自行监听车辆并发起控车或推送。即使检测到上车、工作日早晨和常用车辆，也只返回“可建议的通勤流程”；是否弹出提示先查上游通知/免打扰策略。用户曾拒绝此类建议时，应作为负反馈抑制或挂起推荐，不靠时间频率覆盖拒绝。
- **逐步执行**：上游对每步实时查询 Tool Registry/Policy、核实车辆状态与用户身份，得到 `allow/confirm/deny` 后才调用工具。`published` 不是 `allow`。某步未授权或失败时只执行已获准且语义独立的步骤；依赖步骤停止并返回部分完成状态，不自动重试敏感动作。邮件界面只能按其能力授权，读取/发送邮件属于另一个独立步骤与权限。
- **三种返回**：`matched`（条件与槽位齐全，仍需上游授权）、`needs_clarification`（歌曲、目的地或条件冲突/缺失）、`no_match`（未发布/身份条件不符/明确拒绝）。返回须携带 skill version、证据 ID、每步参数来源与安全状态，不传一句不可解释的“我觉得该这样做”。历史习惯问答可检索 candidate/observed 作描述，但不能交给执行链。
- **成本与版本**：精确场景先按 scope、意图、条件和状态索引查，不需每次 Qwen/BGE；模糊自然语言仍复用 QueryPlanner。上游执行时重新检验过期建议；用户纠正、工具 Schema 改版或权限变化时挂起/重验 Skill 版本。主动通知策略由上游持有，记忆只保存有证据的偏好/反馈。

## 17. 通勤 Skill 更新：新旧习惯与负反馈（讨论草案）

- 每个已发布 Skill 保留 `family_id + version`。新 Episode 继续记录为证据和统计，不直接改写已发布步骤；当步骤支持率、顺序或槽位条件发生持续变化，生成新 `candidate version` 与旧版比较，经验证后切换当前版。旧版保留证据和有效期以回答“以前怎么做”，但不再作为当前建议。
- 用户明确说“通勤时不要再打开邮件”属于强负反馈：对相应用户/车辆/条件下的邮件步骤立即停止推荐或挂起，即使历史出现很多次；不等待统计阈值。说“今天别打开邮件”只作用于本次或明确的短时范围，不永久删除通勤流程。执行被上游 Policy 拒绝也不自动等价于用户永久厌恶；分别记录 `user_rejection`、`policy_denial`、`operation_failure`。
- 音乐曲目变化优先更新独立的条件音乐 Preference/slot 解析，不必每换一首歌就给通勤 Skill 整体换版。只有“通勤不听音乐了”或步骤位置/是否存在发生持续变化，才动流程版本。某日未播放音乐可能是设备故障、离线或用户没发起，不是自动撤销。
- 失败/unknown 不能计成功支持，也不能单凭一次失败删除流程。多次同类失败触发 `needs_review`/暂停该步骤推荐；故障恢复、用户显式纠正或新证据可形成新版本。版本变更必须反向链接原始 Message/Operation/Episode，支持解释、审计、删除传播和 PostgreSQL/JSON Mirror 一致更新。
- 借鉴 Mem0 的新旧记忆更新和 memU/MIRIX 的 Procedure/Skill 生命周期；我们的车载改造是区分强负反馈、短期例外、权限拒绝与设备故障，且把音乐内容变更留在条件偏好层，不让每个曲目造成整条 Skill 重建。上述仍为 V1 设计，阈值和暂停策略待模拟及真实回放校准。

## 18. 多用户归属：同车操作不能直接写成驾驶员习惯（讨论草案）

- 对每条事件分开记录 `requester_user_id`（谁发起）、`driver_user_id`（若可信识别）、`occupant_user_id`（若可信识别）与 `vehicle_id`；这些身份可缺失，也可能不同。现有车控草案的 `user_id` 应由 Adapter 明确映射为发起者，不能默认为驾驶员；上游若只有车/设备 ID，事件只进车辆级历史，不纳入个人 Preference/Skill。
- 车主用手机远程设空调：可归属发起者的控车历史，但不能推断其正驾驶、正通勤或在车内；没有可信 trip/driver 关联时不并入驾驶 Episode。乘客在车内播放音乐：可作为发起乘客的播放历史（按隐私授权），不能写成驾驶员的音乐偏好。共享车机账户且无可信个人身份时，宁可少个性化，也不跨人污染。
- 同一 Episode 的多步个人 Skill 只能使用归属相容的步骤。Trip 驾驶员已知不代表同趟所有媒体/邮件操作都是驾驶员发起；可把操作放在行程的**车辆时间轴**上，但个人 Skill 统计只取身份确认或上游明确授权的事件。身份冲突时拆成不同用户候选或标 `unattributed`，不靠时间邻近或 LLM 猜谁操作。
- 检索按认证请求者/可见范围过滤；用户问“我通勤听什么”不得把匿名/乘客播放记录混入。删除与撤销按身份及原始证据传播到 Episode、Preference、Skill、缓存和 JSON Mirror；共享车辆历史是否可见由上游权限策略确定，不能只靠 `vehicle_id` 放行。
- 借鉴现有 DesayMem 的 tenant/user/vehicle scope；明确区分发起者、驾驶员、乘员及车辆级事件是我们的车载多用户改造。实现前要与上游确认身份来源和可信等级，不能自行推断。

## 19. 三日通勤模拟推演

- 已补 [commute_skill_walkthrough.json](examples/vehicle_memory_v1/commute_skill_walkthrough.json)：三趟合成工作日行程、导航和媒体成功事件、一次打开邮件界面、一次乘客播放，以及候选输出。它是**派生算法推演**，不是当前 API 可接收的 payload 或 PostgreSQL JSON Mirror。
- 预期主候选是 `navigate(work_destination) → play_music(conditional_music_preference)`，两步各有三个独立 Episode 的支持；邮件只有一次，仍为观察，不当作稳定步骤。雨天两次同歌单、晴天一次另一歌单只构成条件观察，不足以自动宣布天气偏好；乘客播放被排除出驾驶员候选。
- 这组模拟的用途是先固定可核验的预期和缺失信息。真实上游字段到来后再把导航/媒体/应用结果映射到通用 Operation/Result 或独立 Adapter，验证工具目录、身份归属、天气来源和回执语义，不能为了让样例通过而假设上游一定提供这些字段。

## 20. 收口路线与第 1 步：最小持久化关系

余下按四步讨论：①表与证据关系；②输入 Adapter/状态机与幂等事务；③检索返回契约、授权边界和降级；④模拟回放/成本/隐私删除验收。第 1 步目前是逻辑草案，尚未写迁移。

- 现有 PostgreSQL `memory_items` 只支持 `fact/event/cross_event`，`memory_evidence` 仅指向 message/topic；不适合把结构化车控/行程硬塞进去。现有 `profile_items` 只有文本 `value`、必填 `vehicle_id/occupant_id`、必填 1024 维 embedding 和 `active/superseded/deleted` 状态，不直接满足条件 JSON、多源证据和零嵌入写入；需要单独迁移/兼容策略，不能假称已有。
- 拟新增最小关系：`operation_events` 保存每次原始规范化请求/回执（幂等键与来源）；`vehicle_operations` 保存一个 operation_id 的当前聚合状态；`trip_events` 保存每趟行程当前态；`activity_episodes` 保存 trip/时间窗的归并边界；`episode_operations` 以带角色/归属/顺序的关联边连接 Episode 与 Operation。原始事件和聚合态分开，才能对账乱序/重复与保留审计。
- 偏好层决定新增轻量 `conditional_preferences`，避免每条车控偏好强制向量化；它有来源边（message/fact/operation）与版本/条件，现有 `profile_items` 继续负责开放画像。Skill 层拟 `skill_items`（family/version/status/步骤模板）、`skill_evidence`（episode/operation/反馈）和必要的条件/槽位 JSON；不为每种车控能力建专表。
- 每张新增/修改的 PostgreSQL 表都要有**同名表级 JSON Mirror**；镜像的是实际数据库行，不是模拟接口文件。新增、修改、软删、证据边和版本切换需在同一受控写入流程中更新数据库与镜像并可对账；具体原子性/失败恢复方案留第 2 步确定。
- Episode 不复制完整 Operation payload，只保存边界、归属、聚合状态及连接 ID；Skill 不复制完整行程/歌曲历史，只保存步骤模板和证据引用。这样利于云端存储控制与删除传播。索引优先按 tenant/user/vehicle/time/status/tool/trip，只有需要模糊检索的短摘要再可选向量化。

## 21. 第 1 步决策：条件偏好单独建轻量表

- **为什么不扩展旧表**：`migrations/postgres/005_profile.sql` 中现有 `profile_items` 要求文本 value、1024 维 embedding、必填 vehicle/occupant、LLM model/prompt_version，证据表仅连 Event；这是为开放画像蒸馏设计的。直接塞入每条结构化车控偏好会触发无意义嵌入、扩大迁移与旧代码兼容面。故 V1 选择独立 `conditional_preferences`，不改现有 Profile 的读写契约。
- **最小列（逻辑草案）**：`id, tenant_id, user_id, vehicle_id NULL, occupant_id NULL, family_id, version, attribute, value_json, conditions_json, source_kind, status, valid_from, valid_to, created_at, updated_at`。`family_id/version` 保留旧新版本；一个属性允许多个并存值。`source_kind` 区分明确表达与操作观察，status 区分 observed/candidate/active/superseded/deleted；带条件的偏好不因缺失某条件自动推广为全局偏好。
- **证据边**：新增 `conditional_preference_evidence`，每条边指向确切的 session message、fact memory item 或 vehicle operation；建议用互斥的有类型外键列（而非不校验的任意 `source_id`），保存 evidence_role、时间和原始来源。`operation` 证据只有可信成功才增加成功支持；失败/纠错作为反证也要能关联。条件来自对话还是可信 context 必须可追溯。
- **与画像关系**：条件偏好是车机查询的当前结构化真值；现有 `profile_items/profile_snapshots` 继续保存开放画像。后续批次可把已验证且适合概述的偏好写进自然语言 Snapshot，但 Snapshot 不反向覆盖条件偏好，避免两套真值争夺。显式偏好仍复用 Topic 级 Fact 提取，不新增每条车控 Qwen 调用。
- **存储与镜像**：新表本身不存 embedding，JSONB 只保存规范化参数/条件，不重复整段对话或操作 payload；每个新表都有同名 JSON Mirror 文件。下一步再逐表确定主键/唯一索引、关联事务与镜像失败恢复。此为设计决策，尚未写迁移或代码。

## 22. 双轨与其他表：只为不同生命周期建表

- “双轨”指 **开放画像**（既有 `profile_items/profile_snapshots`，LLM 批次蒸馏、自然语言概述）和 **可验证条件偏好**（新 `conditional_preferences` + 证据边，结构化、精确查询、通常不嵌入）。不是两套互相复制的 Profile，也不再为每个空调/音乐/通知属性建表。开放画像可引用/概述有效条件偏好，但条件偏好不能由画像摘要反写生成而丢掉来源。
- 车控/媒体/导航等结构化动作共用通用 Operation 表，不按能力各建一表；请求/回执若需乱序幂等与对账，另有原始事件表。行程单独 `trip_events`（生命周期、驾驶员归属、时间/地点不同）；Episode 单独 `activity_episodes` + `episode_operations` 关联（一次流程可有多动作，不能塞成一个 operation JSON）；Skill 单独 `skill_items` + `skill_evidence`（候选/发布/版本/回滚不同于历史事件）。这些是逻辑边界，V1 不拆成多个物理数据库。
- Fact/Event/Cross-Event 继续复用 `memory_items`，Tag/时间和对话 Session 复用现有表；不新增“车控 Fact 表”“音乐画像表”“通勤统计表”。统计先按 Operation/Trip/Episode 查询或轻量聚合，确认性能瓶颈后才增加物化表。条件天气/季节作为已验证 JSON 字段，首版不单独建 context 表；Tool Registry 由上游维护，记忆侧首版读/缓存版本化目录，不复制成能力主库。
- 以 `memory_audit_events` 为例，现有审计要求非空 user/occupant 且操作类型面向记忆 ADD/UPDATE 等；不能直接替代匿名车控原始回执或完整物理结果流。新原始事件只存受限规范化负载、ID/来源/摘要/时间，并设置保留期；聚合态引用事件，避免无限重复存储。证据关联独立建表是为了可追溯与删除传播，而非为每种属性造表。

## 23. 第 2 步：写入事务、幂等与 JSON Mirror（讨论草案）

- 上游 Adapter 先验证认证身份/作用域、工具版本/参数、时区时间、事件大小，再形成规范化事件。`(tenant_id, operation_id, phase)` 是 V1 逻辑幂等键；有独立 `upstream_event_id` 时另留来源唯一约束。同键同内容摘要返回原接收结果，不增加 Episode/Skill 次数；同键不同内容不覆盖，标冲突并进入对账。若真实上游会对同一 phase 多次修订，需要显式 revision/event ID 后才能开放，首版不暗收。
- 单次 PostgreSQL 事务内写入受限原始 `operation_events`、更新 `vehicle_operations` 当前态和必要证据/审计；请求或回执先到都允许，结果先到只处于待关联，不能对外算成功。`pending` 超时转 `unknown` 由后台任务完成；迟到可信回执可转成最终态并留历史。失败、unknown、冲突、跨身份回执不进成功 Skill 统计。
- **镜像复用现有机制**：当前项目已有 `json_mirror_registry`、表触发器、`json_outbox`、MirrorWorker 和逐表 JSON 投影器。新表迁移时注册主键与触发器；数据库事务提交时 Outbox 一起提交，Worker 异步投影，失败重试。API 不应声称“响应时 JSON 已同步”，只承诺数据库真值与最终镜像一致；部署需监控 Outbox 延迟/失败。
- **现有缺口**：`AtomicJsonTableProjector.reconcile()` 目前仅返回零计数占位，尚不能完成数据库与文件全量对账/修复。V1 云端上线前要实现实际 Reconciler，并验证重启、乱序、重复、软删/硬删、投影失败与多 Worker 情况；不能仅靠 Outbox 重试就保证永久一一对应。跨文件系统与 PostgreSQL 不存在天然同一原子事务，故“镜像一致”应定义为有界延迟的最终一致并可修复，而非同步提交。
- 原始事件按合规保留期清理前须保证必要审计/证据可追溯；JSON Mirror 随数据库实际删除/修改同步，不把已删数据长期滞留。Outbox 与 JSON 文件自身的容量、延迟、失败次数做指标；具体保留期待部署方确认。此节仍是设计讨论，尚未实现新增入口或迁移。

## 24. 第 3 步：检索接口最小契约（讨论草案）

- 现有 `/v1/memories/search` 的 `SearchRequest/SearchResult` 只含对话 Fact/Event/Cross、短期消息和 Profile Snapshot；`QueryPlan.intent` 仅 general/profile/history/ranking，不能声称已覆盖 Operation/Trip/Preference/Skill。V1 建议先新增独立的车载检索入口（暂名 `/v1/vehicle-memory/search`），避免破坏已部署对话 API；上游 Agent 可合并两路结果，后续再评估统一 QueryPlan V2。
- 输入：`request_id`、经认证的 tenant/user、可选已授权 vehicle、`purpose=answer|suggestion`、`query` 或明确 `attribute/tool_name`、带时区的请求时间、可信 `context` 与可选 history 时间范围。回答历史问题走 Operation/Trip 原始事实，查当前设置走 conditional_preferences，查通勤流程走 published Skill；必要时还可调用现有对话 search。身份和车辆不能只信请求体字段。
- 输出统一用 `matches[]` 的有类型结果（`operation/trip/preference/skill`）及 `evidence_ids`、发生/有效时间、scope、条件匹配、来源可信度、版本；另有 `status=matched|needs_clarification|no_match`、`missing_context`、`conflicts`、`partial/degraded`、`usage`。`purpose=answer` 可包含 observed/candidate 并明确措辞；`purpose=suggestion` 只给当前有效且条件匹配的 active Preference/published Skill，不输出可直接执行的物理命令。
- 对 Skill 建议返回每步 `tool_name`、参数模板/已解析参数、参数来源、必需/可选、Registry 版本、证据和未解槽位；用户本轮明确要求优先。上游需重新校验身份/车况、Registry 与 Policy，并按步 `allow/confirm/deny`。建议带生成时刻和短 TTL，过期或上下文变化重新查。
- 资源顺序：精确字段与可信时间/Tag/条件优先走 PostgreSQL 索引，目标 0 次 Qwen/BGE；模糊自然语言复用现有可选 BGE/不确定升级 Qwen。统一预算限制 top_k、证据数、上下文 token 与延迟；无法确认条件时返回 clarification，而不是让 LLM 猜。V1 不把候选 Skill 注入可执行步骤。

## 25. 第 4 步：合成数据纸面回放与未闭环项

本节是**设计推演**，尚未运行新车载 API/数据库代码。当前样例有 18 条车控上报（9 request、9 result，其中 1 result 是重复投递），5 条行程上报对应 4 趟行程，另有 3 趟派生通勤 Episode。

- **Operation 预期**：9 个不同 operation_id → 7 个可信成功、1 个 dispatch 失败、1 个仅请求而 pending/超时后 unknown。先到的 `op-ac-out-of-order` 回执在请求到来前不对外显示成功，末尾相同回执重投不产生第 10 条操作或第 8 个独立成功。失败和 unknown 不计 Skill 成功证据。
- **Trip/身份预期**：`trip-0112` 两次上报只生成同一趟最终 completed；`trip-0114-unattributed` 无驾驶员，只进车辆级历史。另三趟可信驾驶员行程可供模拟通勤 Episode 锚定。乘客 `passenger-music-0114` 虽发生在驾驶行程内，不能计入驾驶员音乐偏好或流程支持。
- **Preference/Skill 预期**：对话中的冬天工作日空调 22°C/座椅 2 档要等 Topic→Fact 与作用域校验后才可成为显式 active 偏好，沉淀前靠 Session/Last-K；车控三日成功只产生观察支持。通勤派生样例中导航/播放音乐各有 3 个独立 Episode 的候选支持；邮件仅 1 次为观察；天气→歌单只有雨天 2 次、晴天 1 次，不够宣称条件音乐偏好。Candidate 不进执行链。
- **刻意未闭环**：后续已给 `commute_skill_walkthrough.json` 的天气/时区/工作日补上模拟来源和观测时间，但这不等于部署方已经认证该来源；其中导航/媒体/邮件仍是派生推演而非可回放的拟议 Operation 请求/回执。上游真实身份链、Tool Registry、Policy、结果 Schema/回执 ID 和镜像 Reconciler 均未实现或未验证，不能声称端到端通过。现有会话 Topic 触发也需真实回放确保对话偏好已沉淀。
- **待编码验收**：逐条重放并断言数据库行数/状态/证据边、跨人跨车隔离、同键冲突、迟到回执、JSON Mirror 与数据库逐表一致、删除传播、检索 answer/suggestion 区分、Skill 不越权。记录每事件 Qwen/BGE 调用、token、p95 延迟、每用户/车存储增量和 Outbox 延迟；目标不是凭纸面估算宣称资源达标。之后再用真实脱敏上游数据和 VehicleMemBench/一年模拟对话验证。

## 26. 模拟环境条件修补后的边界

- 通勤推演现把 `weather/day_type/timezone` 表达成 `value/source/observed_at`，用于设计校验与回放；来源名全部是模拟值。真实请求须由认证上下文服务提供或由服务端在可靠时区计算，并检查观测时间是否足够新、位置/车辆是否适用、字段类型是否符合 Context Registry。单纯在 JSON 中填 `source` 不构成可信证明。
- 过去 Episode 的天气是历史证据；给“现在播放什么”解析 music_slot 时必须另取**当前**可信天气，不能拿上次通勤的天气代替。缺少当前天气时仍可返回“通勤时常播放音乐”这个流程，但音乐内容 unresolved 或由上游明确指定；历史雨天两次/晴天一次不足以建立 active 条件音乐偏好。
- 仍未补导航/媒体/邮件的真实请求/结果上报契约；在现有假数据基础上只能验证归并算法逻辑。下一轮优先讨论是否扩展通用 Operation DTO 覆盖这些应用能力，以及各自的可信回执语义，不把 `vehicle_ack` 用在媒体/导航上。

## 27. 泛化修正：例子不是功能穷举

- 导航、音乐、邮件、空调仅是**测试样例**，不作为算法内置 if/else，也不需要穷举车机所有能力。V1 的统一对象应是 `operation_id + actor/scope + tool_name/capability + schema_version + args + context + requested_at + result/status/source`；Tool Registry 提供每个工具的参数/结果 Schema、可信结果来源与能力标签。记忆引擎只做通用验证、归属、时间归并、证据统计、条件匹配和步骤模板，不知道“雨天应该听什么歌”。
- 前文 `vehicle_operations`/`/v1/vehicle-operations` 是先讨论物理控车时的暂名；纳入导航、媒体、应用后，**建议规范化为通用 `activity_operations` 和拟议 `/v1/operations`**，`operation_events` 保留原始阶段事件，`trip_events` 仍单独。上游仍可保留各领域原接口，由可插拔 Adapter 映射到同一规范化 DTO。此处是对前文命名的修正，不代表代码已改名或新 API 已存在。
- 可变参数不是为歌曲特制：Skill 步骤中的参数可按 Registry/Attribute 映射标成常量、来自本轮用户、来自条件 Preference、来自可信上下文或未解槽位。新增工具时更新外部 Registry 与必要的参数映射配置，而非改 Episode/Skill 核心算法；若新工具缺 Schema、可信回执或参数映射，先受限保存为 `unverified` 历史，不参与可推荐/可执行 Skill。
- 不能做到“完全零配置泛化”：每个可执行工具的参数类型、结果语义、权限和安全等级必须由上游声明。泛化是**算法与存储结构稳定、扩能力靠注册/配置**，不是让 LLM 自行发明全部工具定义。是否有条件偏好提取价值，可由通用属性映射或上游标注决定，不为每个功能造表/代码分支。
- 来源对比：Mem0 提供通用记忆操作，memU/MIRIX/FluxMem 提供过程性记忆/Skill 思路；Tool Registry、车载可信结果/权限隔离、通用 Operation+Episode 槽位是我们的工程约束。示例数据必须显著标注“只检验机制，不代表能力清单”。

## 28. 通用 Operation 输入契约：命令与已发生动作

- V1 规范化对象只覆盖**可识别的工具/能力动作**，不把任意传感器遥测、环境状态、对话文本都塞进 Operation。统一必备身份域、车辆/设备域、`tool_name + schema_version`、动作参数、带时区的时间、可信来源和稳定 ID；具体参数/结果形状由 Tool Registry 声明，不在核心代码枚举导航/音乐/空调等功能。
- **命令型 `command`**：上游有明确发起和终态，继续用同一 `operation_id` 的 `requested`/`result` 两阶段。`requested` 含 actor、target、args、requested_at；`result` 含 result_status、result_source、result_at、可选 actual_result/完成时间。仅平台 accepted 不等于终态成功。
- **已发生动作 `observed_action`**：若上游只可信地报告“用户确实完成了某动作”（如在媒体服务中成功播放），单次 `observed` 事件带稳定 `source_event_id`、可信 actor/target、tool_name、actual_args、occurred_at、result_source，不伪造 requested。它可作为历史事实；要进入个人 Skill 支持，还必须通过身份、Schema、动作语义和结果可信度校验。只有“应用被打开”就只能记打开应用，不能推导播放/读取内容。无稳定事件 ID 时先隔离或受限历史，不能靠内容+时间猜成幂等唯一动作。
- 两种输入由各上游 Adapter 规范化后进入同一 `activity_operations` 当前事实层，保留 `operation_kind`、事件来源和原始引用；幂等键分别为命令的 `(tenant_id, operation_id, phase)` 与已发生动作的 `(tenant_id, source, source_event_id)`。命令乱序/超时状态机不强加给 `observed_action`。来源无法验证时标 unverified，既不算成功 Skill，也不变成当前偏好。
- Registry 每个工具至少声明 `tool_name/schema_version/args_schema/result_schema_or_observation_schema/allowed_result_sources/capability`，以及步骤参数中哪些字段能形成可变槽位/偏好属性的**可选映射**。权限安全等级由上游 Policy 定义；Registry 的 Schema 不是自动执行授权。未知工具受限存历史，注册并重验后才能进入候选。Registry 缓存与版本匹配，不要求每条上报远程查询。
- 这修正了前文“所有操作都必须有 requested/result”的过窄假设，也避免对媒体/应用日志伪造请求。上游真实字段到来后由 Adapter 映射；V1 不采集海量原始遥测，相关状态只作为有来源的 context。此节是待讨论契约，尚未实现。

## 29. 四张逐步流程图的阅读入口

[HTML 技术流程报告](DESAYMEM_TECHNICAL_FLOW_REPORT.html)现在在总图之前增加了四张独立子图，可按以下顺序评审：

- **对话 Add**：完整 user/assistant Turn → Session 即时可用 → Topic 触发 → Topic 级 Qwen Fact → Mem0 风格历史 Fact 对齐 → Event 确定性构建 → 窗口 Cross-Event → 批次 Profile。明确标注 Fact/Topic/Event/Cross/Profile 的落库与 BGE/Qwen 调用点；四类 Fact 标签是我们的车载抽取设计，不是 Mem0 原始类别。
- **对话 Search**：认证 scope/时间/Tag → QueryPlanner → Session、Fact、Event/Cross、Profile 并行读取 → 证据去重与 Token 装箱 → SearchResult。现有对话 SearchResult 不含结构化 Trip/Skill，Agent 需按需调用车机检索入口。
- **车机操作 Add**：command 请求/终态、observed_action 或 Trip → Adapter → 身份/Registry/Context 校验 → 原始来源与聚合态同事务 → Outbox/JSON Mirror → Trip/Episode → 条件偏好与 Skill 候选/审核。橙色节点是拟新增；普通结构化事件目标为零额外 Qwen/BGE。
- **车机操作 Search**：`answer` 从 Operation/Trip 和观察记录回答历史，`suggestion` 仅从有效 active Preference 与 published Skill 给参数/步骤候选；统一检查条件、证据、槽位与冲突，上游 Policy 再逐步确认和执行。此整链尚未在当前代码实现。

这些图用于**讨论数据路径与模块边界**，不是每个箭头都已编码的证明。表字段、来源/我们的改动、模拟验收详见 [V1 契约与验收基线](DESAYMEM_V1_CONTRACT_ACCEPTANCE.md)。
