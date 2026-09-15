# LoCoMo 数据集详解（locomo10.json）

> 路径：`datasets/VehicleMem-Eval/datasets/locomo/locomo10.json`（2.68MB）
> 本文档随讨论持续更新。创建于 2026-09-12，基于全量实测统计。

## 1. 定位

LoCoMo（Long Conversational Memory）是长对话记忆评测基准：两个说话人跨越数月的零散聊天 + 针对历史信息的 QA，用于考察记忆系统的长时记忆构建与召回能力。本仓库（LightMem 上游）用它做主实验数据集。

## 2. 整体分布（10 个对话全部实测）

```
顶层 = JSON list，10 个对话元素
总计：272 sessions、5,882 turns、1,986 QA（其中 444 道为 cat5 对抗题，无 answer）
```

| 对话 | 说话人 | sessions | turns（min~max） | QA | QA 分类（cat1多跳/cat2时间/cat3开放/cat4单跳/cat5对抗） |
|---|---|---|---|---|---|
| conv-26 | Caroline & Melanie | 19 | 419（15~39） | 199 | 32/37/13/70/47 |
| conv-30 | Jon & Gina | 19 | 369（14~28） | 105 | 11/26/0/44/24 |
| conv-41 | John & Maria | 32 | 663（14~37） | 193 | 31/27/8/86/41 |
| conv-42 | Joanna & Nate | 29 | 629（13~38） | 260 | 37/40/11/111/61 |
| conv-43 | Tim & John | 29 | 680（15~43） | 242 | 31/26/14/107/64 |
| conv-44 | Audrey & Andrew | 28 | 675（13~47） | 158 | 30/24/7/62/35 |
| conv-47 | James & John | 31 | 689（14~40） | 190 | 20/34/13/83/40 |
| conv-48 | Deborah & Jolene | 30 | 681（13~44） | 239 | 21/42/10/118/48 |
| conv-49 | Evan & Sam | 25 | 509（14~33） | 196 | 37/33/13/73/40 |
| conv-50 | Calvin & Dave | 30 | 568（10~43） | 204 | 32/32/7/87/46 |

分布规律：
- session 数 **19~32** 不等（不是固定 19）；每 session 10~47 turn，平均 ~22
- 时间跨度 **约 6~8 个月**（如 conv-26：2023-05-08 → 2023-10-22；conv-43 到 2024-01）；各对话起始年份 2022/2023 不一
- QA 分类：cat4（单跳）恒为最多（≈一半），cat5（对抗）约 20~25%，cat3（开放域）最少（全量仅 96 道）；conv-30 无 cat3
- 可评测题 = 1,986 - 444（cat5 无 answer） = **1,542 道**

## 3. 数据格式逐层说明

### 3.1 顶层（每个对话 6 个键）

```json
{
  "sample_id": "conv-26",
  "conversation": { ... },
  "qa": [ ... ],
  "event_summary":  { ... },
  "observation":    { ... },
  "session_summary": { ... }
}
```
- `event_summary` / `observation` / `session_summary`：官方构建 QA 用的**参考标注中间产物**（逐 session/说话人的观察记录、事件摘要等），LightMem 的 add/search **均不使用**
- LightMem 只用 `conversation` + `qa`

### 3.2 conversation（dict，非 list）

```json
{
  "speaker_a": "Caroline",
  "speaker_b": "Melanie",
  "session_1":   [ {turn}, {turn}, ... ],
  "session_1_date_time": "1:56 pm on 8 May, 2023",
  ...                              ← 共 272 对 session_N / date_time
}
```
- 每个对话 2 个固定说话人贯穿全程
- **session 级时间戳**（非 turn 级）；`parse_locomo_timestamp` 解析 `"(I:%M %p on %d %B, %Y)"` 格式
### 3.5 时间戳机制详解

**数据集侧：只有 session 级时间戳（对话开始时间），没有 turn 级时间。**

原始格式为英文明文（挂在 `session_N_date_time` 键上）：
```
"session_1_date_time": "(1:56 pm on 8 May, 2023)"
"session_2_date_time": "(1:14 pm on 25 May, 2023)"
```
- 这个时间的语义就是**该 session（这段对话）的开始时间**；同 session 内所有 turn 共享它
- session 间隔不规律（3 天~1 个月），跨度 6~8 个月，模拟"想起来才聊"的真实节奏；对话由 Snap 用人设 agent 按预生成事件图（每人 6~12 个月、≤25 个因果相连事件）逐 session 生成，session 日期取自事件图——这是 cat2 时间题能考"5月8日说的'昨天'=5月7日"的基础
- turn 字段只有 `speaker/dia_id/text`（+可选图片字段），无任何时间

