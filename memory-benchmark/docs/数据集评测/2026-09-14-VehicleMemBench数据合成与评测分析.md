# VehicleMemBench：数据合成、评测要点与车载记忆适用性

> **日期**：2026-09-14  
> **对象**：论文 [VehicleMemBench: An Executable Benchmark for Multi-User Long-Term Memory in In-Vehicle Agents](https://arxiv.org/abs/2603.23840)（arXiv:2603.23840）  
> **本地数据**：`D:\agent memory\code\VehicleMem-Eval\datasets\vehiclemembench`  
> **对照材料**：本仓库《车载云端记忆工业界调研与差异化方案》、DesayMem yearlong 模糊记忆评测、VehicleMem-Eval 适配器实现  
> **结论先行**：适合作为车载记忆系统的**核心能力评测集之一**，不适合当**唯一数据集或唯一指标**。它测的是「多乘员长程偏好能不能变成正确的车控终态」，不是「检索列表排得对不对」，也不是中文座舱、隐私、安全门控或 Skill 闭环。

---

## 1. 它实际在测什么

VehicleMemBench 由中科大与科大讯飞提出，定位为**可执行的车载多用户长期记忆基准**。现有记忆评测（LoCoMo、LongMemEval）多为单用户、静态问答；现有工具评测多为短程指令跟随。该基准把两者接到一起：Agent 必须从很长的多人交互历史中恢复偏好，处理冲突与演化，再调用车控工具，使仿真车辆到达目标状态。

本地目录结构：

```
datasets/vehiclemembench/
├── history/history_{1..50}.txt    # 三人共用一车的长对话历史
└── qa_data/qa_{1..50}.json        # 每文件 10 道可执行题
```

规模（论文 Table 3）：

| 项 | 数值 |
|---|---|
| 场景组 | 50 组，每组 3 个固定乘员，共 150 人设 |
| 查询 | 500 条；每场景 10 题共享同一段历史 |
| 事件链 | 共 1,500 条；每实例平均 30 条链 |
| 历史长度 | 平均 81.78 个事件、2,690 行、约 92,819 token（GPT-4o tokenizer） |
| 查询本身 | 平均 37.63 token，很短 |
| 时间跨度 | 偏好出现到被提问，平均延迟 23.16 天 |
| 执行面 | 23 个车载模块、111 个 `carcontrol_*` API |

无记忆时，最强模型 Exact State Match 也只有约 19.4%。题目没法靠常识猜，必须从长历史里找回偏好再落到车辆状态。

与 LoCoMo 的关键差别：LoCoMo 问的是「对话里发生过什么」；VehicleMemBench 问的是「根据记过的偏好，现在该把车调成什么」。

---

## 2. 数据合成流程

论文的核心不是直接写车机对话，而是先造结构化事件链，再把车载偏好埋进很长的人际闲聊。查询短、历史长，正确答案不能从问句本身猜出来。

### 2.1 五阶段管线

| 阶段 | 模型与参数 | 做什么 | 关键约束 |
|---|---|---|---|
| 1. 人设组 | Gemini-3-Pro，T=0.7 | 从 Persona-Hub elite 子集抽 100 组 × 3 人，补全职业、MBTI、舱内偏好；人工留 50 组 | 三人共享同一辆车 |
| 2. 事件链 | Gemini-3-Pro，T=0.8 | 每组约 30 条链：20 条生活干扰 + 10 条可执行车载偏好 | 单条事件不能泄露最终答案 |
| 3. 时间交织 | 规则按时间戳排序 | 多条偏好线程混排，跨数周到数月 | 车载线索被长闲聊淹没 |
| 4. 对话生成 | GPT-4.1，T=0.7 | 事件扩成乘员之间的自然语言对话 | 禁止对车说话；偏好只能旁敲侧击；干扰事件 ≥40 行 |
| 5. 问答与金标 | Gemini-3-Pro，T=0.0 | 生成延迟查询 + `carcontrol_*` 工具序列，在 VehicleWorld 执行校验 | 终态对齐目标车辆状态 |

人工质检（三人独立标注，全票通过才保留）：

- 500 个事件中改了 83 处（16.6%，Cohen's κ=0.74）
- 500 条查询改了 42 处（8.4%，κ=0.69）
- 500 条答案改了 62 处（12.4%，κ=0.77）

对话看起来像 LoCoMo 长聊，车载设定是夹带进去的。本地 `history_1.txt` 就是林业写手 Gary Allen、儿科医生 Justin Martinez、工业工程师 Patricia Garcia 共用一辆车：大量篇幅在谈 Egleston 林业史、孩子耳炎、锅炉延期，中间才夹一句 “I really love this green instrument panel”。

### 2.2 对话生成的关键设计（难度主要来源）

Appendix I 的对话 prompt 明确要求：

1. **只生成人对人说话**，不允许出现车载 Agent / AI 助手的话语。
2. 车载偏好必须作为闲聊里的旁白、抱怨或对另一乘员的请求出现，**不能写成显式语音命令日志**。
3. 说话人身份必须与事件元数据一致。
4. 每条与车辆无关的干扰事件至少扩成 40 行对话。
5. 不得把结构化事件改写成更直白的形式。

这使基准明显难于「把事件直接转成结构化记忆记录」，也更接近真实座舱里偏好常被间接透露的情况。但与真实量产座舱仍有差距：真实场景大量是人对车的短指令，而不是超长人际闲聊。

### 2.3 QA 数据字段

以 `qa_1.json` 为例，每条样本为：

```json
{
  "gold_memory": "[March 10, 2025] ... Gary set the panel color to green.\n[March 25, 2025] ... Patricia changed the panel color to white.",
  "reasoning_type": "preference_conflict",
  "query": "At 10:00 AM, Gary got into the driver's seat ... 'Let's get back to my usual calming atmosphere for this drive.'",
  "new_answer": [
    "carcontrol_instrumentPanel_set_color(color=\"green\")"
  ]
}
```

| 字段 | 含义 |
|---|---|
| `gold_memory` | 从历史抽出的相关片段，供对照与 Gold Memory 设定；评测 Autonomous Memory 时不应直接喂给被测系统 |
| `reasoning_type` | 五类推理之一 |
| `query` | 延迟出现的新请求，通常很短、很口语 |
| `new_answer` | 金标工具序列，在仿真器中执行后得到目标终态 |

---

## 3. 五类推理任务

每实例含 20 条背景链（生活噪声）和 10 条可执行车载偏好链。五类任务相对均衡，没有单一类别主导。

| 类型 | 题数 | 占比 | 要测什么 | 本地 `qa_1` 例子 |
|---|---:|---:|---|---|
| Preference Conflict | 149 | 29.8% | 同一设备多人偏好冲突，按当前驾驶员/在场角色执行 | Gary 喜欢绿色仪表盘，Patricia 夜间要白色；Gary 说 usual calming atmosphere → `set_color("green")` |
| Conditional Constraint | 102 | 20.4% | 偏好绑定时间/地点/天气/活动，条件未触发则不能套用 | Patricia 进工业区要内循环；看到烟囱说 Here comes the smog zone |
| Coreference Resolution | 97 | 19.4% | 昵称、代词、习惯说法映射到具体 API 参数 | 外甥喜欢蓝色氛围灯；Justin 说 his favorite color |
| State Shift | 90 | 18.0% | 偏好演化后用最新有效值；旧值可追溯但不能当当前值 | 座椅/HUD 等设置随时间改写，后续按新状态执行 |
| Error Correction | 62 | 12.4% | 用户明确纠错后，错误记忆必须失效 | 通风 3 档太强改成 2；later comfortable level → `speed=2` |

State Shift 与 Error Correction 都要时序推理，但处理旧记忆的方式不同：

- **State Shift**：多个历史状态可以共存，系统优先最新有效值。
- **Error Correction**：必须主动作废错误旧值，使其不再被考虑。

可执行查询覆盖的目标模块（按金标工具序列统计）：Navigation 19.2%、Seat 17.0%、Light 12.8%、AirConditioner 12.8%、InstrumentPanel 8.4%、Music 8.2%、其余 21.6%（天窗、后视镜、车门、踏板等）。

论文实验还表明：**Conditional Constraint 全场最难**；Preference Conflict 反而相对容易。多用户干扰不是主瓶颈，真正难的是把偏好绑到天气、时间、地点、活动等触发条件上。

---

## 4. 评测协议与指标

### 4.1 官方协议

评测分两段：

1. **离线 ingest**：将按时间排序的对话历史 `D` 写入记忆系统，得到长期记忆 `M`。通用记忆系统按天切分、按各自 API 顺序灌入。
2. **在线交互**：Agent 拿到新查询 `q` 和初始车辆状态 `v_init`，最多 10 轮，每轮只能做一类动作：
   - 记忆检索（`memory_search` / `memory_list`）
   - 模块发现（`list_module_tools`，按需加载该模块的 `carcontrol_*`）
   - 车控工具调用（改变环境状态）
   - 终止（纯文本回复，结束循环）

金标工具序列在同一初始状态上执行，得到参考终态 `v_ref`，与模型终态 `v_pred` 比较。少量文本参数用模糊匹配，其余字段精确匹配。

### 4.2 官方指标

| 指标 | 公式/含义 | 用途 |
|---|---|---|
| **ESM（Exact State Match）** | `1[v_pred = v_ref]` | **主指标**，严格 0/1 任务成功 |
| Field-F1 | 相对初始状态，改对了哪些字段 | 看有没有选对设备/属性 |
| Value-F1 | 改对的字段里，值是否也正确 | 看细粒度偏好有没有记准 |
| Calls | 平均每题工具调用次数 | 执行开销 |
| MemToken | 平均每次记忆检索的 token 数 | 检索成本 |
| **MemoryScore** | `ESM_auto / ESM_gold` | 把记忆能力和工具能力拆开 |

三个对照档必须一起看：

| 设定 | 输入 | 测什么 |
|---|---|---|
| No memory | 只有问句 | 题目是否真依赖历史（最强模型 ESM ≈ 19%） |
| Gold Memory | 正确偏好直接塞进上下文 | 工具会不会用（Gemini-3-Pro ESM 90.6） |
| Autonomous Memory | 系统自己写、自己召回 | **记忆系统分数**（Rec. Sum. 下 Gemini-3-Pro 掉到 64.8） |

论文关键实验信号：

- 记忆是主瓶颈。Gold → 自动记忆，各骨干模型 ESM 普遍掉 20 分以上；记忆误差占失败的 63.9%。
- 自动记忆时常能选对字段、给不对具体值（Field-F1 与 Value-F1 拉开约 10 分）。
- 通用 Mem0 / MemOS / Memobase 经常打不过针对车载改过的 Recursive Summarization。原因是通用系统留下大量生活噪声，召回时把过期或不相关偏好带进来。
- 准确与成本很难兼得：Supermemory 较准但 MemToken 近 1000；KV Store 便宜但 State Shift / Conditional 明显更差。

### 4.3 本地 VehicleMem-Eval 的代理指标

`adapters/vehiclemembench_adapter.py` **不拉起 VehicleWorld**，用官方 `score_tool_calls` 做代理：

- 将预测与金标都解析为 `(name, json.dumps(args, sort_keys=True))`
- 计算工具集合的 Precision / Recall / F1
- F1≈1 且无多余/缺失，当作 exact match

优点：可复现、便宜、能按 `reasoning_type` 分组。  
缺点：两条语义等价但顺序/拆分不同的工具序列会被判错；漏掉字段/取值级细粒度；混进了格式服从误差，分不出「记错了」还是「API 写错了」。

对外报官方 VehicleMemBench 分数时，应以仿真器 ESM 为准；本地 tool-F1 只能作为无仿真器时的工程代理。

---

## 5. 适不适合评价车载记忆系统

### 5.1 适合压的能力

与《车载云端记忆工业界调研与差异化方案》中的差异化主线高度对齐：

- **多乘员归属与冲突消解**：三人共用一车，必须按当前驾驶员执行。
- **情境化时序记忆**：条件偏好、State Shift、Error Correction 要求记住「谁、在什么条件下、何时有效」。
- **记忆到执行的闭环**：主指标是任务成功率（终态），不是问答字面重合。这对应调研稿里「记忆的商业价值来自服务完成率，而非召回率本身」。
- **抗噪声检索**：20 条生活链淹没 10 条车载链，能检验系统会不会把林业论文、过敏、出差写进车辆偏好。

对本项目数据模型的直接含义：Event / Preference 必须带 `occupant`、`condition`、`valid_from`（以及纠错后的失效标记），而不能只存一句「用户喜欢绿色」。yearlong 评测里的「外面空气不好，按老规矩调整」本质上就是 Conditional Constraint。

### 5.2 不能覆盖的缺口

对照调研稿 §10 必测场景：

| 需要测的能力 | VehicleMemBench | 建议 |
|---|---|---|
| 多乘员冲突 / 条件偏好 / 纠错 / 时序更新 | 强覆盖 | 作为主测集 |
| 记忆 → 车控动作的任务成功率 | 官方 ESM 强；本地 tool-F1 中等 | 有仿真器用 ESM；没有则保留 tool-F1，并拆 Gold vs Auto |
| 中文座舱、POI、亲子餐厅、空气老规矩 | 无，全英文合成人设 | 继续用 yearlong_cockpit + must_fact_recall |
| 人–车短指令、传感器 / 位置 / AQI 真值 | 只有对话旁白，无真实车况通道 | 补 metadata 驱动用例 |
| 抽取质量、P@K、MRR、过期误召回 | 不评检索列表，只评终态 | 保留现有检索层指标做诊断 |
| 隐私串扰、访客隔离、删除 / 遗忘 | 几乎不测 | 自建必测集 |
| 安全门控、Skill 蒸馏、主动建议打扰率 | 不测 | Skill 层单独评 |
| 端云同步、离线、跨车迁移 | 不测 | 工程评测，不走这个 benchmark |

另外两点形态偏差需要心里有数：

1. **对话形态与真实座舱相反。** 真实场景大量是短指令 + 车况；这里是超长人际闲聊里埋偏好。会高估「从长文本挖旁白」的能力，低估「短指令 + 结构化车况」的能力。
2. **语言与人设偏西方。** 林业作家 / 儿科医生 / 工厂工程师的英语闲聊，不能代表国内座舱的 POI、亲子、点餐、通勤话术。

### 5.3 和现有 DesayMem 评测如何分工

| 评测资产 | 粒度 | 回答的问题 |
|---|---|---|
| VehicleMemBench | 记忆 → 车控终态 | 多乘员长程偏好有没有变成正确动作 |
| yearlong + must_fact_recall / P@K / MRR | 检索列表 | 写进去了没、召回了没、排没排到前面 |
| CarMem | 偏好抽取 / 检索 hit / Pass-Update-Append | schema 抽取和维护动作对不对 |
| LoCoMo / LongMemEval | 通用长对话 QA | 通用记忆能力下限，不代表车载 |

VehicleMemBench 不告诉你「写进去了没召回」还是「根本没写成带条件的偏好」。终态错了之后，仍要用检索层指标做诊断。

---

## 6. 建议用法

1. **当作主能力探针，不要当全集。** 跑 Gold Memory 和 Autonomous Memory 两档，按五个 `reasoning_type` 拆开看。两档差距大，说明记忆层有问题，不是 LLM 不会调工具。
2. **有 VehicleWorld 就用 ESM；没有就继续用 tool-F1，但不要对外宣称官方分数。**
3. **检索诊断继续用现有 yearlong + must_fact_recall / P@K / MRR。**
4. **中文场景、隐私、安全、Skill 必须自建。** 这才是论文和通用 Mem0 都没覆盖、可以形成壁垒的评测资产。
5. **优先盯 Conditional Constraint 和 Error Correction。** 论文里前者最难，后者直接对应「用户纠正后旧记忆必须失效」——这是产品信任的核心。

一句话：VehicleMemBench 是目前最贴近「车载多用户长期记忆 → 正确执行」的公开基准，用来卡记忆系统是否懂冲突、条件和时间；不能用来证明中文座舱产品、端云记忆或安全 Skill 已经评完。

---

## 7. 来源

- Chen et al., *VehicleMemBench: An Executable Benchmark for Multi-User Long-Term Memory in In-Vehicle Agents*, arXiv:2603.23840, 2026. 数据：<https://huggingface.co/datasets/callalilya/VehicleMemBench>；代码：<https://github.com/isyuhaochen/VehicleMemBench>
- 本地实现：`VehicleMem-Eval/adapters/vehiclemembench_adapter.py`、`metrics/official.py`（`score_tool_calls`）
- 样本：`datasets/vehiclemembench/history/history_1.txt`、Hugging Face `qa_data/qa_1.json`
- 对照：`车载云端记忆工业界调研与差异化方案.md` §10；`DesayMem_mem0/data_cesi/memory-add-search-test-guide.md`
