# LLM 调用复杂度分析

> 配套文档：[architecture-analysis.md](architecture-analysis.md)（整体架构 / 表设计 / 检索链路）；[DesayMem_mem0_LLM调用降本改进方案.md](DesayMem_mem0_LLM调用降本改进方案.md)（架构级降本路线，与本文优化项的对照见 §4.5）
> 本文聚焦：每次 LLM 调用做了什么、传了什么、prompt 为什么这么写，以及系统的调用复杂度与可优化点。
> 代码基线：`src/desaymem`，所有行号以当前 main 为准。

---

## 一、调用全景

整个系统只有 **4 个 LLM 调用点**（全部走 `OpenAICompatibleLLM.complete`，`providers/llm/openai_compatible.py:45`），其余全是确定性 Python 计算：

| # | 调用点 | 时机 | Prompt | 角色 |
|---|---|---|---|---|
| 1 | `MemoryExtractor.extract`（`extraction/extractor.py:78`） | `add` Phase 2 | `ADDITIVE_EXTRACTION_PROMPT` | L1 事实提取 |
| 2 | `EpisodeBuilder.judge`（`layers/episodes.py:50`） | `add` L2 | `EPISODE_SYSTEM_PROMPT` | L2 情景边界判断 |
| 3 | `ProfileDistiller.distill`（`layers/distiller.py:104`） | `add` L3 | `DISTILL_SYSTEM_PROMPT` | L3 信念蒸馏提议 |
| 4 | `SemanticReranker.select`（`layers/reranker.py:60`） | `search` Step 9 | `RERANK_SYSTEM_PROMPT` | 检索重排 |

**一次 `add` = 3 次串行 LLM 调用**（extraction → episode → distill）；**一次 `search` = 1 次 LLM 调用**（rerank）+ 1 次 embedding 调用。

另有 3 个**已定义但默认不挂载**的 prompt：`PROCEDURAL_MEMORY_SYSTEM_PROMPT`（程序性记忆，仅在 `memory_type=procedural_memory` 路径使用）、`AGENT_CONTEXT_SUFFIX`（无调用点）、`VEHICLE_CUSTOM_INSTRUCTIONS`（需显式传 `custom_instructions` 才注入提取器）。

### 统一调用参数

所有调用共用同一组采样参数（`config.py:34-36`，可环境变量覆盖）：

```
temperature=0.1   top_p=0.1   max_tokens=2000
response_format={"type": "json_object"}
```

- `temperature=0.1 / top_p=0.1`：四个任务全部是**结构化抽取/判断**，不是创作——确定性越高越好，幻觉越少越好
- `response_format=json_object`：强制 JSON 输出，Python 侧 `parse_object` 再做兜底解析
- `max_tokens=2000`：统一上限（后面会讲这是 extraction 的隐患）

---

## 二、逐个调用详解

### 调用 1：`ADDITIVE_EXTRACTION_PROMPT` — L1 事实提取

**做什么**：读一轮对话消息，输出自包含的事实陈述列表（ADD-only）。

**传参**（system + user 两条消息）：

- **system** = `ADDITIVE_EXTRACTION_PROMPT`（`extraction/prompts.py:23`，约 480 行）。这是四个 prompt 中唯一从 Mem0 OSS 平台版迁移来的，体积最大，内容分：ROLE / 语言保持 / INPUTS 说明 / GUIDELINES（提取什么、质量标准）/ Integrity Rules（防幻觉、防回声、防细节污染）/ Memory Linking / 12 个 few-shot 示例 / 输出前 checklist / OUTPUT FORMAT
- **user** = `generate_additive_extraction_prompt`（`extraction/prompts.py:579`）拼装的 7 段：

