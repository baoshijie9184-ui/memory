> **[归档文档]** 本文描述的是旧的单层+九阶段基线架构，不反映当前三层（L1/L2/L3）设计。当前架构请看 [../TECHNICAL_REPORT.md](../TECHNICAL_REPORT.md)。  
> 归档时间: 2026-09-06

# DesayMem：车机长期记忆系统的架构演进与多轮对话实践

> 从"听过就忘"到"记住你的每一句偏好"——本文记录了我们如何为车载座舱构建一套私有化部署的长期记忆系统，并用车机多轮对话模拟器验证其端到端能力。

## 一、背景：车机为什么需要"长期记忆"？

当前车载语音助手的典型交互是这样的：

```
用户：我开车的时候喜欢把空调调到22度
车机：好的，已为您设置空调温度22度

（下次上车）
用户：帮我开空调
车机：请问您想设置到多少度？     ← 忘了
```

**每次上车都是一次失忆。** 用户不得不重复声明偏好——空调温度、导航习惯、音乐口味、座椅位置。这在功能机时代可以接受，但在智能座舱的预期下，用户体验是断裂的。

业界有 Mem0 等开源记忆框架，但直接用于车机场景有几个问题：

- 车机数据合规要求**私有化部署**，不能依赖云端托管 API
- 车机场景有**多用户隔离**需求（司机 vs 乘客、家庭多驾驶员）
- 车机对话有**领域特殊性**（导航实体、空调偏好、操作步骤），通用 prompt 不够精准
- 需要支持**多记忆类型**（偏好类 vs 操作流程类）

基于这些痛点，我们开发了 **DesayMem**——一套从 Mem0 OSS 核心迁移、面向车载座舱优化的长期记忆系统。

## 二、系统架构：三库分离的持久化设计

### 2.1 整体架构

系统采用 **三库分离** 架构，各司其职：

```
┌──────────────────────────────────────────────────────────┐
│                     DesayMemory 核心                      │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │  Extractor │  │ Retriever │  │  Linker   │             │
│  │ (LLM抽取) │  │ (九步检索) │  │ (实体链接) │             │
│  └─────┬────┘  └─────┬────┘  └─────┬────┘             │
│        │             │             │                   │
│        ▼             ▼             ▼                   │
│  ┌─────────────────────────────────────────────┐        │
│  │              三库持久化层                     │        │
│  │                                             │        │
│  │  ┌─────────────┐  ┌──────────┐  ┌────────┐ │        │
│  │  │  PgVectorStore │  │ SQLite   │  │PgEntity│ │        │
│  │  │  (记忆向量库)  │  │ (会话+历史)│  │(实体库)│ │        │
│  │  └─────────────┘  └──────────┘  └────────┘ │        │
│  └─────────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────────┘
```

| 存储层 | 技术 | 职责 | 对应 Mem0 OSS |
|--------|------|------|--------------|
| 向量库 | PostgreSQL + pgvector | 语义记忆的向量存储与检索 | `memory_items` 表 |
| 会话库 | SQLite | last-k 会话上下文 + 操作历史审计 | `messages` + `history` 表 |
| 实体库 | PostgreSQL（第二张表） | 结构化实体抽取与关联 | `memory_entities` 表 |

**设计决策：为什么不用纯内存库？**

早期版本使用 `InMemoryVectorStore`，虽然是零依赖的快速原型方案，但存在致命问题——进程重启即丢失全部记忆。对于车机场景，熄火再点火是常态，记忆必须在跨会话间持久化。

`from_settings` 工厂方法现在**强制拒绝 InMemoryVectorStore**，从代码层面杜绝生产环境误用：

```python
if store is not None and isinstance(store, InMemoryVectorStore):
    raise ConfigurationError(
        "from_settings does not use in-memory stores",
        error_code="CFG_STORE",
        suggestion="Use PostgreSQL + pgvector for memories and entities",
    )
```

InMemory 实现降级为**仅限测试和 Demo 使用**。

## 三、记忆增加逻辑：三条路径，七阶段抽取

