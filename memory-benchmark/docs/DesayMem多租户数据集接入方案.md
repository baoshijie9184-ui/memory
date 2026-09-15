# DesayMem 多租户数据集接入方案与优化计划

> 位置：`datasets/DesayMem_multitenant_benchmark/`
> 定位：现有四数据集（locomo/longmemeval/carmem/vehiclemembench）之外的第五个维度——**多租户隔离 + 召回抗噪 + 去重语义**
> 与单用户版 `DesayMem_yearlong_benchmark` 互补（那套测召回质量，这套测隔离正确性）

## 1. 数据集概况

- **规模**：814 sessions / 1680 条消息 / 26 条评测用例；2 租户 × 3 车 × 6 用户（father 为主，SUV1 474 + SUV2 25 + SED 46，mother 92 / child 69 / grandma 81 / shared 27）
- **时间**：2025-09-08 → 2026-09-07 全年，逐月均匀，含 4 个出差周（mother 接管通勤）+ 2 个休假周空窗
- **可复现**：seed=20260907 确定性生成，`scripts/generate_multitenant_dataset.py --seed 20260907` 可重造
- **组织**：`tenants/{tenant}/{vehicle}/{user}/sessions.jsonl + persona.json`，另有 `_shared_multi_occupant.jsonl`（9 次家庭旅程 × 3 乘员，同 session_id 不同 occupant）

### 评测范式（与四数据集的根本差异）

不是 QA→LLM 判分，而是**检索断言式**：

| 断言 | 含义 |
|---|---|
| `must` | Top-K 结果必须命中关键词 |
| `must_not` | Top-K 全部结果中不得出现 |
| `not_top1` | Top1 不得是该项 |
| `search_dual` | 同一查询打两个 scope 分别判定（iso_05） |
| `vehicle_leak` | filter 模式下不得泄漏其他车辆记忆 |

四组用例：A 隔离 9 条（iso_01~09，互斥画像判定泄漏）、B filter 语义 4 条（flt_01~04）、C 模糊召回 10 条、D 陷阱 3 条（偶发不覆盖习惯/闲聊不入库）。

### 画像互斥设计（隔离判定的核心）

| 用户 | 独有偏好 |
|---|---|
| father | 亲子餐厅=童梦森林、清蒸鲈鱼少油不要香菜、西冷七分熟 |
| mother | 亲子餐厅=麦田（互斥）、藜麦沙拉、拿铁少糖燕麦奶 |
| child | 汪汪队、草莓酸奶 |
| grandma | 粤剧音量 12、每周三市中心医院透析 |
| father@SUV2 | 露营模式（红花湖+后排放倒） |
| father@SED | 话术与 SUV 完全相同 → 测跨租户去重 |

## 2. 质量评估

**优点**：
- 隔离维度是四个现有数据集都没有的，且互斥画像让隔离失败可直接判定（不用人工判）
- 抗噪设计好：17 条偶发偏离 + 25 条闲聊 + "老规矩/跟平时一样"模糊指代
- 时间结构真实（月分布、出差/休假空窗、42 天亲子餐厅轮换）

**短板（优化方向）**：
1. 规模小：26 用例，统计意义弱
2. 单轮指令流：平均 2 条消息/session，无多轮交互
3. 题型单一：无时间推理（"上个月去的餐厅"）、无多跳、无更新演化（偏好改变的追踪）
4. assistant 回复模板化（"导航X+空调N度+放Y"三段式）
5. **关键词判定脆弱**：must 依赖原文措辞，记忆系统做摘要/改写存储会误判——对 LightMem 这类抽取式记忆是系统性风险
6. 场景失衡：commute 占 56%（459/814）

## 3. 接入 VehicleMem-Eval 方案

### 3.1 范式适配：新增"检索断言"adapter

现有 pipeline 是 adapter→(history,QA)→记忆系统→LLM 作答→judge/F1。DesayMem 不需要作答，只需检索。最小改造：

- 新建 `adapters/desaymem_adapter.py`，不走 QA pipeline，实现 `run_official_eval` 的变体：**断言式评测**
  - 每个用例调 `mem.retrieve_memory(query, uid, top_k=5)` 拿回文本
  - 判定：must 关键词 in 文本、must_not 关键词 not in 文本、not_top1 看 Top1 行
  - 指标：通过率（ACC=通过用例/26）
- 复用现有 bridge 窄接口，mem0/structmem/lightmem 都能直接跑