| 段 | 内容 | 来源 | 为什么传 |
|---|---|---|---|
| `## Summary` | L3 narrative 快照 | `profiles.get_snapshot`（Phase 0） | 画像反馈：帮 LLM 知道已确立的偏好，写更贴合上下文的事实 |
| `## Last k Messages` | 最近 10 条历史消息（截断 300 字/条） | SQLite session store | 消解新消息里的代词和省略主语 |
| `## Recently Extracted Memories` | 本会话近期已提取内容 | 调用方传入 | 会话内去重 |
| `## Existing Memories` | 向量检索的 top-10 相似旧记忆（**假整数 id**） | `retriever.neighbors_for_extraction`（Phase 1） | 跨会话去重 + `linked_memory_ids` 链接目标 |
| `## New Messages` | 本轮对话原文 | 调用输入 | 提取对象 |
| `## Observation Date` | 对话发生日 | metadata/当前日期 | **唯一时间锚**——"昨天/上周"全部相对它解析，写成绝对日期 |
| `## Current Date` | 系统当天 | `datetime.now` | 明确告知"不要用它解析相对时间"，防混淆 |

**输出**：`{"memory": [{"id","text","attributed_to","linked_memory_ids"}]}`

**为什么这么写**（设计意图）：

1. **ADD-only 是刻意裁剪**：上游 Mem0 还有 UPDATE/DELETE prompt（LLM 直接改库）。DesayMem 砍掉它们，更新职责交给 L3 supersede 机制——LLM 幻觉改写已有记忆的风险被结构性消除
2. **假整数 id 防幻觉**（extractor.py:60-67）：LLM 引用旧记忆时最容易编造 UUID，传整数 "0".."9"，Python 持有 fake→real 映射回填——与 distill/rerank 的 fake id 方案一脉相承
3. **Observation Date vs Current Date 分离**：对话回放/评测场景下两者可能差数年，prompt 里用大写强调 + few-shot（Example 7）固化行为
4. **防回声规则**：assistant 消息里的确认性复述不提取，但 assistant 提供的新信息（推荐、计划）要提取——车载场景车机回复大量是"已为您调到22度"，这条规则直接决定库会不会被车机话术污染
5. **"宁可冗余不可漏提"**：因为下游有三道去重防线（LLM 参考列表 → hash 去重 → DB UNIQUE 约束），提取端可以放宽召回

**失败行为**：`raise LLMError`——**唯一不降级的调用点**。提取是 add 主流程的必经路径，失败必须让调用方看见。

---

### 调用 2：`EPISODE_SYSTEM_PROMPT` — L2 情景边界判断

**做什么**：判断新事实是延续当前活跃 episode（continues=true，顺便产出覆盖全量的新 summary）还是新开一个。

**传参**（user prompt 6 段，`layers/episodes.py:115-124`）：

| 段 | 内容 | 为什么传 |
|---|---|---|
| `## Current date` / `## Observation time` | 当前日 / 事件发生时刻 | 时间差是边界判断的核心信号 |
| `## Open episode` | 当前活跃 episode 的 `{id, summary, updated_at, source_memory_ids}`（无则 `null`） | 被延续/被关闭的对象 |
| `## Embedding similarity to open episode` | **Python 预计算的向量相似度**（新事实 mean_pool vs episode embedding 的余弦值，`episode_similarity`） | 把几何信号转成自然语言喂给 LLM——LLM 自己算不了向量，但能把这个数字和语义证据综合判断 |
| `## New facts` | 本轮 L1 事实 `[{id, text}]` | 判断对象 |
| `## Latest messages` | 本轮消息原文 | 事实之外的语篇信号（话题转折词等） |

**输出**：`{"continues": bool, "episode_summary": "...", "confidence": 0.0-1.0}`

**为什么这么写**：

1. **system prompt 只约 30 行**，与 extraction 的 480 行形成鲜明对比——边界判断是单一二元决策 + 摘要，任务复杂度决定了 prompt 不需要 few-shot 堆砌
2. **"Do not use keyword lists. Judge from meaning, time gap, and the written evidence only"**：不用"然后/另外"这种关键词路由，从语义判断——与系统全局的 domain-agnostic 原则一致
3. **禁止相对时间写进 summary**（明列 "上周/昨天/前几天" 禁词）：episode summary 会被存进 memory_items 被检索；如果里面写了"上周"，半年后检索到它就是错的。时间由 Python 以绝对时间戳另存，相对表达统一留给检索端 rerank 用 Current date 解析——**写端与读端的时间语义分工**
4. **summary 要求"self-contained, covering the whole episode"**：延续分支不是追加式摘要，而是每次重写全量 summary，保证 episode 语义完整可独立检索