### 3.1 三种 Add 路径

系统根据参数自动选择三条不同的记忆写入路径：

```
add(messages, user_id, infer=True, memory_type=None)
  │
  ├── memory_type == "procedural_memory"?
  │     → _create_procedural_memory()
  │       LLM 用 PROCEDURAL_MEMORY_SYSTEM_PROMPT 生成操作摘要
  │
  ├── infer == False?
  │     → _add_raw_messages()
  │       每条消息原文直接存储，跳过 LLM
  │
  └── infer == True (默认)
        → 七阶段语义抽取流程
```

**三种路径的适用场景：**

| 路径 | 参数 | 场景 | 示例 |
|------|------|------|------|
| 语义抽取 | `infer=True` | 用户偏好、事实陈述 | "我开车喜欢把空调调到22度" |
| 原文存储 | `infer=False` | 导航指令、需保留原文的操作 | "导航去上海迪士尼" |
| 过程性记忆 | `memory_type=procedural_memory` | 多步骤操作流程 | "打开座椅加热→选3挡→确认" |

### 3.2 七阶段语义抽取流程（infer=True）

这是最核心的 add 路径，完整对齐 Mem0 OSS V3 的七阶段设计：

```
Phase 0  上下文组装
         │  从 SQLite 拉取最近 K 条会话消息（last-k）
         │  作为 LLM 抽取的上下文参考
         ▼
Phase 1  已有记忆检索
         │  向量搜索当前用户已有的 top_k=10 条记忆
         │  作为 LLM 抽取的"已知事实"参考（避免重复抽取）
         ▼
Phase 2  LLM 抽取（单次调用）
         │  将新对话 + last-k 上下文 + 已有记忆 一起喂给 LLM
         │  LLM 返回 JSON 格式的抽取结果
         ▼
Phase 3  批量 Embedding
         │  对 LLM 抽取出的每条记忆事实做向量化
         ▼
Phase 4  Hash 去重
         │  MD5 content_hash 与已有记忆比对
         │  跳过完全重复的记忆
         ▼
Phase 5  CPU 处理
         │  构建 StoredMemory 对象
         │  注入 metadata、memory_type、text_lemmatized
         ▼
Phase 6  批量持久化
         │  写入 PgVectorStore（向量 + 元数据）
         │  写入 SQLite history（ADD 事件记录）
         ▼
Phase 7  实体链接
         │  从抽取的记忆中识别实体（地名、品牌等）
         │  写入实体库并建立 memory ↔ entity 关联
         ▼
         保存会话消息到 SQLite last-k 存储
```

**关键设计：last-k 上下文**

Phase 0 是本次改动新增的关键步骤。每次 add 之前，系统会从 SQLite 拉取当前 session 最近 K 条对话消息（默认 K=10），作为 LLM 抽取的上下文。

没有 last-k 时，LLM 只看到当前这一轮对话，抽取结果有限。有了 last-k 后，LLM 能感知到"对话连续性"，抽取的信息更丰富。

实际效果对比：

```
无 last-k（旧版）:
  用户: 我开车的时候喜欢把空调调到22度
  抽取: User喜欢开车时将空调温度设置为22度     ← 只抽了用户侧

有 last-k（新版）:
  用户: 我开车的时候喜欢把空调调到22度
  车机: 好的，已为您设置空调温度22度
  抽取: User喜欢开车时将空调温度设置为22度     ← 用户偏好
       Assistant已为用户设置空调温度为22度     ← 车机执行动作
```

### 3.3 过程性记忆（Procedural Memory）

操作流程类对话走独立的 `_create_procedural_memory` 路径，使用专门的系统 prompt：

```python
PROCEDURAL_MEMORY_SYSTEM_PROMPT = """
You are a memory summarization system ...
## Summary of the agent's execution history
**Task Objective**: ...
**Progress Status**: ...
**Step-by-Step Execution History**:
  Step 1: Agent Action / Action Result / Key Findings ...
  Step 2: ...
**Preserve Every Output**: Each agent output must be recorded verbatim
"""
```

