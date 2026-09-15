# LightMem / StructMem LoCoMo 全量测试指南

> 适用仓库：`/data/pengshuang/memory-benchmark/systems/LightMem`
> 环境：`/data/pengshuang/memory-benchmark/envs/lightmem`
> 服务：LLM `http://127.0.0.1:20140/v1`（model `memory-llm`，**上下文 16384**，key `boluoboluomi`）；Embedding 本地 `bge-m3`（1024 维，GPU）
> 相关：车机多数据集统一评测框架（LoCoMo/LongMemEval/CarMem/VehicleMemBench）见 [VehicleMemBench数据集详解](VehicleMemBench数据集详解.md)——LoCoMo 官方口径为 Porter-stem Token-F1，最终对外报告建议统一用该框架 metrics/official.py

---

## 1. 原理：LightMem 记忆管线（构建阶段，`add_locomo.py`）

LightMem 模拟人类三阶记忆，把长对话流式写入记忆库。**StructMem = LightMem + 跨事件摘要**（`--extraction_mode event --enable_summary`）。

```
原始对话消息（逐 turn）
  → MessageNormalizer 规范化（补 speaker_name / session_time / weekday）
  → PreCompressor 预压缩（LLMLingua-2-x 709MB 本地模型，压缩率 0.6，GPU）
  → SenMemBufferManager 感觉缓冲（user 消息按 token 攒，≥512 token 触发 TopicSegmenter 语义切分；
      两阶段边界：粗切 + bge-m3 相邻 turn 余弦相似度 <0.2~0.5 微调）
  → ShortMemBufferManager 短时缓冲（攒 topic 段，触发阈值后成批送去抽取）
  → MemoryManager（OpenaiManager）LLM 双路抽取（event 模式）：
      factual 路：逐消息抽"全部事实"（过去事件/未来计划/观点偏好/具体实体）
      relational 路：抽说话人之间的关系与互动
      两路结果 merge 成 MemoryEntry（memory 文本 + 时间戳 + speaker + topic_id + sequence 等元数据）
  → Qdrant 本地向量库（bge-m3 1024 维，目录模式：qdrant_pre_update / qdrant_post_update）
  → Phase 2.5 StructMem 摘要（--enable-summary）：对每个 (speaker, 时间窗) 聚类种子记忆，
      LLM 生成跨事件摘要，存独立集合 {conv}_summary
  → Phase 3 offline_update：embedding 相似度 >0.9 的记忆对交给 LLM 判断
      合并（merge）/ 删除（delete）/ 忽略（ignore），去重压缩记忆库
```

关键机制（与官方一致）：
- **流式逐 turn 调用**：每个对话 ~19 sessions、每 session ~20 turn，逐 turn 调 `add_memory()`。
- **强制触发**：`force_segment/force_extract` 只在**整个对话最后一个 turn** 传 True（`add_locomo.py:328`），把残余缓冲切干净。
- **LoCoMo 适配**：每条真实发言转成 `user(内容) + assistant(空)` 消息对，说话人记录在 `speaker_name`；抽取时 `messages_use="user_only"` 只取 user 侧。
- **时间窗摘要**：`--summary_time_window 3600`（秒）+ `--summary_top_k_seeds 15`（每个聚类取 top15 种子记忆做摘要输入）。

## 2. 输入

| 项 | 值 |
|---|---|
| 数据集 | `datasets/VehicleMem-Eval/datasets/locomo/locomo10.json`（官方 LoCoMo 10 对话，~200 QA/对话） |
| 冒烟 | `data/lightmem/locomo_smoke1.json`（仅 conv-26 单对话） |
| 配置（add_locomo.py 头部） | `LLM_MODEL='memory-llm'`、`API_BASE_URL='http://127.0.0.1:20140/v1'`、`EMBEDDING_MODEL_PATH='/data/pengshuang/desaymem/models/bge-m3'`、`EMBEDDING_MODEL_DIMS=1024`、`LLMLINGUA_MODEL_PATH` 本地路径、`max_tokens=8192`（**勿改回 16000**，服务上下文 16384 会 400） |
| 必备环境变量 | `TIKTOKEN_CACHE_DIR=/data/pengshuang/memory-benchmark/tmp/tiktoken_cache`（离线 tiktoken：cl100k_base + o200k_base） |

## 3. 全量构建命令（Step 1：add）

改 `add_locomo.py` 头部 `DATA_PATH` 指回全量：
```python
DATA_PATH = '/data/pengshuang/memory-benchmark/datasets/VehicleMem-Eval/datasets/locomo/locomo10.json'
```

