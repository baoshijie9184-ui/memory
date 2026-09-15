# VehicleMem-Eval / VehicleMemBench 数据集详解

> 框架位置：`/data/pengshuang/memory-benchmark/datasets/VehicleMem-Eval`（`psile/VehicleMem-Eval`）
> 定位：**车机记忆系统的统一评测框架**——四数据集一个 harness，同时测准确性（官方口径）+ 效率（token/调用/耗时/内存）
> 关联文档：LightMem 侧的 LoCoMo 评测见 [LightMem-LoCoMo全量测试指南](LightMem-LoCoMo全量测试指南.md)；
> 合成与评测分析见 [VehicleMemBench数据合成与评测分析](数据集评测/2026-09-14-VehicleMemBench数据合成与评测分析.md)——深入拆解该基准的数据合成方式（50 组三人共用一车的长程事件链）、评测口径（工具调用 P/R/F1 精确匹配）与车载记忆适用性结论（适合作为核心能力评测集之一，但不能当唯一数据集）。

---

## 1. 框架总览

```
VehicleMem-Eval/
├── run.py                  # 统一 CLI：python run.py --dataset X --memory-system desaymem ...
├── config/
│   ├── models.yaml         # 模型端点配置（LLM + embedding）
│   └── datasets.yaml       # 数据路径 + 冒烟限制（--full 清零跑官方全集）
├── adapters/               # 四数据集适配器（BaseAdapter: load_data/build_history/get_queries/evaluate_answer）
├── evalcore/               # 编排：memory_build → retrieval → qa → judge
├── metrics/
│   ├── official.py         # 各数据集官方评分（含自实现 Porter stemmer 的 LoCoMo F1）
│   ├── efficiency.py       # 效率聚合
│   └── reporter.py         # TXT+JSON 报告
└── datasets/               # 四数据集原始数据（大部分 LFS）
```

四阶段流水线：`memory_build`（写记忆）→ `retrieval`（向量检索）→ `qa`（作答）→ `judge`（仅 LongMemEval 用 LLM judge，其余规则评分）。

## 2. 四数据集与官方指标

| 数据集 | 规模 | 官方指标 | 与我们的关系 |
|---|---|---|---|
| LoCoMo | 10 对话 ~1986 QA | **Token-F1**（Porter stem + 归一化；cat1 mean-of-max 子答案；cat5 弃答=1.0） | 我们 LightMem 评测的同源数据；**官方口径是 F1 非 LLM judge** |
| LongMemEval | 500 题 6 类型 | GPT 式 yes/no judge（micro + macro） | 含 abstention 弃答类型，judge 需强模型 |
| CarMem | 100 用户 × 10 偏好 | 三阶段：抽取 In-schema F1 / 检索 hit@k / 维护 Pass/Update/Append | **车机偏好记忆专用**，最贴业务 |
| VehicleMemBench | 50 history + QA | 工具调用 P/R/F1（(name, args) JSON 精确集合匹配） | **车机专属**，5 种推理类型 |

**效率记账（内置，与我们文档定稿的记账原则一致且更细）**：每阶段 × {Token In, Token Out, Total, LLM Calls, Runtime(s)} + 全局 Peak Memory(MB, tracemalloc)。

## 3. VehicleMemBench 深度解析

### 3.1 数据结构

```
history/history_{1..50}.txt   # 多用户聊天日志，纯文本
  行格式: [YYYY-MM-DD HH:MM] Name: text
  例: [2025-03-12 10:00] Patricia Garcia: When I'm driving to industrial sites,
      I need the map in 2D so I can match it to the site blueprints.

qa_data/qa_{1..50}.json     # LFS（未 pull 时只有指针文件）
  {related_to_vehicle_preference: [
     {gold_memory, reasoning_type, query, new_answer}]}
```

### 3.2 规模统计（实测 history 文件）

- 每 history 2,380~3,470 行（约 2,400 中位数），50 文件共 134,468 行
- 每文件 **3 个说话人**（如 Gary Allen / Justin Martinez / Patricia Garcia），840 行/人左右
- 时间跨度约 **4 个月**（2025-03 ~ 2025-06），分钟级时间戳
- 内容：日常多话题闲聊（职业、家庭、写作、医疗……）+ **嵌着车辆控制偏好**（"driving to industrial sites 需要 2D 地图"这类）——车辆偏好在对话中自然出现，不是全篇车控

### 3.3 评测机制（adapter 源码考证）