**失败行为**：`return None` → 跳过 L2 更新，L1 已落库不受影响（旁路降级）。

---

### 调用 3：`DISTILL_SYSTEM_PROMPT` — L3 信念蒸馏提议

**做什么**：读一个 L1 事实簇（cluster），提议结构化 belief（SAV + stability + decision + evidence）。

**传参**——四个调用中 user prompt **最简**，只有一段（`distiller.py:411-416`）：

```
## Related facts
[{"id":"0","text":"空调设为22度","occurred_at":"2026-08-01T..."},
 {"id":"1","text":"上次空调设过24度","occurred_at":"2026-07-15T..."}, ...]
# Output:
```

cluster 构成（`memory.py:739-756`）：本次新 L1 事实 + 向量检索旧 L1 邻居（top_k=12，排除 episodic、排除重复）。id 仍是 fake 整数。

**输出**：

```json
{"beliefs": [{"subject","attribute","value","conditions",
              "stability":"episode|recurring|identity",
              "decision":"CREATE|CONFIRM|REFINE|COEXIST|SUPERSEDE|NOOP",
              "confidence", "evidence_ids":["id-from-input"]}]}
```

**为什么这么写**：

1. **"You do not write to any database"**（system prompt 第二段）：角色定义直接声明无写权限——LLM 只提议，Python `_apply_one` 是唯一写者。即使 LLM 输出完全乱掉，最坏结果是错误提议被两道防线（evidence_ids 校验、recurring 日期检查）拦截，库不会被污染
2. **user prompt 不传当前 belief 列表**：与 extraction 传 Existing Memories 不同，distill 不让 LLM 看已有 belief——匹配决策（要不要 CONFIRM/SUPERSEDE 某条已有 belief）由 Python 的 `attribute_embedding` 0.82 阈值完成，LLM 只决定"对这堆事实该提议什么"。职责切分：**语义合并交给向量，语义提议交给 LLM**，避免 LLM 拿不完整列表做出错误的"已存在所以跳过"判断
3. **decision 枚举在 system prompt 中定义，但执行权在 Python**（prompt 原文标注 "decision (Python will apply it)"）：六分支的实际落库行为（UPDATE 合并 / 关窗+INSERT / 并存 INSERT）由 `_apply_one` 硬编码，LLM 的 decision 只是建议
4. **硬规则三条**（evidence 只能引用输入 id / recurring 需两个不同时间证据 / 不得编造）与 Python 校验一一对应——prompt 约束 + 代码校验双保险

**失败行为**：`return 0` → 跳过 L3，L1/L2 不受影响（旁路降级）。

---

### 调用 4：`RERANK_SYSTEM_PROMPT` — 检索重排

**做什么**：从候选池（默认 32 条）中选出真正有助于回答查询的子集，有序返回。

**传参**（user prompt 4 段，`layers/reranker.py:52-58`）：

| 段 | 内容 | 为什么传 |
|---|---|---|
| `## Current date` | 查询当天 | 解析查询里的相对时间（"上周"） |
| `## Query` | 用户原话 | 任务目标 |
| `## Profile` | `{narrative, beliefs[]}`（含每条 stability/confidence/conditions） | 当前值判断依据（22度过期 vs 画像 26°C）；"我一般听什么"类问题的直接答案源 |
| `## Candidates` | 32 条 `{fake_id, layer, text, occurred_at, score}` | 重排对象；**带向量分**但不强制 LLM 遵守——LLM 可以推翻排序 |

**输出**：`{"selected_ids": [...], "time_scope": null | {"from","to"}}`

**为什么这么写**：