**管线侧：session 时间 + 500ms 递增 = turn 伪时间。**

对"session 时间就是对话开始时间，之后每轮加 500ms"的理解基本正确，但有一个精确化：**递增单位是"每条发言（turn）500ms"**，而且因为 LoCoMo 适配把每条发言拆成了 `user+assistant` 两条消息，实际实现是 `sequence_number × 500ms`，即第 i 条发言（从 0 计）的伪时间 = session 开始时间 + i×0.5 秒。冒烟日志实证（conv-26 session_1，1:56 pm）：

```
第 0 条发言  13:56:00.000   ← session 开始时间本身
第 1 条发言  13:56:00.500
第 2 条发言  13:56:01.000
第 3 条发言  13:56:01.500
...（共 419 条 → session 内占 0~几十秒伪时间）
```

四步加工链：

```
"(1:56 pm on 8 May, 2023)"
  │ ① add_locomo.py parse_locomo_timestamp: strptime("%I:%M %p on %d %B, %Y")
  │    → "2023-05-08 13:56:00"，挂在该 session 所有 turn 上
  │ ② MessageNormalizer (lightmem.py): 补 time_stamp(ISO)/weekday/session_time 元数据
  │    （weekday 从日期算出，进抽取 prompt 的 "[时间, 周几]" 前缀）
  │ ③ assign_sequence_numbers_with_timestamps(offset_ms=500): 伪 turn 时间
  │    第 i 条发言 = session时间 + i×0.5s，同时保序、互不重合
  │ ④ float_time_stamp: 转 epoch 数值存入 MemoryEntry payload（Qdrant 数值过滤）
       - offline_update 建候选队列: float_time_stamp <= 自身 → 只找"更早"的记忆
       - 摘要滑窗: [t, t+3600s) 取窗口条目
```

**该机制的缺陷与影响：**
1. **伪时间粒度失真**：一个 session 几十条发言被压进十几秒"假时间"，turn 间只保序不保真实间隔
2. **相对时间推理全靠 LLM 硬算**：记忆条目存绝对时间戳+原文（原文含"昨天"等相对表述），管线无专门的时间归一化模块，cat2（时间题）答对与否取决于抽取/作答两处 LLM 的换算能力——这正是 cat2 公认最难的原因之一
3. **摘要时间窗与真实时间脱节**：`--summary_time_window 3600`（1 小时）滑的是伪时间轴——一个 session 全部记忆挤在十几秒内，一个 3600s 窗口往往横跨多个 session（冒烟日志首个窗口 13:56:00-14:56:00 即如此），窗口边界实际由 session 间隔决定，而非设计意图的"每 1 小时一段"

### 3.3 turn（5,882 个）

| 字段 | 出现次数 | 说明 |
|---|---|---|
| `speaker` | 5,882 | 发言人名（匹配 speaker_a/b 得 speaker_id） |
| `dia_id` | 5,882 | 位置编号，如 `D1:5` = session1 第 5 条 |
| `text` | 5,882 | 发言正文 |
| `blip_caption` | 1,226 | 图片文字描述（BLIP 模型生成） |
| `query` | 888 | 搜图时的检索词 |
| `img_url` | 910 | 图片 URL |
| `re-download` | 206 | 数据集内部标记，忽略 |

多模态 turn 真实示例（conv-26, D1:5）：
```json
{
  "speaker": "Caroline",
  "img_url": ["https://i.redd.it/l7hozpetnhlb1.jpg"],
  "blip_caption": "a photo of a dog walking past a wall with a painting of a woman",
  "query": "transgender pride flag mural",
  "dia_id": "D1:5",
  "text": "The transgender stories were so inspiring! ..."
}
```
- 约 **20% turn 带图**。LightMem 只用 `text` + `blip_caption`，拼成 `"text (image description: caption)"` 送入抽取（event prompt 里有对应的"图片信息并入事实"指令）；`img_url/query` 丢弃

### 3.4 qa（1,986 道）

| 字段 | 出现次数 | 说明 |
|---|---|---|
| `question` / `evidence` / `category` | 1,986 | 必有 |
| `answer` | 1,542 | cat5 之外的 gold answer |
| `adversarial_answer` | 446 | 仅 cat5 |