### 3.2 scope → uid 映射（多租户的关键）

用例的 scope 是 (tenant_id, user_id[, vehicle_id, occupant_id])，bridge 只有 user 维度。映射方案：

**uid = `{tenant}_{user}_{vehicle}`**（如 `oem_suv_family_usr_father_veh_suv_001`），同 carmem 复合 uid 的剥后缀机制：
- 纯用户级隔离（iso_01~09）：建库按 (tenant, user) 合并该用户所有车的 session → collection `desaymem_{tenant}_{user}`
- 车辆级过滤（flt_02）：collection 细分到车，`_collection_for` 剥后缀命中
- profile/dedup 用例（flt_04、iso_06）：初期可跳过或降级为 search 断言，记 TODO

### 3.3 建库

复用 `build_lightmem_lib.py` 模式（LightMem 管线），新数据集入口 `--dataset desaymem`：
- 加载数据集自带 loader（按 (tenant,user) 聚合 sessions.jsonl，messages 有时间戳前缀可解析）
- 跨用户隔离靠 collection 天然实现（每 (tenant,user) 一个库，绝不共享）
- 预计 7 个 collection（7 个 tenant×user 组合），father 需 3 个（SUV1/SUV2/SED 分开建才能测 flt_02，合并建只能测 iso 组——先建细粒度，断言时剥后缀聚合）

### 3.4 判定放宽（针对抽取式记忆的误伤）

must 关键词过 strict 会对 LightMem 误伤（抽取时"少糖"可能被改写成"低糖"）。两档判定：
- **strict**：原文关键词子串匹配（对齐官方语义）
- **loose**：LLM 判定语义等价（memory-llm 一问一答："检索结果里是否表达了'拿铁要少糖用燕麦奶'？"）
- 报告两档都出，loose 作为主要参考、strict 作为下界

### 3.5 冒烟计划

1. 建库（7 collection，约 814 session 总量小于 locomo 单对话，估 <1 小时）
2. structmem × desaymem 断言评测（iso_05 双断言是最有信息量的一条）
3. none 基线没有意义（无记忆系统无检索结果，跳过）
4. mem0 可跑同 pipeline 对照

## 4. 数据集优化计划（丰富方向，按价值排序）

### P0（提升评测有效性）
1. **must 关键词语义判定标注**：给每条 must 加同义改写集（如"少糖"→["少糖","低糖","不加糖","less sugar"]），解决抽取式误伤——改 eval_cases.json schema，兼容原 strict 匹配
2. **扩用例规模**：26 → 60+，重点补：时间推理（"上周三 grandma 去哪""去年冬天空调怎么设"）、偏好演化（42 天轮换周期做成"这个月该谁选餐厅"）、多跳（"孩子上次坐车看的动画片对应的零食"）

### P1（提升真实度）
3. **多轮对话**：把 10% session 改 3-5 轮（追问/确认/纠正："不是这家园，是上个月那家"）
4. **场景再平衡**：commute 56%→35%，补 charging/maintenance/poor_air（现在 6/3/6 条太少）
5. **assistant 去模板化**：回复加 2-3 变体 + 偶尔只执行部分指令

### P2（规模扩展）
6. **更多租户/用户**：2→5 租户，加"同 user 跨 3 车辆""同车辆跨 2 用户"矩阵
7. **时间跨度延长或并行多用户年**：多份 seed 并行生成

### 执行方式
优化基于 `scripts/generate_multitenant_dataset.py` 改生成器（seed 可复现），改完 `--mode dry-run` + pytest 验证一致性。建议 P0 先行（影响判定正确性），P1/P2 在全量评测跑通后做。

## 5. 接入状态

- [x] 数据集分析（本文档 §1-2）
- [x] desaymem_adapter.py 断言式评测（§3.1，含 strict/loose 双判定）
- [x] scope→uid 映射（§3.2，10 个 collection：usr_X 粗 + usr_X_veh_Y 细）
- [x] 建库（§3.3，--skip-existing 断点续跑）
- [x] 冒烟 structmem（§3.5：strict 11/26，loose +5；真 FAIL 均为 LightMem 抽取丢实体——见 DS-11）
- [ ] P0 优化落地（§4）
- [ ] mem0 冒烟对照（mem0 建 814 session 较慢，可后置）

（施工日志追加到 docs/三数据集适配调研与施工记录.md，编 DS-11+）