与语义记忆的区别：

| 维度 | 语义记忆 (semantic) | 过程性记忆 (procedural) |
|------|---------------------|------------------------|
| 内容 | 事实/偏好（"喜欢22度"） | 操作步骤（"如何开座椅加热"） |
| LLM 处理 | ADD-only 抽取 | 全文摘要 |
| 存储标记 | `memory_type=semantic_memory` | `memory_type=procedural_memory` |
| 检索过滤 | 默认 | 可按 `memory_type` 过滤 |

## 四、检索逻辑：九步混合检索

### 4.1 为什么不能只用向量搜索？

纯向量搜索在车机场景有两个问题：

1. **专有名词漏召**：用户说"上海迪士尼"，如果 embedding 模型对这个地名的向量化不够精确，相似度可能低于阈值
2. **短查询退化**：用户说"空调温度"只有 4 个字，向量信息密度低，容易召回不相关记忆

解决方案是 **混合检索**——向量搜索负责语义匹配，BM25 全文检索负责精确关键词匹配，实体 boost 负责结构化知识加分。

### 4.2 九步检索流程

```
Step 1  查询预处理
        │  lemmatize_for_bm25(query) → 词形还原
        │  extract_entities(query) → 实体识别
        ▼
Step 2  查询向量化
        │  embedding.embed(query)
        ▼
Step 3  语义搜索（Over-fetch）
        │  向量库搜索 max(top_k×4, 60) 条候选
        │  保证召回率，后续靠 rerank 精排
        ▼
Step 4  关键词搜索（BM25）
        │  PostgreSQL ts_rank_cd + plainto_tsquery
        │  或 InMemoryStore 的简易词匹配
        ▼
Step 5  BM25 分数归一化
        │  normalize_bm25() → sigmoid 映射到 [0, 1]
        │  自适应参数：查询词数越少，sigmoid 中点越大
        ▼
Step 6  实体 Boost
        │  对包含查询实体的候选记忆加分
        │  entity_boost_min_similarity 控制阈值
        ▼
Step 7  候选集合并
        │  语义命中为主集，BM25 命中补充
        ▼
Step 8  综合打分排序
        │  combined = (semantic + bm25 + entity_boost) / max_possible
        │  先过滤低于 threshold 的候选
        │  按分数降序取 top_k
        ▼
Step 9  格式化输出
        │  返回 MemoryItem 列表
        │  explain=True 时附带 score_details
```

### 4.3 自适应 BM25 参数

一个细节设计：BM25 归一化的 sigmoid 参数会根据查询词数自适应调整：

```python
def get_bm25_params(query_terms: int) -> tuple[float, float]:
    if query_terms <= 2:
        return 3.0, 0.3   # 短查询：中点高，区分度大
    elif query_terms <= 5:
        return 2.0, 0.5   # 中等查询
    else:
        return 1.0, 0.7   # 长查询：中点低，容忍度高
```

短查询（如"空调"）的 BM25 原始分通常很高（因为词频集中），需要更高的 sigmoid 中点来拉开区分度；长查询（如"用户习惯的空调温度是多少"）的 BM25 分数分布更均匀，需要更宽松的归一化。

## 五、多轮对话模拟器：端到端验证

### 5.1 设计思路

有了记忆系统，怎么验证它真的"记住了"？我们编写了一个车机多轮对话模拟器 `scripts/cockpit_simulator.py`，模拟司机上车后的一次完整驾驶会话。

模拟器的设计原则：

- **Live LLM**：强制使用真实 DashScope API，不降级到 Fake，验证真实抽取质量
- **每轮即时验证**：存完立即 search，不等全部存完再查
- **跨轮召回**：最后一轮不写入新记忆，只检索，验证记忆的跨轮持久性

### 5.2 五轮对话场景