普通题示例：
```json
{"question": "When did Caroline go to the LGBTQ support group?",
 "answer": "7 May 2023", "evidence": ["D1:3"], "category": 2}
```
cat5 对抗题示例（**无 answer 字段**）：
```json
{"question": "What did Caroline realize after her charity race?",
 "evidence": ["D2:3"], "category": 5,
 "adversarial_answer": "self-care is important"}
```

类别语义（**按官方论文口径，cat1≠单跳！**，2026-09-12 实测全量分布并与官方 benchmark 仓库核对修正）：

| category | 全量数量 | 含义 | 真实例题（来自本数据集） |
|---|---|---|---|
| 1 | 282 | **多跳推理 Multi-hop**：需组合多处信息推理 | "What is Caroline's identity?" → "Transgender woman"（evidence D1:5） |
| 2 | 321 | **时间推理 Temporal**：日期/先后/相对时间（公认最难类） | "When did Melanie paint a sunrise?" → 2022（evidence D1:12） |
| 3 | 96 | **开放域 Open-domain**：对话内容+世界知识/常识推断 | "What fields would Caroline be likely to pursue in her education?" → "Psychology, counseling certification"（evidence D1:9+D1:11 需推断） |
| 4 | 841 | **单跳 Single-hop**：直接检索单一事实（占比最大） | "What did the charity race raise awareness for?" → "mental health"（evidence D2:2） |
| 5 | 446 | **对抗 Adversarial**：诱导题，**无 answer 字段**，只有 adversarial_answer | "What did Caroline realize after her charity race?"（实际参赛的是 Melanie——张冠李戴诱导幻觉，正确行为是拒答/答无证据） |

### 各类别通俗解读（中文详解）

**cat1 多跳推理（282 道）——"拼图题"**
答案不是某一句话直接说的，需要把散落在对话不同位置的两块以上信息拼起来推理。例如问 Caroline 的身份，evidence 指向 D1:5（第 1 个 session 第 5 条发言），系统需要从那段对话里抽取出"跨性别女性"这一身份标签。多跳题的 evidence 常是多个（如 cat3 例题的 ["D1:9","D1:11"]），跨 session 组合。考察的是记忆系统的**信息整合能力**——单条召回不准就会断链。

**cat2 时间推理（321 道）——"时间轴题"**
所有题围绕"何时"展开：具体日期、先后顺序、相对时间换算。难点在于对话里说的常是**相对时间**（"昨天""上个月""去年"），系统必须把它锚定到 session 的绝对时间戳上换算。例如 5 月 8 日的 session 里说"我昨天去了支持小组"，问"Caroline 哪天去的？"正确答案是 5 月 7 日——记得内容不难，难的是**相对时间→绝对日期的换算**。官方论文里这是 LLM 与人类差距最大的类别（低约 73 个百分点），因为向量检索本身没有时间概念，新旧陈述在向量空间里"平权"。

**cat3 开放域（96 道）——"常识+推断题"**
对话里没有现成答案，要结合世界知识或对说话人的整体理解做推断。例如"Caroline 可能会往什么教育方向发展？"——对话里没直接说，gold 是"心理学、心理咨询认证"，需要系统理解 Caroline 的经历（自我认同历程、帮助他人的意愿）再往职业方向推断。数量最少（96 道）因为最难出题、也最难评分。考察记忆系统能否形成对说话人的**整体画像**而不只是事实清单。

**cat4 单跳（841 道）——"直接检索题"**
最基础也最多：答案就在对话某一句话里，找到即答对。例如"慈善跑是为了提高什么意识？"答案 "mental health" 就在 D2:2。考察纯**检索召回能力**——嵌入模型质量、检索排序直接决定得分。LightMem 冒烟里此题类 95.7%，说明原子记忆抽取+向量召回对"找到那句话"已经很强。

**cat5 对抗/诱导（446 道）——"陷阱题"**
专测**幻觉抵抗力**。手法通常是张冠李戴：把 A 的事安到 B 头上、或问一件从未发生过的事。检索"charity race"会正常召回 Melanie 的相关记忆——这恰恰是陷阱：表面看检索成功了，但问题是问 Caroline 的。理想行为是识别"证据不属于问题主体"并拒答（"对话中没有相关记录"）。数据设计上这 446 道全部没有 answer 字段、只有 adversarial_answer（即"被诱导后会答出的错误答案"），官方评测协议默认排除 cat5（不同系统对它的处理口径不一，横向对比时通常只报 cat1-4 的 1,540 道）。