1. **检索全链路唯一调 LLM 的地方**：Step 1-8（lemmatize、BM25、实体 boost、融合打分、冲突过滤）全是确定性计算。LLM 只在最后做它不可替代的四件事：指代消解（"老规矩"）、时间窗推算、当前值 vs 历史值判断、画像优先路由
2. **候选带 `occurred_at` + Current date**：与调用 2 的"写端禁止相对时间"呼应——相对时间的解析权集中在这一处，由查询时刻的日期和候选的绝对时间戳共同决定，避免存档时的语义漂移
3. **`selected_ids` 要求"subset of the given candidate ids"**：Python 侧 `remap_ids` 会过滤一切不在映射内的 id——防幻觉选 id 的最后防线
4. **score 传进去但不进输出**：LLM 看得到向量分（作为参考信号），但输出只有 id 顺序——重排结果完全由 LLM 语义判断决定

**失败行为**：`return candidates[:top_k]` → 退回向量排序（旁路降级）。

---

## 三、复杂度分析

### 3.1 调用次数

| 操作 | LLM 调用 | Embedding 调用 | 备注 |
|---|---|---|---|
| `add`（infer=true，全开） | **3**（串行） | 2-3 批次 | Phase 3 批量 embed 提取文本；episode summary embed；每条 belief 候选 embed key（一次 API 多向量） |
| `add`（infer=true，episode/profile 任一关闭） | 2 / 1 | 递减 | `enable_episodes` / `enable_profile` 开关 |
| `add`（infer=false 原始写） | 0 | 1 | 仍走 L2/L3 演化 |
| `search` | **1**（rerank） | 1 | Step 1-8 无 LLM |

一次典型 add（三层全开）的**串行链路**：

```
Phase 0: 读历史消息 + profile snapshot          (DB)
Phase 1: 向量检索旧记忆 top-10                    (DB + embed? 否，直接用消息?)
Phase 2: LLM 提取                        ← LLM #1 (~5-15s)
Phase 3-5: 批量 embed + hash 去重                (embed API)
Phase 6: 批量落库 + 审计                          (DB)
Phase 7: 实体链接                                 (DB + embed)
L2: similarity 计算 + LLM 判定           ← LLM #2 (~2-5s)
L3: 邻居检索 + LLM 蒸馏                  ← LLM #3 (~3-8s)
     └─ 每条候选 belief embed key + _nearest 匹配 (embed API + DB ANN)
     └─ applied > 0 → refresh_snapshot 全量重建   (DB)
```

**三次 LLM 调用完全串行**：extraction 结果是 episode 的输入（new_facts），episode_id 是 distill 的输入（挂 evidence_episode_ids）。端到端延迟 ≈ 3 次 LLM 之和 + 若干 DB/embed 往返。以 GLM-4 级模型估算，单次 add 的 LLM 耗时约 10-30 秒——对车载"对话后写记忆"的异步场景可接受，对同步 API 不友好。

### 3.2 Token 量估算

| 调用 | system prompt | user prompt（典型） | 输出上限 |
|---|---|---|---|
| Extraction | **~5,000-6,000 tok**（480 行 + 12 示例） | ~1,500-3,000 tok（10 条历史 + 10 条旧记忆 + 本轮对话 + narrative） | 2,000（max_tokens） |
| Episode | ~300 tok | ~500-1,500 tok（episode summary + 新事实 + 消息） | 2,000 |
| Distill | ~400 tok | ~800-2,500 tok（最多 ~13 条事实簇） | 2,000 |
| Rerank | ~350 tok | ~2,000-4,000 tok（profile + 32 候选） | 2,000 |

单次 add 的 LLM token 总量约 **12,000-20,000**，其中 extraction 占大头（system prompt 每次**全量重发**）。

### 3.3 结构性复杂度评价

