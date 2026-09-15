# VehicleMem-Eval 多记忆系统接入改造记录

> 目标：把 VehicleMem-Eval（`datasets/VehicleMem-Eval`，自有仓库 psile/VehicleMem-Eval，可直接改代码）从"仅 desaymem"改造成**统一赛场**：lightmem / structmem / mem0 / memoryos / desaymem / none 同台对比，同 LLM、同 embedding、同 prompt、同官方评分。
> 关联：[VehicleMemBench数据集详解](VehicleMemBench数据集详解.md)（数据集分析）、[LightMem-LoCoMo全量测试指南](LightMem-LoCoMo全量测试指南.md)（LightMem 侧评测）
> **本文档定位：改造的施工日志。每一步改动（文件、函数、为什么这么改）都记录在案，保证可维护、可回溯。**

## 0. 改造前的基线状态（2026-09-13 盘点）

| 事实 | 出处 |
|---|---|
| eval 框架只认 `MEMORY_SYSTEMS = ["none", "desaymem", "vehiclemem"]` | run.py:30 |
| `_build_memory_system` 硬编码 `from edge.edge_memory import EdgeMemory`，期望 `datasets/../DesayMem` 目录 | eval_engine.py:75-118 |
| **DesayMem(edge) 本机不存在**——框架默认系统在这台机器上反而跑不了 | 全盘 find 无 edge_memory.py |
| eval 引擎消费记忆系统的全部接口只有 4 个方法（见 §1.2） | eval_engine.py:325-368 |
| 记忆系统口径约定：**系统只出检索文本，作答 LLM 由框架统一**（公平性前提） | eval_engine.py:334-341 |

各被测系统 API 盘点（写 bridge 的依据）：

| 系统 | 位置 | 写入 API | 读取 API | 清理 API | 备注 |
|---|---|---|---|---|---|
| mem0（官方） | `systems/mem0`（mem0ai/mem0@c7ee362a） | `Memory.add(messages, user_id, infer=True)` main.py:760 | `search(query, filters={'user_id'}, top_k)` :1379 | `delete_all(user_id)` :1890 | OpenAI 兼容 LLM+embedder，Qdrant 本地模式；`infer=True` 内部 LLM 抽取+add/update/delete 决策 |
| LightMem | `systems/LightMem` | `add_memory(messages=[{role,content,time_stamp}], ...)`（消息对） | `retrieve()` + qdrant 直读 | 删库目录 | 已跑通 LoCoMo 全套（见指南） |
| StructMem（自研） | `systems/StructMem` | `add_memory()` structmem.py:187 | `retrieve()` :522；另有 FastAPI :130 | 删库目录 | LightMem 同构，PostgreSQL 存储 |
| MemoryOS | `systems/MemoryOS-detailed/.../memoryos-chromadb` | `add_memory(user_input, agent_response, timestamp)` memoryos.py:245 | `get_response(query)` :269（**自带作答**）或内部 retriever | `close()` | 签名与 eval 引擎几乎直配 |
| desaymem(edge) | **缺位** | — | — | — | 待补（不阻塞其他系统） |

## 1. 设计决策（动手前定稿）

### 1.1 注册表替代硬编码
`_build_memory_system` 的 if-else 改为 `evalcore/memory_bridges.py` 注册表：`{"none":…, "mem0":…, "lightmem":…, "structmem":…, "memoryos":…, "desaymem":…}`。run.py 的 choices 同步从注册表取。

### 1.2 窄接口契约（所有 bridge 的输出统一满足）
eval 引擎实际消费（hasattr 探测，缺省安全）：
```python
add_memory(user_input=, agent_response=, user_id=, timestamp=)   # 逐 turn 写
retrieve_memory(query, user_id, top_k=5) -> str                    # 检索文本（拼进框架统一 prompt）
ingest_history(path, uid) / ingest_history_text(text, uid)         # VehicleMemBench 可选批量灌入
delete_memoryos_user(uid)                                          # 样本级清理（等价平台规划 reset(namespace)）
```

### 1.3 已定设计点
- **D1 作答口径**：所有系统只出检索文本，作答统一走框架 LLM。MemoryOS 不用 `get_response`（自带作答破坏公平），直接调其 retriever 取上下文。
- **D2 跨环境**：bridge 一律**进程内 import + 各系统独立 venv 由后续按需引入**（mem0 依赖与 eval 环境兼容性优先验证；LightMem 的 LLMLingua-2/GPU 依赖重，其 bridge 走 qdrant 直读复用已有库，add 侧独立进程跑）。
- **D3 库隔离**：每 (run, user_id) 独立存储路径/namespace，测完 `delete_memoryos_user` 清理。
- **D4 mem0 用官方库**（`systems/mem0`），fork 版 DesayMem_mem0 后续单列 `desaymem-mem0`。