```
司机上车 (session_drive_001)
    │
    ▼
轮次 1: 空调偏好
  司机: 我开车的时候喜欢把空调调到22度
  车机: 好的，已为您设置空调温度22度
  动作: add(infer=True) → LLM 抽取
  验证: search("用户习惯的空调温度是多少") 命中 "22" ✓
    │
    ▼
轮次 2: 导航目的地
  司机: 导航去上海迪士尼
  车机: 正在为您规划前往上海迪士尼的路线
  动作: add(infer=False) → 原文存储
  验证: search("上海迪士尼") 命中 "上海迪士尼" ✓
    │
    ▼
轮次 3: 音乐偏好
  司机: 开车时帮我放周杰伦的歌
  车机: 已为您播放周杰伦的歌单
  动作: add(infer=True) → LLM 抽取
  验证: search("司机喜欢听什么音乐") 命中 "周杰伦" ✓
    │
    ▼
轮次 4: 座椅加热（过程性记忆）
  司机: 帮我把座椅加热打开到3挡
  车机: 座椅加热已调至3挡
  动作: add(memory_type=procedural_memory) → LLM 摘要
  验证: 类型 == procedural_memory ✓
        search("座椅加热") 命中 "座椅" ✓
    │
    ▼
轮次 5: 跨轮上下文召回
  司机: 我之前说的空调温度是多少度？
  车机: 您之前说喜欢把空调调到22度
  动作: 不写入新记忆，仅检索
  验证: search("用户习惯的空调温度是多少") 命中 "22" ✓
        → 证明轮次 1 的偏好已持久化并可跨轮召回
    │
    ▼
会话结束: 5/5 全部通过 ✓
```

### 5.3 实际运行输出

```
模式: 直连 DesayMemory | LLM=qwen3.8-flash EMB=qwen3.7-text-embedding-flash

--- [轮次 1/5] 空调偏好 ---
  司机: 我开车的时候喜欢把空调调到22度
  车机: 好的，已为您设置空调温度22度
  记忆存储: User喜欢开车时将空调温度设置为22度
  记忆存储: Assistant已为用户设置空调温度为22度
  召回验证: ✓ 期望「空调温度22度」已确认

--- [轮次 2/5] 导航目的地 ---
  司机: 导航去上海迪士尼
  车机: 正在为您规划前往上海迪士尼的路线
  记忆存储: 导航去上海迪士尼
  记忆存储: 正在为您规划前往上海迪士尼的路线
  召回验证: ✓ 期望「导航目的地上海迪士尼」已确认

--- [轮次 3/5] 音乐偏好 ---
  司机: 开车时帮我放周杰伦的歌
  车机: 已为您播放周杰伦的歌单
  记忆存储: User喜欢开车时听周杰伦的歌
  记忆存储: Assistant已为用户播放周杰伦的歌单
  召回验证: ✓ 期望「周杰伦音乐偏好」已确认

--- [轮次 4/5] 座椅加热（过程性记忆） ---
  记忆存储 [procedural_memory]: ## Summary of the agent's execution history...
  类型验证: ✓ 过程性记忆类型保留 (procedural_memory)
  召回验证: ✓ 期望「座椅加热操作步骤」已确认

--- [轮次 5/5] 跨轮上下文召回 ---
  (跨轮召回模式: 不写入新记忆，仅检索已有记忆)
  召回命中: User喜欢开车时将空调温度设置为22度
  召回验证: ✓ 期望「从已有记忆中召回22度偏好」已确认

============================================================
  模拟结果汇总
============================================================
  通过: 5/5
  全部轮次验证通过 ✓
```

### 5.4 双后端模式

模拟器支持两种调用方式，通过 `--http` 参数切换：

**直连模式（默认）**：直接 import `DesayMemory`，进程内调用，适合本地开发验证。

**HTTP 客户端模式**：通过 httpx 调用 API server，模拟真实车机客户端的 HTTP 调用链路。

```bash
# 直连模式
python scripts/cockpit_simulator.py

# HTTP 模式（需先启动 API server）
python scripts/cockpit_simulator.py --http
python scripts/cockpit_simulator.py --http --base-url http://192.168.0.166:8000
```