然后：
```bash
cd /data/pengshuang/memory-benchmark/systems/LightMem/experiments/locomo
TIKTOKEN_CACHE_DIR=/data/pengshuang/memory-benchmark/tmp/tiktoken_cache \
nohup /data/pengshuang/memory-benchmark/envs/lightmem/bin/python add_locomo.py \
  --extraction_mode event --enable_summary \
  --summary_time_window 3600 --summary_top_k_seeds 15 \
  --workers 1 \
  > /data/pengshuang/memory-benchmark/logs/experiments/lightmem_full_add_<date>.log 2>&1 &
```

- `--workers 1`：顺序跑 10 个对话（并行会同时加载多份 bge-m3，GPU 显存压力大）。
- 产物（冒烟实测 1 对话的量，全量 ×10 估算）：
  - `data/lightmem/qdrant_pre_update/{conv-xx}`：827 entries/对话
  - `data/lightmem/qdrant_post_update/{conv-xx}`：805 entries/对话（offline_update 后 -22）
  - `data/lightmem/qdrant_post_update/{conv-xx}_summary`：19 条摘要/对话
  - `logs/<timestamp>/conv-xx.log` + `lightmem_<ts>.log`：分阶段耗时、API calls、token 用量（冒烟：364 calls / 47.98 万 token / 23 分钟 → 全量约 **3,600 calls / 480 万 token / 4 小时**）

**验证成功标志**：每个 conv 的 log 末尾 `✓ Add_memory completed: N entries`（N>500）且无 `Error processing API call`。

## 4. 全量评测命令（Step 2：search）

```bash
cd /data/pengshuang/memory-benchmark/systems/LightMem/experiments/locomo
TIKTOKEN_CACHE_DIR=/data/pengshuang/memory-benchmark/tmp/tiktoken_cache \
nohup /data/pengshuang/memory-benchmark/envs/lightmem/bin/python search_locomo.py \
  --dataset /data/pengshuang/memory-benchmark/datasets/VehicleMem-Eval/datasets/locomo/locomo10.json \
  --qdrant-dir /data/pengshuang/memory-benchmark/data/lightmem/qdrant_post_update \
  --output-dir <结果目录> \
  --embedder huggingface --embedding-model-path /data/pengshuang/desaymem/models/bge-m3 \
  --retrieval-mode combined --total-limit 60 \
  --enable-summary --summary-limit 5 \
  --llm-api-key boluoboluomi --llm-base-url http://127.0.0.1:20140/v1 --llm-model memory-llm \
  --judge-api-key boluoboluomi --judge-base-url http://127.0.0.1:20140/v1 --judge-model memory-llm \
  > /data/pengshuang/memory-benchmark/logs/experiments/lightmem_full_search_<date>.log 2>&1 &
```

### 评测总体流程（两阶段，先写后读）

**阶段一 add（写记忆）**：10 个对话（每个 ~19 session × ~20-30 turn）逐 turn 调 `add_memory()` 流入三阶管线；每对话结束时收尾（跨事件摘要 + offline_update 去重），独立产出 `{conv-xx}` 事件库 ~805 条 + `{conv-xx}_summary` ~19 条。

**阶段二 search（读记忆 + 评分）**：add 全部完成后，逐对话、逐 QA 评测（每对话 cat1-4 约 150-180 题）：
1. query 只是 question 文本（bge-m3 编码；原对话内容、多模态 img_url 均不参与）
2. **检索范围限定在该 QA 所属对话的记忆库**——conv-26 的题只查 conv-26 的 805+19 条，不跨对话（process_sample 按样本隔离）。10 个对话 = 10 个独立"用户"的长期记忆
3. top-60 事件 + top-5 摘要拼 prompt → LLM 作答
4. (question, gold answer, prediction) 进 judge 算 J-score；token F1 由 eval_f1.py 离线算
5. gold 来源：cat1-4 用 `answer`；cat5 用 `adversarial_answer` 且默认 `--allow-categories 1 2 3 4` 排除不评

**关于"长期记忆"属性**：add 完成后一次性检索只是评测的工程顺序，并非模拟"一年后"的专门协议——LoCoMo 的 19 个 session 本身跨约一年（带真实日期），时间跨度是数据集天然携带的。cat5 对抗题（题面诱导模型答不存在的事）默认不评，保持与官方口径一致。