鉴别证据（cat4/cat5 "孪生题"）：cat4 存在 "What did **Melanie** realize after the charity race?"（answer: "self-care is important"）；cat5 把同一题主语换成 Caroline（她没参加过慈善跑）变成诱导题。这印证 cat5 的机制：故意用相似记忆诱导系统答错。

### 3.6 多模态数据详解（约 20.8% 的 turn 带图）

**统计**：全量 5,882 个 turn 中 1,226 个带 `blip_caption`（20.8%），平均每个对话 ~123 张图。生成流程：对话中说话人"分享图片"时，Snap 用检索词（`query` 字段，如 "painting sunrise"、"transgender pride flag mural"）从网上搜图（存 `img_url`），再用 BLIP 模型生成图片的文字描述（`blip_caption`）。**图片本体不在数据集里，只有 URL 和文字描述**——所以评测实际是"文字化的多模态"，系统从不真正看图。

**带图 turn 的完整真实案例（conv-26, D1:5 及上下文）**：

```
D1:3 Caroline: I went to a LGBTQ support group yesterday and it was so powerful.
D1:4 Melanie:  Wow, that's cool! What happened? Did you hear any inspiring stories?
D1:5 Caroline: The transgender stories were so inspiring! I was so happy and thankful
                for all the support.          ← 文本本身不提图片
                [img_url: https://i.redd.it/l7hozpetnhlb1.jpg]
                [blip_caption: "a photo of a dog walking past a wall with a painting of a woman"]
                [query: "transgender pride flag mural"]     ← 搜图用的词，与caption并不严格一致
D1:6 Melanie:  Wow, love that painting! So cool you found such a helpful group.
```
注意两点：① 发言文本与图片描述是**两个独立通道**（文本聊感受，图是"顺手分享"的视觉佐证）；② `query` 和 `blip_caption` 可能对不上（搜"彩虹旗壁画"回来的图是"狗走过画着女人的墙"）——搜图本身就有随机性，caption 如实描述搜回来的图。

**图片信息如何进入 QA（最典型案例）**：

```
cat2 时间题: "When did Melanie paint a sunrise?"     答案: 2022
依据 D1:12（Melanie）:
  text:          "You'd be a great counselor! ... By the way, take a look at this."
                  ← 文本完全没说"画日出"！
  blip_caption:  "a photo of a painting of a sunset over a lake"
  query:          "painting sunrise"
                  ← "画日出"这个事实只存在于搜图词和图片内容里
```
这道题若丢掉图片信息**必然答错**：turn 文本只是闲聊+分享动作，"Melanie 画画"这个事实以及画的内容都来自 `query`/`blip_caption`。这是数据集考察多模态记忆的典型设计——答案的一半藏在非文本通道。

**其他 evidence 含带图 turn 的真题**：

| 题目 | 类别 | 答案 | 依据 |
|---|---|---|---|
| "What is Caroline's identity?" | cat1 | Transgender woman | D1:5（彩虹旗壁画图那次发言） |
| "When did Caroline meet up with her friends, family, and mentors?" | cat2 | The week before 9 June 2023 | D3:11（家人院中合影图） |
| "What activities does Melanie partake in?" | cat1 | pottery, camping, painting, swimming | D5:4+D9:1+**D1:12**+D1:18——"painting"一项纯靠图片，"swimming"纯靠文本 |

**LightMem 的处理方式**（`add_locomo.py:132-133`）：

```python
if 'blip_caption' in turn and turn['blip_caption']:
    content = f"{content} (image description: {turn['blip_caption']})"
```
- **只用 `text` + `blip_caption`**，拼成 `"原文 (image description: 描述)"` 后进入正常文本管线（压缩→切分→抽取）；`img_url`/`query` 直接丢弃
- 注意：**`query`（搜图词）被丢弃**，而上面案例里 "painting sunrise" 的关键事实部分依赖 query——LightMem 只保留 caption（"sunset over a lake" 是日落、题目问 sunrise，靠 LLM 从"日落/日出画作+query语境"推），这会损失一部分图片信号，是上游的实现选择
- 抽取 prompt（`LoCoMo_Event_Binding_factual`）里有专门指令："When an image description is present, enrich the extracted facts by appending relevant visual details to them. Do NOT create separate facts solely for the image content"——即图片描述**并入相关事实**（如"Melanie 分享了一幅湖上日落画作"），而非独立成条