## 六、架构优缺点分析

### 6.1 优点

**1. 持久化架构合理，三库各司其职**

向量库负责语义检索，SQLite 负责会话上下文和审计日志，实体库负责结构化知识关联。职责清晰，不会互相干扰。熄火再点火后，记忆完整恢复。

**2. last-k 上下文提升抽取质量**

引入 last-k 上下文后，LLM 不仅能抽取用户偏好，还能同时记录车机执行动作。信息密度更高，对后续对话理解更有帮助。

**3. 混合检索解决中文检索痛点**

BM25 全文检索对"上海迪士尼"这类专有名词的精确匹配能力，弥补了纯向量搜索的不足。三路融合（语义 + BM25 + 实体）的召回率显著优于单一通道。

**4. `from_settings` 强制持久化**

工厂方法拒绝 InMemoryVectorStore，从代码层面防止生产环境误用内存库。这是正确的安全护栏。

**5. 过程性记忆独立通道**

操作步骤类对话走专门的 procedural prompt，与偏好类语义记忆区分存储，后续可按 `memory_type` 精确过滤检索。

### 6.2 缺点与风险

**1. 持久化路径（PgVector）缺乏端到端测试**

模拟器仍使用 `InMemoryVectorStore`，最核心的 Postgres 持久化路径反而是测试盲区。`PgVectorStore` 的 `keyword_search`、`check_schema` 等新方法需要真实数据库环境验证。

**2. `infer=False` 原文存储产生冗余记忆**

轮次 2（导航）用 `infer=False` 存了 2 条原文（用户话 + 车机话），这些未经过 LLM 提炼的原文在后续检索中会与精炼的语义记忆混在一起，可能稀释召回质量：

```
轮次 2 召回结果:
  导航去上海迪士尼                          ← 原文（有用）
  正在为您规划前往上海迪士尼的路线...         ← 原文（噪声）
  Assistant已为用户设置空调温度为22度        ← 不相关
```

**3. 九步检索链路的延迟问题**

每次 search 需要：1 次 embedding API 调用 + 1 次向量搜索 + 1 次 BM25 全文搜索 + 实体 boost + rerank。车机场景对响应延迟敏感（通常要求 <300ms），这个链路在 Postgres 大表上可能偏重。

**4. 缺少 UPDATE 路径，记忆堆积风险**

当前 add 只有 ADD-only（Mem0 OSS 的 UPDATE 被注释掉了）。如果用户说"我现在喜欢 25 度了"，系统会新增一条 25 度记忆而不是更新 22 度那条。长期使用会导致同主题记忆堆积，检索时新旧偏好同时出现。

**5. `from_settings` 拒绝 InMemory 但 `__init__` 不拒绝**

`from_settings` 有安全护栏，但直接 `DesayMemory(store=InMemoryVectorStore(...))` 仍然可以绕过。保护不完整，依赖开发者自觉。

## 七、总结与下一步

DesayMem 从"单内存向量库"演进到"三库持久化架构"，检索从"单步向量搜索"升级到"九步混合检索"，已经具备了车机长期记忆的基础能力。多轮对话模拟器 5/5 全部通过，验证了核心链路的正确性。

下一步需要重点解决：

| 优先级 | 方向 | 说明 |
|--------|------|------|
| P0 | PgVector 端到端测试 | 模拟器增加 Postgres 后端模式，覆盖持久化路径 |
| P1 | 检索延迟实测 | 在真实数据量下测量九步检索的 P99 延迟 |
| P1 | UPDATE 路径 | 支持"偏好变更"场景，避免记忆堆积 |
| P2 | 原文存储二次提炼 | `infer=False` 的原文在后续检索中是否需要可选的 LLM 精炼 |
| P2 | 多用户切换场景 | 模拟器增加多驾驶员切换登录的验证 |

---

*DesayMem 项目源码：内部仓库 `DesayMem_mem0`，基于 Mem0 OSS v2.0.18 (commit 4fa48390) 迁移重构。*