**对话隔离的物理实现（10 对话怎么互不干扰）**：`qdrant_post_update/` 只是目录不是数据库，每个对话一套完全独立的 sqlite——`conv-26/collection/conv-26/storage.sqlite`（805 条）+ `conv-26_summary/...`（19 条），全量 10 对话共 20 个独立库。add 时 `load_lightmem(collection_name=conv-26)` 库根即定在各自子目录，写入只进自己的库；search 时 `load_entries('conv-26')` 打开同一子目录。**隔离在物理层完成，不是查了再过滤，是跨对话根本不可见**。

**车机场景适配：assistant 非空回复怎么处理**（2026-09-13 讨论定稿）：

LoCoMo 协议下"assistant 空"是刻意的——记忆主体是说话人本人（user 消息带 speaker_name，两个说话人交替当 user），assistant 空壳只是消息对格式的占位，抽取时被 `user_only` 白名单（openai.py:283 role_filter）完全忽略。车机场景 assistant（车机回复）有内容，适配三策略：

| 策略 | 改动 | 语义 | 适用 |
|---|---|---|---|
| B：仍 user_only（零改动） | 无——用户话当 user（标 speaker_name），车机回复直接丢弃 | 记忆只记用户说的事，与 LoCoMo 协议完全同构 | **推荐起步**：先跑通拿基线 |
| A：hybrid（一行） | `"messages_use": "hybrid"`，并确保 assistant 消息也带正确 speaker_name（如"车机"），否则记忆无法区分归属 | 车机回复也进记忆（导航目的地/播放历史等服务性事实） | 需要车机回复可召回时 |
| C：车机回复独立库 | 新增 collection，类似 summary 库做法 | 车机回复作为独立记忆类型，检索时合并 | A 证明有增益后 |

路线：B 跑通 → A 对比（F1/J 量化车机回复进记忆的增益）→ 视结果考虑 C。**另需提前定义**：车机对话的 session 边界（点火/熄火或天）与时间戳策略（点火时间+turn 递增），直接影响时间窗摘要效果，比消息角色适配影响更大。

**20 个库是怎么来的（懒创建，非预建）**：没有任何"预先建好 10 个对话的库"的步骤——`Qdrant.__init__`（qdrant.py:63）每次实例化调 `create_col`，内部先查同名 collection 存在则跳过（qdrant.py:74-77），不存在才创建。add_locomo.py 每处理一个对话 `load_lightmem(collection_name=conv-xx)` 实例化一次，**首个 turn 写入时该对话的库才诞生**——20 个库是 10 次独立实例化的副产品。

**Qdrant vs PostgreSQL（自研移植版存储选型）**：仓库里的 storage.sqlite 只是 Qdrant **本地模式**的内部存储后端（QdrantClient(path=) 的私有文件，勿直接当业务库用）；Qdrant 也能 server 模式跑（docker，一个进程管上千个 collection，建 collection = 一次调用，"建 10 个库"在 Qdrant 语境下是 10 行循环的成本，不是 10 个数据库实例）。自研版用 PostgreSQL+pgvector 完全可行，推荐**单库 + conv_id 字段过滤**（`WHERE conv_id=... ORDER BY embedding<=>$q LIMIT 60`，HNSW 先过滤后检索，与 LightMem 物理隔离语义等价，F1 可比），不要每对话一 schema。对接评测只需替换 retrievers.py 的 `QdrantEntryLoader`（load_entries/load_summaries 两个方法改为查 Postgres），search 的 prompt/judge/F1/token 记账脚手架全部原样复用。注意避免"先 ANN 后过滤"（post-filter）实现——召回跨对话记忆会污染 F1，这是物理隔离天然免疫的问题。

**产品化部署 vs 评测部署的结构差异（自研移植版对比时注意）**：LightMem 代码假设"每用户一个独立实例"（库路径即用户归属，payload 无 user_id 字段）。产品化形态应是全局 2 个 collection（事件+摘要）+ Qdrant payload filter 按 user_id 逻辑隔离（Mem0 产品线的做法）。两种形态对 LoCoMo 评测等价（物理隔离 ⊇ 逻辑过滤），但物理隔离把"检索时过滤对召回的影响"从评测里移除了——若自研 StructMem 移植版做单库多用户，需明确 top-k 召回发生在"过滤后子空间"还是"全库召回再过滤"，与 LightMem 对比时须保证同构，否则 F1 不可比。

**库内部的话题区分（软区分，非分区）**：单个对话库内 805 条所有话题混存一个 sqlite，区分靠检索时的向量相似度（bge-m3 top-60，"问野营捞野营"），是向量空间的连续划分而非预分桶。每条记忆 payload 带 `topic_id`（TopicSegmenter 切分标签，冒烟库 topic 0~21+）但**只是元数据，不参与检索过滤**——想按 topic 硬分区检索需自写 Qdrant filter，LightMem 设计哲学是"全库向量检索+语义召回"。摘要库（top-5）作为话题级压缩视图先给 LLM 全景，再叠事件细节。