**好的方面**：
- 4 个调用点职责单一、边界清晰，每个 prompt 对应一个不可降级的语义判断，其余全部确定性计算——LLM 使用是"少而准"，不是到处撒
- 三层各自的 LLM 调用失败都独立降级（extraction 除外，它是主路径），单点故障不扩散
- fake-id 映射、JSON 强制、Python 校验三件套把 LLM 幻觉面压到最小
- system prompt 领域无关（明确 no keyword lists），换车舱/家居场景不用改 prompt

**复杂度风险**：
1. **串行 3 连调**是延迟瓶颈（见 4.1）
2. **extraction system prompt 每调用全量重发**，token 成本最高（见 4.2）
3. **max_tokens=2000 全局统一**：extraction 输出条数多时（prompt 鼓励"extract everything"，checklist 要求 10+ 消息提 5-15 条）可能被 2000 tok 截断，JSON 解析失败整轮 add 报错
4. **无调用级缓存与去重**：相同对话重复 add 会重花全部 3 次调用（只有 DB 层 hash 去重挡住落库，LLM 费用照付）
5. **distill 无预筛**：哪怕这轮对话全是无画像价值的琐事（闲聊、导航指令），只要 `enable_profile=true` 且有 stored，就会发起蒸馏调用——大多数调用可能返回 `{"beliefs": []}`
6. **rerank 候选序列化全部 32 条**进 prompt：candidates 里每条都全量带 text，token 随库增长（fetch_k 固定 32 还好，但 32 条中文长文本约 2-4k tok）

---

## 四、可优化点

按"收益/改动成本"排序，分四档。

### 4.1 延迟优化

**O1：episode 与 distill 局部并行**（改动小，收益 1 次 LLM 时延）

`_evolve_layers`（`memory.py:718-764`）里 L2、L3 串行。但 distill 对 episode_id 的依赖是**可选的**（仅用于给新 belief 挂 `evidence_episode_ids`）。cluster 构成只依赖 stored（L1），不依赖 episode 结果。可以：

```
L2 判定 与 L3 的 cluster 构建 + LLM 蒸馏提议 并行启动
    → L2 完成后拿到 episode_id，在 _apply_one 落库前注入
```

最坏情况（L2 慢于 L3 的 LLM 返回）：等待 episode_id 再落 belief；L2 失败则 episode_id=None，与现状一致。端到端从 3 次 LLM 串行压缩到 2 段。

**O2：episode 判定的几何短路**（省 1 次调用，改动小）

`episode_similarity` 已经算出余弦分并传给 LLM。可以在 Python 侧加两道硬短路：`similarity > 0.95` 直接判延续（用现成 summary 追加），`similarity < 0.3` 且时间差大直接判新开。只有中间灰区才调 LLM。风险：极端值边界行为从"语义判断"退化为"阈值判断"，需要评测数据验证精度损失。

**O3：distill 预筛门**（省比例最高的调用，改动小）

蒸馏前用轻量规则判断本轮事实是否有画像价值（如：cluster 内是否含偏好/身份类语义、邻居是否命中已有 belief 槽位、是否纯导航/操作指令）。无价值则直接跳过 LLM 调用。配合审计统计 `beliefs_applied=0` 的比例可量化收益。也可做成 `profile_distill_min_facts` 配置。

### 4.2 成本优化

**O4：extraction system prompt 瘦身**（token 大头）

480 行 system prompt 含 12 个 few-shot 示例，很多示例针对通用个人助手场景（D&D、法律案例、露营）。车载对话分布集中（车控、偏好、出行、闲聊），可以裁到 3-4 个高相关示例 + 保留全部 Integrity Rules，system token 预计降 40-60%。或者用 **prompt caching**（OpenAI 兼容接口的 `cached_tokens` 机制 / vLLM prefix cache）：system prompt 完全静态，天然可缓存，成本可降约 50%（取决于 provider 支持）。

**O5：区分调用点的 max_tokens**（修正确性隐患）

统一 2000 对 extraction 不够（12 条 × 80 词中文 ≈ 2000+ tok）。建议 `Settings` 拆成 `llm_max_tokens_extraction=4000`、其余保持 2000——episode/distill/rerank 输出短，无需放大。