- **入库**：所有说话人全部转为 `user` 消息，内容拼 `[名字] 文本`（adapter build_history）——**无 assistant 角色**，与 LoCoMo 协议同思路（人说的才入库）
- **query**：`related_to_vehicle_preference` 里每条一个 query + gold 工具调用集合 `new_answer`
- **评分**（score_tool_calls，official.py:353）：预测/参考工具各自转 key = `(name, json.dumps(args, sort_keys=True))`，集合匹配算 P/R/F1；exact_match = F1==1 且无多余/缺失（作为官方"环境状态精确匹配"的无仿真器代理）
- **分组**：按 reasoning_type 5 类报告——preference_conflict（偏好冲突）/ conditional_constraint（条件约束）/ coreference_resolution（共指消解）/ error_correction（纠错）/ state_shift（状态迁移）

### 3.4 合理性评估（结论：**可用，但有 4 个显著缺陷**）

**合理的部分**：
- 评测目标真实：车机记忆的终点不是聊天，是**触发正确的车控动作**——工具调用 F1 比文本 QA 更贴近业务终点
- 5 种推理类型设计好：偏好冲突/状态迁移等正是长期记忆的难点（偏好会变："我以前喜欢 3D 地图，现在工地巡检要 2D"）
- 多用户共享一个记忆库（3 人/文件）：比 LoCoMo 的"每对话隔离"更接近车机多人用车场景
- 效率记账与准确性并列：符合车机边缘部署约束