### 评测原理（`search_locomo.py`）——单题内部细节

对每条 QA：
1. **检索**：用 `bge-m3` embed 问题，在 Qdrant `{conv-xx}` 集合中 combined 模式取 top-60 记忆条目（跨说话人合并），同时在 `{conv-xx}_summary` 取 top-5 摘要。
2. **组装 prompt**（`ANSWER_PROMPT_StructMem`）：摘要 + 记忆条目 + 问题，要求"5-6 词以内简短回答"。
3. **生成答案**：LLM `memory-llm`，temperature 0。
4. **LLM Judge**（`llm_judge.py:ACCURACY_PROMPT`）：把 (question, gold answer, generated answer) 交给 judge 模型，宽松标准——"只要 touch 到同一主题即 CORRECT"；时间题允许格式差异。返回 `{"label": "CORRECT|WRONG"}`。
5. **统计**：`judge_correct` 取 0/1，按 category 1/2/3/4 分桶算 mean±std。

输出：
- `sample_{conv-xx}.json`：每题的 prediction / reference / category / retrieved_count / speaker_distribution / token_usage
- `summary.json`：
  - `aggregate_metrics.overall.judge_correct.{mean,std,count}` —— **总准确率**
  - `aggregate_metrics.category_{1..4}.judge_correct.{mean,std,count}` —— 分题型准确率
  - `token_statistics`：prompt/completion/total tokens、api_calls、平均值
  - `retrieval_statistics`：avg_summaries_per_question
  - `config`（记录 method: "structmem"、total_limit 60、summary_limit 5 等）

### 评价指标说明（双指标体系：J-score + token F1）

评测采用 **LLM Judge（J-score）为主、token F1 为客观锚点**的双指标体系，两者交叉验证结论才可信。

**一句话总览**：

| 指标 | 怎么算 | 代表什么 | 会怎么失真 |
|---|---|---|---|
| J-score | judge LLM 看 (问题, gold, 答案) 判 CORRECT/WRONG，取比例 | **语义层面的答对率**（贴近人眼判断，可与 Mem0/Zep/MemoryOS 等公开结果横向比较） | judge 有随机性；宽松标准会放行蹭主题的答案；换 judge 模型可漂 5-10 点 |
| token F1 | 答案/gold 各自切词去重成集合，算集合交并的 P/R 调和平均 | **表面词汇重叠度**（完全客观可复现，与 MemoryOS 官方 eval 同口径） | 不认同义改写（"Paris"≠"France's capital"→0）；罚长答案、奖励碰词的错答案 |

**一个具体例子贯穿两个指标**：gold="A shell necklace"，pred="She got a beautiful shell necklace in Hawaii"：
- J-score：judge 认为触及同一主题（shell necklace）→ CORRECT（1 分）
- token F1：pred 词集 {she,got,a,beautiful,shell,necklace,in,hawaii}，gold 词集 {a,shell,necklace}；P=2/8、R=2/3 → F1≈0.47
- 同一个答案，两个指标给出"对"和"对得不干净"两个互补评价——这就是双指标的意义

#### 指标 1：LLM Judge 准确率（J-score，主指标）

公式与含义：
```
J = (1/N) * Σ 1[judge(q_i, gold_i, pred_i) == CORRECT]

1[·]      指示函数：judge 判 CORRECT 记 1，WRONG 记 0
judge     LLM 判分器（llm_judge.py），输入 (question, gold, generated)，
          按"宽松语义"标准打二值分：只要答案 touch 到 gold 的同一主题即 CORRECT；
          时间题允许格式差异（"May 7th" == "7 May"）
N         可评题数（cat1-4，共 1,540 道；cat5 无 gold 默认排除）
```
含义：**语义层面的"答对率"**，与业界（Mem0/Zep/MemoryOS 后续工作）的 J-score 口径一致，分数可与公开结果横向比较。

#### 指标 2：Token F1（客观锚点）

公式与含义（与 MemoryOS evalution_loco.py / snap-research 官方口径一致，词集合级）：
```
tokens(x)  = 小写化后按 \b\w+\b 切词并去重成集合
P = |tokens(pred) ∩ tokens(gold)| / |tokens(pred)|    精确率：答案里的词有多少命中 gold
R = |tokens(pred) ∩ tokens(gold)| / |tokens(gold)|    召回率：gold 里的词答案覆盖了多少
F1 = 2·P·R / (P + R)                                   调和平均：同时惩罚多答与漏答
```
含义：**表面词汇重叠度**。完全确定、可复现、无 judge 随机性，但只认字面不认语义。