**O6：rerank 候选裁剪**

32 条候选全量序列化。可以先按融合分截断（如 score < 0.3 的丢弃），或按 top_k 相关性只送 2×top_k 条，减半 rerank token。风险：截掉的候选里可能有向量分低但语义关键的项（恰恰是 rerank 的价值），需要评测。

### 4.3 可靠性优化

**O7：LLM 调用重试与超时治理**

当前 4 个调用点均无重试：extraction 一次网络抖动直接 `LLMError` 整轮 add 失败。建议对幂等的读取类调用（episode/distill/rerank）加 1 次指数退避重试；extraction 可加不可重试与可重试错误码区分。

**O8：JSON 解析失败的降级细化**

`parse_object` 解析失败时：episode/distill/rerank 静默降级（好），extraction 直接失败（可讨论）。若截断（O5 修掉后仍可能因格式问题失败），可尝试一次"修复重发"（把坏输出 + 错误信息回传要求修正）再放弃。

### 4.4 结构性优化（长期）

**O9：蒸馏增量化**

现状每次 add 全量重构 cluster + 全量重建 narrative（`refresh_snapshot` 取所有 active beliefs）。用户 belief 数到几百条后，narrative 重建和 profile 读取成本线性涨。可改为：belief 变更只 diff 叙述行；cluster 检索只取与本次事实同 attribute 槽位的邻居。

**O10：按调用点路由模型**

episode 边界判断（二元 + 摘要）和 rerank（选择排序）可用更小更快的模型（如 7B 级）；extraction 和 distill 保留大模型。`Settings` 已有单一 `llm_model`，可扩为 per-task model 映射。车载低延迟场景收益明显。

**O11：调用观测**

四个调用点目前只在失败时打日志。建议给每次调用记录 `latency_ms / prompt_tokens / completion_tokens / parse_ok`（extraction 已有 extracted 计数），进 audit 或独立 metrics 表——上面 O1-O6 的每项优化都需要这个数据做前后对比。

### 4.5 与《LLM 调用降本改进方案》的对照

[降本方案](DesayMem_mem0_LLM调用降本改进方案.md) 是架构级的调用量治理（其"5 次/轮"口径含上游车机 Agent 回复 1 次；本文只统计记忆后端的 4 个调用点，两者不冲突）。它按"在线轻量链路 + 后台分层演化"重排触发时机，与本文的 11 项优化形成三个层次的关系：

**被方案超越（按方案执行则本文项无需再做）**

| 本文项 | 方案对应 | 说明 |
|---|---|---|
| O1 episode/distill 并行 | §5 L2 异步化 + §6 L3 条件触发 | 方案直接移出在线路径（会话结束/idle/证据阈值批处理），比并行更彻底；8 轮会话从 8 次 L2 降为 1 次 |
| O3 distill 预筛 | §6 触发矩阵 | 方案给出完整条件表：明确偏好立即触发、跨 2 日期触发、同会话重复不升级、普通无画像价值事实不触发、累计 10 条批量触发 |
| O6 rerank 候选裁剪 | §7 Rerank 按复杂度启用 | 方案的 `rerank_mode=off|auto|always` 路由直接跳过简单查询的整个调用；`auto` 判据含关键词信号 + 前两名分差 <0.05 |

**正交可叠加（异步化之后仍然要做）**

| 本文项 | 为什么仍需要 |
|---|---|
| O4 prompt 瘦身/caching | L1 extraction 在方案里仍是在线路径（第一阶段保留独立抽取），其 480 行 system prompt 的 token 成本不变 |
| O5 max_tokens 拆分 | L2 批处理一次读 5-10 轮未归档 L1，输入输出都变长，统一 2000 的截断风险**更高** |
| O7/O8 重试与解析修复 | 方案 §10 要求任务最终成功率 ≥99.5%，重试/幂等是前置条件 |
| O10 按任务路由模型 | 批处理对时延不敏感，但在线剩余的 L1 抽取更值得小模型化 |
| O11 调用观测 | 方案 §14 的验收指标（≤1 次/轮、Recall@5 降幅 ≤1pp）全部依赖调用埋点才能度量 |