**缺陷/注意点**：
1. **数据是合成的**：内容风格统一（英文闲聊模板感重），偏好的分布密度、语言多样性远低于真实车机语料——绝对分数不可外推到产品，只能做系统间横评
2. **无 assistant/车机回复**：和 LoCoMo 一样只记人说话。真实车机场景车机本身有大量动作（已执行的导航/空调操作应进记忆），**这层信息 VehicleMemBench 不覆盖**——若自研系统记忆车机行为，此评测无法度量其价值（对照 LightMem 指南"车机适配 B/A/C 策略"）
3. **工具调用 F1 的 exact match 苛刻**：args 是 JSON 精确匹配（sort_keys 后字符串相等），多一个默认参数/参数顺序含义相同都算 FP——报分数时建议主看 F1，exact match 作参考
4. **LFS 数据当前未拉取**：qa_data/*.json 与 carmem/longmemeval 主体都是 LFS 指针，跑前必须 `git lfs pull`（需 LFS 网络/配额）；history 目录可直接用

### 3.5 怎么测比较好（建议方案）

**第一步：先跑 LoCoMo + CarMem（数据已可用/轻量）**
```bash
# 在 VehicleMem-Eval 目录
python run.py --dataset locomo --memory-system desaymem --model-config <models.yaml条目> --sample-limit 1   # 冒烟
python run.py --dataset carmem --memory-system desaymem --model-config <...> --full                            # CarMem全量（100用户偏好抽取）
```
CarMem 不需要长对话写库，测的是偏好抽取/检索/维护三阶段，成本低、与车机偏好业务直接对应，**建议作为自研系统的第一个评测**。

**第二步：LFS pull 后跑 VehicleMemBench**
```bash
git lfs pull   # 拉取 qa_data 等
python run.py --dataset vehiclemembench --memory-system desaymem --model-config <...> --full   # 50 文件全量
```
评测时让记忆系统输出工具调用格式（adapter 的 parse_pred_tools 会从模型输出解析 (name,args)），按 5 类 reasoning_type 看 F1 分化——**preference_conflict 和 state_shift 预期最难**（记忆系统要能更新旧偏好而非只堆叠）。

**第三步：与 LightMem/自研移植版对照时注意**
- VehicleMemBench 的对话量级（~2,400 行/文件 × 50）≈ LoCoMo 的 3 倍/对话，add 成本相应更高
- LoCoMo 官方口径是 **F1（Porter stem）**，与 LightMem 指南里 eval_f1.py 的朴素词集合 F1 有差异（stem 后 "maps"="map" 会命中）——**最终对外报告应统一用 metrics/official.py 的官方实现**，自研 F1 作过程监控
- 效率对比表直接用框架输出的分阶段 token/calls/runtime/peak-mem，无需自己再记账

## 4. 多记忆系统对比接入方案（与平台规划 §11 对齐，2026-09-13 讨论定稿）

**目标**：这套四数据集 harness 作为统一赛场，让当前流行的记忆架构（Mem0 / MemoryOS / LightMem / Zep / DesayMem / 自研）在同一 LLM、同一 embedding、同一 prompt、同一评分下对比。

### 4.1 现状：数据三档可用，框架只支持 desaymem

| 数据集 | 可用性 | 动作 |
|---|---|---|
| LoCoMo | ✅ 就绪 | 无 |
| CarMem / VehicleMemBench | ⚠️ LFS 指针 | `git lfs pull`（文件都很小：1.9MB / 4KB×50） |
| LongMemEval | ⚠️ LFS 指针 | 同上（主文件 265MB 较重） |

框架硬编码 `MEMORY_SYSTEMS = ["none", "desaymem", "vehiclemem"]`（run.py:30），`_build_memory_system`（eval_engine.py:75）里 `from edge.edge_memory import EdgeMemory` + DesayMem 专属 config——**接入第三方系统必须改这里**。

### 4.2 记忆系统的实际接口只有 4 个（适配面很窄，这是好消息）

eval_engine 实际消费的全部方法：

```python
mem.add_memory(user_input=..., agent_response=..., user_id=..., timestamp=...)   # 逐 turn 写
mem.retrieve_memory(query, user_id, top_k=5) -> 文本                              # 读（结果会被 _format_retrieval_context 拼进 prompt）
mem.ingest_history(path, uid) / ingest_history_text(text, uid)                   # VehicleMemBench 可选批量灌入（hasattr 判断）
mem.delete_memoryos_user(uid)                                                     # 测完清理，可选（hasattr 判断）
```

任何记忆系统写一个薄 bridge 实现前两个即可跑；后两个 hasattr 探测，不实现也能跑（VehicleMemBench 退化为逐 turn add）。

### 4.3 接入步骤（以 LightMem 为例）

```python
class LightMemBridge:
    def __init__(self, model_cfg):
        config = {...LightMem 配置, 库路径按 user_id 隔离...}
        self.lm = LightMemory.from_config(config)
    def add_memory(self, user_input, agent_response, user_id, timestamp):
        self.lm.add_memory(messages=[{"role":"user","content":user_input,
                                      "time_stamp":timestamp,"speaker_name":user_id},
                                     {"role":"assistant","content":agent_response or ""}], ...)
    def retrieve_memory(self, query, user_id, top_k=5):
        entries = self.lm.retrieve(query, user_id, top_k)   # 按库路径隔离对话
        return "\n".join(e.memory for e in entries)
    def delete_memoryos_user(self, uid):
        shutil.rmtree(f"库目录/{uid}")                      # 评测隔离，对应平台规划 reset(namespace)
```

在 `_build_memory_system` 加分支（建议改注册表：`MEMORY_SYSTEMS` dict → name: factory），wrapper 按平台规划 §4.1 路径管理（临时在 `systems/adapters/`，正式迁 `runner/adapters/`）。

### 4.4 与平台规划 §11 的关系（两套接口不要强行合一）

平台规划的统一 `MemoryAdapter`（health/reset/add/search/get_state/close + 身份字段 tenant/user/vehicle/occupant/session + 结构化输出）是**长期规范**，比 eval_engine 的窄接口（4 方法、user_id 单键）宽。建议两层架构：

```
runner/adapters/ 统一 MemoryAdapter 规范（长期）
       └── 薄转换层 → VehicleMem-Eval 窄接口（评测期）
```

评测期不阻塞：先写窄接口 bridge 跑对比出数字；统一 Adapter 规范并行演进。

### 4.5 公平性红线（对比前自查）

- 同一 LLM / embedding 端点（models.yaml 保证）
- 检索 top_k 统一（框架默认 5）
- prompt 由框架统一组装（bridge 不得私自改写检索文本语义）
- 各系统的 add 侧参数（抽取模式/压缩率/去重阈值）需在 manifest 里记录——这些是系统间真实差异，不是不公平，但必须透明
- 效率记账（分阶段 token/calls/runtime/peak-mem）框架自动生成，直接取用

### 4.6 建议落地顺序

1. `git lfs pull`（一次补齐全部数据）
2. desaymem 四数据集冒烟（`--dataset all --sample-limit 1`）确认框架可用
3. `_build_memory_system` 改注册表 + LightMemBridge / Mem0Bridge 两个模板
4. 全量对比：`--dataset all --memory-system <X> --full` 逐系统跑，报告自动出

## 5. 更新记录
- 2026-09-13 初版：框架结构、四数据集官方指标、VehicleMemBench 深度解析（规模实测、评分机制源码考证、4 缺陷、三步测试建议）
- 2026-09-13 增补：多记忆系统对比接入方案——数据可用性三档、记忆系统 4 方法窄接口考证、LightMemBridge 模板、与平台规划 §11 两层架构对齐、公平性红线、落地顺序