#### 为什么必须双指标（实测证据）

两指标各有系统性盲区，单看任何一个都会误判：

| 失真类型 | 实测案例（来自评测真实数据） |
|---|---|
| F1 惩罚语义正确的答案 | sys="Maria's aunt" vs gold="Her aunt" → F1=0.40 但语义完全对 |
| F1 惩罚简洁答案（LoCoMo 要求 5-6 词作答，F1 天然吃亏） | sys="Yes" vs gold="Yes, since she collects classic children's books" → F1=0.22 |
| F1 奖励错误答案 | sys="6 May 2023" vs gold="August 4, 2023" → F1=0.33（日期完全错） |
| F1 奖励不完整答案 | sys="kickboxing" vs gold="Weight training, Circuit training, Kickboxing, yoga" → F1=0.33（答 1/4） |
| J-score 有 judge 随机性 | 判分模型非确定（temp=0 仍有漂移）；换 judge 模型分数可漂 5-10 点；答题模型兼任 judge 会自我印证偏差 |

J-score 的不稳定性可用重复测量压制：阿里 Hologres 官方协议跑 5 轮取均值，实测整体波动仅 0.71 个百分点。建议重要对比跑 3 轮取均值±std。

#### 冒烟实测双指标（conv-26，StructMem 模式，152 题）

**v2（服务已开 reasoning-parser 后的真值，2026-09-12 复测）**——预测文本无思考污染后：

| 题型 | n | F1 | J-score |
|---|---|---|---|
| cat1 多跳 | 32 | 0.326 | 0.500 |
| cat2 时间 | 37 | 0.605 | 0.838 |
| cat3 开放域 | 13 | 0.232 | 0.769 |
| cat4 单跳 | 70 | 0.451 | 0.771 |
| **Overall** | 152 | **0.443** | **0.730** |

v1（旧值，J-score 被思考泄漏虚高，作废）：overall J=0.921 / F1=0.443。**v1 的 J 为什么虚高**：旧服务未开 reasoning parser，prediction 带着 ```think 全部推理过程传给 judge，judge 在推理文本里"摸到"答案主题即判 CORRECT；修复后 judge 只看到纯答案，回落到 0.730。F1 两版一致（0.443）因为 eval_f1.py 的 clean_prediction 早已剥掉思考再计算。**结论：J=0.921 不可信，报告基线应使用 v2。**

cat2 时间题 F1 反常高（gold 多为短日期、词集合小易重合）；cat3 开放域 F1 最低（gold 是长描述性答案）。另实测 Qwen3-32B 未严格遵守"5-6 词以内"作答约束（部分 prediction 达 338 字符），加剧 F1 precision 稀释，属模型指令遵循问题而非 bug。

#### F1 计算脚本（eval_f1.py）

离线对 search_locomo.py 产出的 sample_*.json 批量计算，不重跑评测：
```bash
cd experiments/locomo
python eval_f1.py --results-dir <search输出目录> --output <目录>/f1_summary.json
```
脚本内置 `clean_prediction()`：剥离本地 vLLM（Qwen 系未开 reasoning parser）输出中的 ```think 思考前缀，取末行短答案再算 F1。**不开清洗时 F1 会被思考文本污染到 0.063**（precision 被几百个思考词稀释），这是本地服务未开 reasoning parser 的连锁影响——如服务侧修复，此清洗为无害冗余。

### 评价指标说明

- **唯一指标是 LLM-as-Judge 准确率**（judge_correct mean）。**没有**官方 LoCoMo 协议的 F1 / BLEU / 槽位 F1 实现——本仓库自带的 judge prompt 是从官方仓库抄的宽松版判分提示词，但打分模型换成了本地 `memory-llm`，不是官方的 GPT-4o。
- category 1-4：官方定义 1=单跳事实、2=多跳推理、3=时间推理、4=开放域；**category 5（adversarial）默认跳过**（`allow_categories` 不含 5；若要测 adversarial 需显式传 `--allow-categories 1 2 3 4 5`，且脚本用 `adversarial_answer` 作 gold）。
- 严格复现论文的话需要外加官方 eval 脚本（F1/BLEU），本仓库不含。

## 5. 与官方 LightMem 的对应关系（结论：对得上）