## 2. 施工日志（按步追加）

（每步格式：**日期 | 改动文件:行 | 做了什么 | 为什么 | 验证方式**）

### Step 1 | 2026-09-13 | eval 专用环境 | `envs/eval/`（venv, python 3.11.11）

- **做了什么**：`python -m venv` 创建（conda/mamba 离线不可用）；装 openai/pyyaml/numpy/requests/posthog/qdrant-client/pytz；mem0 从 `systems/mem0` 本地路径 `pip install -e --no-deps` 可编辑安装（源码改动即时生效），逐个补缺依赖直到 import 通过
- **为什么**：平台规划 §4.2 每系统独立环境；eval 框架与 mem0 依赖兼容共一个 env，LightMem/StructMem 后续各用各的
- **验证**：`from mem0 import Memory` OK

### Step 2 | 2026-09-13 | models.yaml | `config/models.yaml` 追加 `memory-llm` 条目

- **做了什么**：本地端点条目——LLM `http://127.0.0.1:20140/v1`（memory-llm，Qwen3-32B 已带 --reasoning-parser qwen3）+ embedding `http://127.0.0.1:20141/v1`（bge-m3，1024 维），key boluoboluomi
- **为什么**：多系统对比公平性红线——所有系统共用同一 LLM/embedding 端点；本地离线可跑
- **验证**：20141 embeddings 探测返回 1024 维向量

### Step 3 | 2026-09-13 | 注册表模块 | 新建 `evalcore/memory_bridges.py`（核心改动）

- **做了什么**：
  - `Mem0Bridge` 类：封装 mem0 官方 `Memory`，实现窄接口契约——`add_memory`（构造 [user,assistant] 消息对，`infer=True` 保留 mem0 自主抽取+add/update/delete 决策）、`retrieve_memory`（`search(query, filters={'user_id'}, top_k)` 拼文本）、`delete_memoryos_user`（`delete_all(user_id)`）
  - `BRIDGES` 注册表 + `build_memory_system()` 统一入口；原 desaymem 硬编码逻辑整体迁入（含本机缺 edge_memory 的显式报错与可用列表提示）
- **为什么**：注册表替代 if-else（§1.1），新系统只加一个 builder + 一行注册
- **验证**：`MEMORY_SYSTEMS` 导出 `['none', 'mem0', 'vehiclemem']`

### Step 4 | 2026-09-13 | eval_engine.py | `_build_memory_system` 委托注册表

- **改了什么**：头部 import 注册表、删孤立 `DESAYMEM_ALIASES`；旧函数体（硬编码 edge_memory + DesayMem config ~43 行）替换为 4 行委托；模块 docstring 更新
- **为什么**：引擎不再感知具体系统，扩展点收敛到注册表
- **验证**：`import evalcore.eval_engine` OK，调用链 `_build_memory_system → _build_from_registry → BRIDGES[name]`

### Step 5 | 2026-09-13 | run.py | choices 从注册表取

- **改了什么**：`MEMORY_SYSTEMS` 硬编码列表 → 从 memory_bridges import；help 文本更新
- **验证**：`run.py --help` 显示 `--memory-system {none,mem0,vehiclemem}`

### Step 6 | 2026-09-13 | Mem0Bridge 两个 bug 修复

1. **from_config 类型错误**：`Memory.from_config()` 内部 `MemoryConfig(**config_dict)`（main.py:733）要求 dict 而非实例 → bridge 构造改为纯 dict 字面量（llm/embedder/vector_store 三段）
2. **Qdrant 本地模式单实例锁**：mem0 在 `~/.mem0/migrations_qdrant` 建全局库，异常退出残留锁 → 后续实例 `RuntimeError: already accessed`。修复：清 `~/.mem0/migrations_qdrant`；正常评测路径靠 delete_memoryos_user 清数据，锁残留仅崩溃后发生（运维知识，记入文档）
- **验证**：端到端单测通过——add("I live in Paris...") 后 search("Where does the user live?") 返回 `['User lives in Paris', 'User loves hiking on weekends']`（LLM 抽取 + bge-m3 检索全链路）

### Step 7 | 2026-09-13 | mem0 × locomo 端到端冒烟（进行中）

- **命令**：`run.py --dataset locomo --memory-system mem0 --model-config memory-llm --sample-limit 1`
- **观察**：memory_build 逐 turn 调 mem0 add（每 turn 一次 LLM 抽取 + Qwen3 思考），单对话 ~600 turn 预计 1-2 小时；spaCy/fastembed 未装为无害警告（BM25/lemma 特性关闭，不影响核心链路）
- **系统差异备注**：mem0 是"每 turn 全量上下文重抽取 + 增量决策"（ADD/UPDATE/DELETE 事件驱动），无显式 offline_update 阶段——效率账单中 memory_build 的 calls 将显著高于 LightMem，属真实系统差异