**方案独有、本文未覆盖的增量设计**

1. **MemoryGate 记忆价值门控**（§4.1）：在 extraction **之前**判断是否值得写长期记忆——比本文 O3（distill 预筛）更靠前，闲聊可省掉整次抽取调用
2. **工具执行成功才写"已执行"事实**（§4.1/§10）：从系统边界治理车机话术污染——"已为您调到22度"只有工具真执行成功才可信，比 extraction prompt 的防回声规则更硬
3. **Agent 回复合并抽取**（§4.3，第三阶段）：车机 Agent 一次返回 `reply + tool_calls + memory_candidates`，后端校验直写不再调抽取模型；保留独立抽取作降级
4. **PG 任务表起步不引 Redis**（§11）：`memory_jobs` + `FOR UPDATE SKIP LOCKED` 轻量 Worker，降低早期部署复杂度

**落地前需注意的四个张力点**

| 张力 | 说明 |
|---|---|
| 关键词门控 vs no-keyword-routing 原则 | MemoryGate 的 `memory_signals`（喜欢/习惯/记住...）和 rerank 路由的 `complex_signals` 都是中文关键词表，与 `prompts.py` 头部 "no keyword lists, Python never routes on keywords" 冲突。更实际的风险："我女儿叫小雨"这类身份事实不含任何信号词会被漏掉——恰是 identity belief 的高价值来源。需评测集标定漏检率，或补实体/NER 通道 |
| profile_slot 固定槽位 vs attribute 开放命名 | 方案 §6 给 L1 预打 `profile_slot="music.artist_preference"`，与 DISTILL prompt 的 "attribute is an open natural-language key, not a fixed taxonomy" 矛盾。可用 `attribute_embedding` 匹配已有 belief 槽位替代硬编码枚举 |
| L2 批处理改变 episode 边界时点 | 现状每轮增量判定延续/关闭；延迟到会话结束/idle 后，跨会话延续的 episode（同一出行分两次上车）判定输入不同，COMPLETE 事件及时性下降 |
| 与现有 recurring 规则的一致性（无冲突） | 方案触发表"跨 2 日期才升级 / 同会话重复不升级"与 `distiller.py:183-186` 的 Python 硬校验语义完全对齐——这部分可以直接落地 |

**综合优先级建议**：方案的阶段一（Rerank auto 路由 + MemoryGate + L2/L3 开关 + flush_session）改动小、收益确定（5→1~2 次/轮），先做；同步落地 O11 观测埋点建基线；阶段二任务表上线后叠加 O5（批处理输入变长后的 max_tokens 调整）；O4 prompt 瘦身/caching 在独立抽取仍在在线路径的整个周期内持续有效。

---

## 五、总结

| 维度 | 现状 | 评价 |
|---|---|---|
| 调用点数量 | 4 个（add 3 + search 1） | 少而克制，职责切分干净 |
| 采样参数 | 全局统一 temperature=0.1 / max_tokens=2000 | 合理但过粗（O5） |
| Token 量 | 单次 add ~12-20k | extraction system 占大头（O4） |
| 延迟 | add 端到端 3 次串行 LLM | 异步场景可用，同步场景需优化（O1-O3，及[降本方案](DesayMem_mem0_LLM调用降本改进方案.md)的异步化路线） |
| 可靠性 | 三处降级 + 一处必抛，无重试 | 降级设计好，重试缺失（O7） |
| 防幻觉 | fake-id + JSON 强制 + Python 校验 | 系统性覆盖，无明显缺口 |

核心设计哲学一以贯之：**LLM 只做语义判断与提议，Python 掌握一切写库与路由权；能确定性计算的绝不调 LLM**。优化空间集中在"何时可以不调"（O2/O3，以及降本方案的 MemoryGate/rerank auto 路由）与"调的时候少花 token"（O4/O5/O6），而不是增加新的 LLM 环节。