| 环节 | 官方 LightMem/StructMem | 本地配置 | 一致性 |
|---|---|---|---|
| 抽取模式 | event 双路（factual+relational） | 同 | ✅ |
| 预压缩 | LLMLingua-2（rate 0.6） | 同模型同参数（本地 709MB） | ✅ |
| 感觉缓冲 | 512 token + TopicSegmenter | 默认参数未动 | ✅ |
| 摘要（StructMem） | 时间窗聚类摘要 | `--summary_time_window 3600 --summary_top_k_seeds 15`（上游 README 默认值） | ✅ |
| offline_update | 相似度 >0.9 LLM 合并/删除 | 同 | ✅ |
| 评测 | LoCoMo LLM judge（官方 eval 脚本另有 F1/BLEU） | 同 judge prompt；LLM/judge 换成本地 memory-llm；无 F1/BLEU | ⚠️ judge 模型不同→绝对值与论文不可直接比，但系统间横向对比公平 |
| LLM | 论文用 GPT-4o-mini 级 | memory-llm | ⚠️ 同上 |
| Embedding | 论文 text-embedding-3-small(1536) | bge-m3(1024) | ⚠️ 影响检索质量，系统间公平 |

**总结**：管线结构、prompt、默认参数与上游完全一致（属同仓库脚本），唯一替换是 LLM/Embedding 服务指向本地，因此适合作为 StructMem 自研移植版的"上游同配置基线"，但绝对分数不能与论文数字直接比较。

## 6. 已知坑（本次踩过）

1. **`max_tokens=16000` 会静默失败**：服务上下文 16384，prompt>384 token 即 400，异常被 `process_segment_wrapper` 吞掉 → 0 记忆。已改为 8192。
2. **tiktoken 离线**：必须设 `TIKTOKEN_CACHE_DIR`，缓存文件名是编码 URL 的 sha1，内容需 sha256 校验（cl100k_base、o200k_base 已备好）。
3. **spacy**：`retrievers.py` import 了但未用，直接 pip 装即可（不需语言模型包）。
4. **思考前缀污染（已根治）**：vLLM 2026-09-12 起加 `--reasoning-parser qwen3` 重启，思考剥离进 reasoning_content，content 为纯答案。修复前危害比预想大：F1 0.063→0.443（污染），**且 J-score 虚高 0.921→0.730（judge 读到推理过程即放行）**。eval_f1.py 的 clean_prediction 保留作无害冗余。服务启动命令模板见 §9。
5. **Write/Edit 工具失败时**用 bash heredoc / python 字符串替换改代码。

## 7. 结果归档（平台规范）

按 `docs/Memory-Benchmark平台建立与目录规划.md`：
```
results/raw/<run_id>/         # run_id 格式 YYYYMMDD_HHMMSS_<系统>_<模型>_<数据集版本>
├── summary.json              # search 输出（改名或复制）
├── sample_*.json
├── add 构建日志 + token 统计
└── manifest.json             # 记录配置、服务地址、模型版本、代码 commit
```

## 8. 一键流水线（run_full_pipeline.sh，推荐入口）

`experiments/locomo/run_full_pipeline.sh` 串联全流程：**清库 → add（构建记忆）→ 摘要同步 → 镜像导出 → search（评测）→ F1**。**所有评测都应从这一条命令进入**，不要手工分步（除非调试）。

### 8.1 用法速查

```bash
cd /data/pengshuang/memory-benchmark/systems/LightMem/experiments/locomo

# 全量基线（10 对话，清库重建，约 5 小时）
./run_full_pipeline.sh

# 全量 + 关思考（答案生成不带推理，快 7 倍，约 add 4h + search 40min）
./run_full_pipeline.sh --no-think

# 冒烟（conv-26 单对话，约 25min add + 50min search）
./run_full_pipeline.sh --smoke

# 复用已有记忆库，只重跑评测 + F1（调试评测侧用）
./run_full_pipeline.sh --smoke --skip-add

# 组合：复用库 + 关思考快速对照
./run_full_pipeline.sh --smoke --skip-add --no-think
```

| 参数 | 作用 |
|---|---|
| `--smoke` | 只跑 conv-26 单对话（冒烟验证），数据集自动切 locomo_smoke1.json |
| `--skip-add` | 跳过清库和 add，直接用现有库跑评测（库不干净时慎用） |
| `--no-think` | search 答案生成传 `chat_template_kwargs={"enable_thinking": false}` 强制 Qwen3 关思考（见 §8.2） |

### 8.2 思考开/关对照（--no-think）