**一句话总结**：LoCoMo 的"多模态"是文字代理式的——图片以 BLIP caption 形式存在，约 1/5 的发言带图，部分题目的关键事实只藏在图片描述里；LightMem 把 caption 拼进正文当文本处理，不做任何视觉编码。

注意：网上很多博客把 cat1-4 写成 "1=单跳/2=多跳/3=时间/4=开放域"，与本数据集实测不符（实测 cat4=841 道、cat1=282 道，与官方 mem0 benchmark 仓库的 "1=multi-hop(282) / 2=temporal(321) / 3=open-domain(96) / 4=single-hop(841) / 5=adversarial(446)" 映射一致），引用时以数据本身为准。

- `evidence: ["D1:3","D7:2"]` = 答案依据 session1 第 3 条 + session7 第 2 条发言；跨 session 即多跳

## 4. 与 LightMem 管线的对应关系

| 数据集字段 | 管线用途 |
|---|---|
| turn（text+blip_caption） | `add_locomo.py` 拆成 `user(内容)+assistant(空)` 消息对，5,882 次 `add_memory` 流式调用（turn 是单方发言，无 user/assistant 语义，拆对只为适配管线） |
| session_N_date_time | 解析为时间轴：抽取元数据 time_stamp/weekday、摘要时间窗（3600s）滑动 |
| speaker（a/b） | speaker_id/speaker_name 元数据；评测时按说话人分组检索 |
| question | 检索 query（bge-m3 向量化） |
| answer / adversarial_answer | LLM judge 的 gold |
| category | 分桶统计准确率（search 默认只用 1-4） |
| evidence | 官方评测用（F1 等），本仓库评测脚本未使用 |

## 5. 数据量到运行量的换算（冒烟实测锚点）

- conv-26：419 turn → 827 MemoryEntry（event 双路抽取）→ offline 去重 805 → 19 条摘要
- 全量估算：5,882 turn → **约 11,000+ MemoryEntry**；~272 摘要；add 阶段约 3,600 API calls / ~480 万 token / ~4 小时（workers=1）
- 评测：1,542 题 × 2 次 LLM 调用（作答 + judge），约 3,100 calls

## 6. 更新记录

- 2026-09-12 创建：全量统计、格式说明、与管线对应关系（基于 10 对话实测）
- 2026-09-12 修正：cat1-5 类别语义按官方口径修正（1=多跳 2=时间 3=开放域 4=单跳 5=对抗），此前"1=单跳 2=多跳 3=时间 4=开放域"映射有误；补充每类真实例题与 cat4/cat5 孪生题鉴别证据；全量类别分布 cat1:282 cat2:321 cat3:96 cat4:841 cat5:446

## 7. 评测指标的两种流派（与 MemoryOS eval 对照）

LoCoMo 评测存在两套指标流派，本平台采用双指标并用：

**① token F1（MemoryOS evalution_loco.py / 官方论文口径）**
```
tokens(x) = 小写化、\b\w+\b 切词、去重成集合
P = |pred∩gold| / |pred|   精确率（答案词有多少命中 gold）
R = |pred∩gold| / |gold|   召回率（gold 词被覆盖多少）
F1 = 2PR/(P+R)             调和平均（同时罚多答与漏答）
```
确定性 100% 可复现，但只认字面：语义同义换词扣分（"Maria's aunt" vs "Her aunt" 仅 0.40）、简洁答案吃亏（"Yes" vs 长答案 0.22）、错误日期也能得 0.33（"6 May 2023" vs "August 4, 2023" 词形部分重合）。MemoryOS 自带旧结果重算 F1≈0.33，LightMem 冒烟（清洗思考前缀后）≈0.44，量级一致。

**② LLM Judge / J-score（LightMem search_locomo.py、Mem0/Zep 等业界主流）**
```
J = 判分 LLM 按"宽松语义"标准逐题判 CORRECT/WRONG（touch 到 gold 主题即对），取均值
```
语义敏感、可跨表述对齐，但存在 judge 随机性（temp=0 仍漂移、换 judge 模型漂 5-10 点），需重复测量压制（官方协议 5 轮取均值，波动可压到 <1 点）。

**结论**：单看 F1 会系统性低估短答案语义质量，单看 J-score 有主观漂移——两者交叉验证、结论同向才可信。F1 由 `experiments/locomo/eval_f1.py` 离线计算（内置思考前缀清洗，防止本地 vLLM 未开 reasoning parser 时 F1 被 0.06 级污染）。