### Step 8 | 2026-09-13 | 冒烟第一轮 F1=0.00% 排障（三连环）

**现象**：冒烟跑通全流程（build→QA→报告），但 Token-F1=0.00% (0/20)。

**排障过程与三个根因**：

1. **伪 bug：检索"空结果"** —— 提前手工验证时 3 条 QA 检索全空。真相：探针传的 `user_id='eval_mem0'`（collection 名），而 engine 给样本的 ID 是 `user_0`。用 `user_0` 复验后 3/3 全部召回（LGBTQ/sunrise/psychology 题）。**教训：bridge 探针必须用 engine 同款 uid 生成规则（`f"user_{idx}"`）**
2. **伪 bug：主库 0 条记忆** —— 冒烟结束后 `results/mem_data/.../storage.sqlite` points=0。真相：eval_engine.py:345 样本收尾调 `delete_memoryos_user(uid)` → mem0 `delete_all(user_id)` 清库，**符合设计**（对话隔离+测完清理）。排查时应看 build 过程中的库或提前备份副本
3. **真 bug：作答 LLM 返回空串 → F1 全 0** —— QA 阶段 20 次调用每次平均仅 180 prompt token、completion 191 token 但 pred 全空。根因：Qwen3 思考型 + 作答 `max_tokens=200`（eval_engine.py:324），思考过程耗尽 200 token，`content=""`。与 LightMem search 同源问题

**修复（两文件）**：
- `evalcore/llm_client.py`：`chat()` 加 `disable_thinking` 参数，True 时注入 `extra_body={"chat_template_kwargs": {"enable_thinking": False}}`（与 LightMem search 同款开关；judge 刻意不关——思考利于判分）
- `evalcore/eval_engine.py:324`：作答调用改为 `disable_thinking=True`

**验证**：副本库+3 题 QA 复现，pred 非空（"Caroline attended an LGBTQ support group meeting around September 12, 2026." 等）。低 F1 值本身是题目难度+记忆时间偏差（mem0 抽取时把相对日期写成系统当前日期 2026），非管线问题

**附录：探针工具链（复用）**——Qdrant 本地库锁（portalocker）需 `rm <storage>/.lock` + 临时 `HOME` 隔离 migrations 锁；库副本可直接 cp -r 后独立打开读取（points 为 pickle 二进制，需 qdrant_client 环境反序列化）

### Step 9 | 2026-09-13 | 冒烟提速: --build-limit 参数

**问题**：冒烟慢的瓶颈是 memory_build（整对话 ~600 turn 逐条 add，每 turn 一次 LLM 抽取，1-2 小时），而 qa_limit 已裁到 20 题。QA 题问的往往是后段对话，全量 build 对"验证管线是否跑通"没必要。

**改了什么**：
- `run.py`：新增 `--build-limit N`（0=全部），透传 run_evaluation
- `eval_engine.py`：run_evaluation 加 `build_limit` 形参；ingest 循环 enumerate 后 `_bi >= build_limit` 即 break

**用法**：`run.py --dataset locomo --memory-system mem0 --model-config memory-llm --sample-limit 1 --build-limit 50` → build 只跑前 50 轮（约 5 分钟），管线验证从 1-2 小时缩到 ~10 分钟内。

**注意**：build_limit 只用于管线验证，出的 F1 无意义（记忆不全）；出数对照仍需全量 build（或 --build-limit 0）。

### Step 10 | 2026-09-14 | LightMem bridge（qdrant 直读复用全量库）

**设计**（落实 D2 决策）：不加载 LightMem 完整管线（llmlingua/cuda/spacy 重依赖），bridge 直接读 `data/lightmem/qdrant_post_update/` 下 add_locomo.py 产出的全量库（conv-26/30/41/42/43/44/47/48/49/50 + _summary）。

**改了什么**（`evalcore/memory_bridges.py`）：
- 新增 `LightMemBridge`：
  - `add_memory` = no-op（全量库由 LightMem 自身实验离线跑好，build 成本由原实验承担，评测零 add）
  - `retrieve_memory` = sqlite 直读 points（pickle 反序列化 payload+vector）→ vLLM bge-m3 API 算 query embedding → 纯 Python cosine top-k → `"- [ts speaker] memory"` 文本
  - `delete_memoryos_user` = no-op（复用的库不能删）
  - user_id→collection 映射约定：`user_N` → 排序后第 N 个 conv-*
- BRIDGES 注册 `"lightmem"`

**两个小坑**：
1. vLLM embedding 端点要求 `encoding_format="float"`（否则 400）
2. nohup 后台跑 stdout 全缓冲，日志延迟写入——正常，进程在跑