**背景**：上游评测脚本按 GPT-4o-mini 等非思考模型设计，无任何思考开关；OpenaiManager 的请求参数（openai.py:100）也传不了 `enable_thinking`。用 Qwen3-32B 跑等于默认开思考，无法从管线侧关闭。本仓库补了 `--disable-thinking`（search_locomo.py），通过 vLLM 的 `chat_template_kwargs` 在**请求级**关思考。

**开关边界（重要）**：
- ✅ 影响：search 的**答案生成** LLM（每题 1 次调用）
- ❌ 不影响：**add 阶段全部 LLM 调用**（抽取/摘要/去重走 OpenaiManager，无开关，思考照常生成——add 耗时无法以此提速）；**judge 判分**（评测外部组件，不在记忆系统成本内，刻意不关）

**实测对照（conv-26 冒烟，152 题，同一含污染摘要的库）**：

| 指标 | 开思考 | 关思考 | 结论 |
|---|---|---|---|
| Overall J | 0.776 | 0.665 | 思考带来 +11 pt 语义答对率 |
| Overall F1 | 0.281 | 0.354 | 关思考答案短而聚焦，precision 高 |
| cat1 多跳 J | 0.656 | 0.375 | **多跳推理高度依赖思考（-28 pt）** |
| cat3 开放域 J | 0.769 | 0.846 | 开放域靠检索质量，不靠推理 |
| prediction=None | 9 题（思考超 8192 截断） | 0 题 | 思考超限是隐性丢分源 |
| 单题 completion | ~918 token（思考占 85%） | 18 token | -98% |
| 单题耗时 | ~36 s | ~5 s | -86% |

**使用建议**：调试迭代 / 快速回归用 `--no-think`；出正式基线数字用开思考。与 GPT-4o-mini 系结果横向比较时，关思考的数字更接近其真实条件（非思考模型）。**横向对比公平性：对比双方必须用相同思考设置**。

### 8.3 流水线各步骤

- **Step 0 清库**：`rm -rf` 两个 qdrant 目录 + add 脚本 logs——防旧数据噪声、防 add_locomo.py 按"目录已存在"误判跳过。**会删掉所有历史记忆库，冒烟产物请去 results/ 找副本**
- **Step 1 add**：`LOCOMO_DATA_PATH` 环境变量注入数据路径（add_locomo.py:36 已改为 `os.environ.get`，无需改源码切 smoke/full）；`--workers 1` 顺序跑防 GPU 显存挤爆
- **Step 1.5 摘要同步**：修复上游 bug（§9），把 pre_update 侧摘要真数据复制到 post_update 的 fallback 读取点，并删遮蔽空库
- **Step 2 镜像导出**：dump_memory_mirror.py（§10）
- **Step 3 search**：评测（参数固定 combined/60/summary 5）
- **Step 4 F1**：eval_f1.py 离线计算

### 8.4 产物目录

`results/<时间戳>_<smoke|full>[_nothink]_structmem/`：

```
├── summary.json      # J-score 聚合（分 category）+ token_statistics + config
├── f1_summary.json   # token F1 分 category + overall
├── f1_report.txt     # F1 表格（人读）
├── sample_conv-*.json# 每题 prediction/reference/category/token_usage
├── memory_mirror/    # 记忆库内容快照（§10）
└── mirror_dump.log
```

## 9. 上游 bug 与修复（.sh 内置，手工跑需知）

**bug：摘要从未进入评测**。`add_loomo.py` Phase 2.5 用 `base_dir=QDRANT_PRE_UPDATE_DIR` 写摘要（add_locomo.py:393），Phase 3 offline_update 只处理事件库，摘要从未同步到 post_update —— search 只读 post_update → **--enable-summary 全程空转**（冒烟两次 "Retrieved 0 summaries" 的真相）。19 条摘要实际躺在 pre_update 四层深目录里。

**附带发现**：这 19 条摘要生成于 vLLM 修复前，`summary` 字段 19/19 被思考文本污染（每条 4-5k 字符以 ```think 开头的推理草稿，真实摘要被淹没）。修复读取路径后这些脏摘要会进入评测 prompt —— **必须清库后用修复好的服务重新 add**（.sh Step 0 + Step 1 自动完成）。

**服务启动命令模板**（Qwen3 系必带 reasoning parser，否则思考文本污染下游一切文本字段）：
```bash
CUDA_VISIBLE_DEVICES=4 nohup vllm serve /data/pengshuang/desaymem/models/Qwen3-32B \
  --served-model-name memory-llm --host 0.0.0.0 --port 20140 --api-key "$VLLM_API_KEY" \
  --dtype bfloat16 --tensor-parallel-size 1 --gpu-memory-utilization 0.84 \
  --max-model-len 16384 --max-num-seqs 3 --reasoning-parser qwen3 \
  > <log> 2>&1 &