## 8. 更新记录
- 2026-09-12 创建：全量统计、格式说明、与管线对应关系（基于 10 对话实测）
- 2026-09-12 修正：cat1-5 类别语义按官方口径修正（1=多跳 2=时间 3=开放域 4=单跳 5=对抗），此前"1=单跳 2=多跳 3=时间 4=开放域"映射有误；补充每类真实例题与 cat4/cat5 孪生题鉴别证据；全量类别分布 cat1:282 cat2:321 cat3:96 cat4:841 cat5:446
- 2026-09-12 增补：3.5 时间戳机制详解（session 级时间+500ms 伪 turn 时间四步加工链与缺陷）
- 2026-09-12 增补：3.6 多模态数据详解（20.8% turn 带图、BLIP caption 文字代理机制、"画日出"案例、LightMem 拼接处理）
- 2026-09-12 增补：第 7 节评测指标双流派对照（token F1 公式与失真实证、J-score 原理与不稳定性、双指标结论）

## 评测协议确认：两阶段"先写后读"（2026-09-13 讨论定稿）

- **阶段一 add**：10 对话逐 turn 写入记忆系统（三阶管线 + 摘要 + 去重），每对话独立产出事件库+摘要库
- **阶段二 search**：add 全部完成后，逐对话逐 QA 检索作答；**检索对话内隔离**（conv 的题只查 conv 自己的库，10 对话 = 10 个独立用户）；query 只用文字（img_url 丢弃）
- **gold answer 作用**：仅作判卷材料，不参与检索与生成；cat1-4 用 answer，cat5 用 adversarial_answer 且默认不评
- **"一年后检索"**：add 完成后一次性检索是工程顺序，非时间模拟协议；时间跨度由数据集 19 个跨年 session 天然提供
- **指标**：J-score（judge 语义判 CORRECT/WRONG 比例）+ token F1（词集合交并的调和平均），双指标交叉验证

## 指标实测补充2：思考开/关对评测的影响（2026-09-13）

答案生成 LLM（Qwen3-32B）的思考模式可通过 vLLM `chat_template_kwargs={"enable_thinking": false}` 在请求级开关（run_full_pipeline.sh `--no-think`），同一库同 152 题实测：

| 维度 | 开思考 | 关思考 | 解读 |
|---|---|---|---|
| Overall J | 0.776 | 0.665 | 思考提升语义答对率 11 pt |
| cat1 多跳 J | 0.656 | 0.375 | **多跳推理强依赖思考** |
| cat3 开放域 J | 0.769 | 0.846 | 开放域靠检索不靠推理 |
| Overall F1 | 0.281 | 0.354 | 关思考答案短、precision 高（F1 与 J 走势相反不矛盾） |
| 单题 completion | ~918 tok | 18 tok | 思考占 85% 成本 |
| 截断丢分 | 9/152 题 | 0 题 | 思考超 8192 上限 → prediction=None |

注意：此对照基于含污染摘要的旧库（vLLM 修复前生成的 19 条 ```think 草稿），绝对数字偏低；**结论方向（思考对多跳的价值、成本结构）有效**，干净库的全量数字以正式基线为准。

上游评测脚本考证：官方按 GPT-4o-mini（非思考模型）设计，无任何思考开关；OpenaiManager 路径传不进 enable_thinking，思考默认开且管线侧关不掉——本仓库的 --disable-thinking 是请求级补丁，只作用于 search 答案生成（add 阶段 LLM 与 judge 不受影响）。

## 指标实测补充：思考泄漏对 J-score 的虚高效应（2026-09-12）

本地 vLLM 服务未开 reasoning parser 时，prediction 携带 ```think 全推理过程：
- token F1：被思考词稀释（0.063，eval_f1.py 清洗后恢复 0.443）
- **J-score 反向虚高**：judge 读到推理文本，"摸到主题即 CORRECT" 的宽松标准大量放行 → 0.921；服务修复（--reasoning-parser qwen3）后同题复测回落 **0.730**
- 教训：J-score 与 F1 失真方向相反，二者交叉验证才能发现这类服务级污染


## 更新记录
- 2026-09-13 补充：思考开/关对照实测（J/F1 分化、多跳-28pt、成本结构、上游无思考开关考证）
- 2026-09-12 补充：思考泄漏对 J-score 的虚高效应（0.921→0.730 实测）