**验证**：
- conv-26 直读 638 条记忆，向量 1024 维
- 试检 "When did Caroline go to the LGBTQ support group?" → 召回含 "attended an LGBTQ support group yesterday (2023-05-07)"（gold: 7 May 2023，质量优于 mem0 同题）
- 10 样本冒烟（200 题）跑通见结果目录

**公平性说明**：lightmem 的 memory_build token 不计入本框架账单（库是离线预建的）；与 mem0 全量对比时需注明此差异，或后续补 add 记账。

### Step 11 | 2026-09-14 | QA prompt 换 LoCoMo 官方 baseline 模板（公平性保持）

**为什么改**：LightMem 原生管线同库跑出 F1 0.396，统一赛场极简 prompt 只有 0.097。差距主要在作答环节——极简模板没有时间推理指令和说话人信息，LoCoMo 一半考题是 temporal/multi-hop。

**改了什么**：
- `evalcore/eval_engine.py` `_qa_prompt` locomo+use_memory 分支：换 LightMem ANSWER_PROMPT 同源模板（官方 baseline）。关键指令：相对时间按记忆时间戳换算绝对日期（例：4 May 2022 记忆里的 "last year" = 2021）、答案 ≤5-6 词、无答案回 "no information available"；注入对话双方名字
- `adapters/locomo_adapter.py` `get_queries`：每题附 `speakers=[speaker_a, speaker_b]`（官方 prompt 需要）

**公平性**：prompt 对所有系统统一（mem0/lightmem/后续 structmem 一视同仁），不构成偏袒。

**验证**（lightmem 10 样本 200 题前后对照）：
- Token-F1 9.72% → **16.77%**（+7pt）
- temporal 7.88% → 13.96%（+6pt，时间换算指令直接命中）
- multi_hop 12.40% → 19.96%
- 过半率 5.50% → 16.50%

**结论**：管线可信，统一赛场基线落到正常区间。mem0 需用新 prompt 重跑对照（旧 13.19% 是极简 prompt 下的数，不可比）。

### Step 12 | 2026-09-14 | Summary 库同步 + StructMem bridge

**前置修复**：全量 run 的 Step 1.5（summary pre→post 同步）未随手动 add 执行——post_update 的 10 个 `_summary` collection 全是空库（12KB），272 条真摘要留在 qdrant_pre_update。手动 cp 同步到位（每对话 19~32 条）。

**改了什么**（`evalcore/memory_bridges.py`）：
- `StructMemBridge(LightMemBridge)`：继承直读机制，`retrieve_memory` 合并 entries + summaries 两层候选（summary 行标注 `[summary {time_range}]`），按统一 cosine 相似度排序截断 top_k
- BRIDGES 注册 `"structmem"`

**验证**：试检同题召回正常（entries 为主，summary 相似度高时进入前 k）；10 样本 200 题对比跑见 results/smoke_structmem。

**运维注**：lightmem/structmem 共用 qdrant_post_update 库（StructMem 是 LightMem 的 event+summary 配置模式，非独立代码），bridge 层差异仅在检索是否合并摘要层。

### Step 13 | 2026-09-14 | 注册表改名澄清 + Step 12 结果勘误

**勘误**（用户指出）：Step 10/12 的 "lightmem vs structmem" 对比口径错误。全量库（run_id 20260913_053225_full_nothink_structmem，`--extraction_mode event --enable_summary`）本身就是 StructMem 模式建的，两个 bridge 读的是同一份库。因此 16.77% vs 16.78% 实际是「同库、检索是否合并 summary 层」的消融，不是 lightmem vs structmem 系统对比。

**改了什么**（memory_bridges.py 注册表）：
- `"lightmem"` → 删除；`"structmem-nosummary"`（消融入口，单层 entries，即原 LightMemBridge 逻辑）
- `"structmem"` = entries+summary 双层
- 真 lightmem（flat 抽取模式）需另建库（add_locomo.py --extraction_mode flat，13 小时级）后另行接入

**消融结论**：summary 层在 top_k=5 下贡献 ≈ 0（16.78% vs 16.77%，过半率同为 33/200）——summary 向量相似度竞争不过 entries，检索预算是瓶颈而非记忆质量。

### Step 14 | 2026-09-14 | 三数据集适配启动 → 硬阻塞确认

详见姊妹文档《三数据集适配调研与施工记录.md》(docs/)。要点：
- 三数据集数据文件均为 LFS 指针未拉取（git-lfs 未装 + 外网不通 + 无本地缓存），数据本体不可得
- adapter/评分/QA prompt 三层代码均已就绪（LoCoMo 同款窄接口），唯一悬置：CarMem 三阶段协议可能需扩展 bridge 接口（extraction/maintenance 非 QA 式）
- vehiclemembench history 50 文件真实可用，仅缺 qa（6.6KB×50），列为数据到位后第一优先