```
验证：chat 请求后 `content` 为纯答案、`reasoning_content` 有思考文本。

**qdrant 双布局陷阱**：lightmem 写入时库根为 `{base}/{collection}`，真数据在 `{base}/{col}/collection/{col}/`；search 的 `QdrantEntryLoader` 先试 `QdrantClient(path=post_update)` 标准布局（读 `{post}/collection/{col}` —— 常是 `collection_entry_count` 误建的空库），失败后 fallback 读 `{post}/{col}/collection/{col}/storage.sqlite`（真数据）。.sh 的同步即把真数据放到 fallback 读取点，并删除遮蔽用的空 collection 目录。

## 10. 记忆镜像（dump_memory_mirror.py）

只读直连 qdrant 本地 sqlite（`points` 表：id TEXT + point BLOB，point 为 pickle 的 PointStruct，payload 含 memory/speaker/topic_id/time_stamp 等全字段），把每个 collection 导出为人可读 JSON：

```
results/<run>/memory_mirror/
├── conv-26.json          # 事件记忆全量（805 条，按时间排序）
├── conv-26_summary.json  # 摘要（修复后应有 19 条）
└── mirror_index.json     # 各 collection 计数 + db 路径 + dump 时间
```

用途：不启动管线即可审查库内容（数据质量、污染、增删改对比 pre/post mirror）。单独使用：
```bash
python dump_memory_mirror.py --qdrant-dir <库目录> --mirror-dir <输出>
```

## 11. 资源消耗记账（LLM calls + 输入/输出/总 token）

**记账原则（已定）**：只记记忆系统自身的资源消耗。judge 判分是评测的外部组件（不在记忆系统里），其调用不进系统成本。

**add 阶段**（add_locomo.py 每 sample 结尾 + main 汇总，写入 `logs/<ts>/conv-xx.log` 与 main.log）：

| 阶段 | 记录字段 |
|---|---|
| add_memory（抽取） | calls / prompt_tokens / completion_tokens / total_tokens |
| summarize（摘要） | 同上（分阶段单列） |
| offline_update（去重） | 同上 |

数据源自 vLLM usage 真实回传（openai.py:136 usage_info），非估算。冒烟实测单对话：364 calls / 47.98 万 token（add+summary+update 合计）。

**search 阶段**（search_locomo.py）：
- 单题：`token_usage{prompt_tokens, completion_tokens, total_tokens}` 逐题写入 sample_conv-*.json
- 汇总：summary.json 的 `token_statistics`（total_prompt/completion/total_tokens、api_calls、各均值）
- **注意**：此账只含 answer 生成的调用；judge 那 152 次同服务调用刻意不计（外部组件）。冒烟实测：152 calls / 131.3 万 token（v3，含污染摘要撑大的 prompt；干净库约为 51.6 万）

**与 MemoryOS 对照**：MemoryOS evalution_loco.py 只记 memory_time，无 token 记录。本管线"分阶段（add 三段）+ 分题（search 单题）"的双粒度记账**优于 MemoryOS**，无需补齐。

**开/关思考的成本口径提醒**：开思考时 completion token 约 85% 是思考（单题 ~918 vs 18），比较不同系统/设置的资源消耗时必须同时注明思考设置，否则不可比。

## 12. 更新记录
- 2026-09-12 初版：管线原理、全量命令、产物清单、评测流程、与官方差异对照、踩坑记录
- 2026-09-12 增补：双指标体系、eval_f1.py、冒烟双指标结果
- 2026-09-12 增补2：reasoning parser 服务侧修复（--reasoning-parser qwen3）；J-score 虚高机制与 v2 真值（0.730）；一键流水线 run_full_pipeline.sh；上游 summary 不同步 bug 及 .sh 修复；记忆镜像 dump_memory_mirror.py；token/LLM calls 记录现状
- 2026-09-13 增补3：--no-think 对照开关（search_locomo.py --disable-thinking + .sh 参数）；思考开/关四象限实测对照表（J -11pt / F1 +7pt / 多跳 -28pt / token -98%）；资源消耗记账原则定稿（judge 为评测外部组件不进系统成本）；.sh 用法速查表与参数边界说明；上游评测脚本无思考开关的考证（按 GPT-4o-mini 设计，OpenaiManager 传不进 enable_thinking）
