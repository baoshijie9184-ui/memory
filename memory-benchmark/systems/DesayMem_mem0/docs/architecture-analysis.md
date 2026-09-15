# DesayMem 源码架构分析：L1→L2→L3 演化与存储设计

> 本文档基于 `src/desaymem/` 源码分析，覆盖数据流、表结构、字段设计和三层演化逻辑。
> 配套：[llm-call-analysis.md](llm-call-analysis.md)（LLM 调用复杂度与优化）、[DesayMem_mem0_LLM调用降本改进方案.md](DesayMem_mem0_LLM调用降本改进方案.md)（架构级降本路线）
> 最后更新：2026-09-08

---

## 目录

- [一、整体架构总览](#一整体架构总览)
- [二、L1 → L2 → L3 演化全链路](#二l1--l2--l3-演化全链路)
- [三、表设计逐张详解](#三表设计逐张详解)
- [四、数据流与字段映射关系](#四数据流与字段映射关系)
- [五、检索链路与 rerank——LLM 收到什么](#五检索链路与-rerankllm-收到什么)
- [六、设计评述](#六设计评述)

---

## 一、整体架构总览

```
src/desaymem/
├── core/           ← 编排层：add/search 流水线、配置、模型
│   ├── memory.py       DesayMemory 主类（七阶段 add + 九步 search）
│   ├── models.py       公开 Pydantic 模型
│   ├── config.py       Settings（环境变量驱动）
│   └── enums.py        MemoryType / BeliefStability / BeliefDecision
├── extraction/     ← L1 生产线
│   ├── extractor.py    LLM 事实提取
│   ├── entities.py     正则实体提取
│   ├── entity_linker.py 实体链接 + 检索 boost
│   ├── deduplicator.py MD5 去重
│   └── parser.py       JSON 解析
├── layers/         ← L2/L3 演化层
│   ├── episodes.py     L2 EpisodeBuilder
│   ├── distiller.py    L3 ProfileDistiller
│   ├── reranker.py     语义重排
│   └── prompts.py      L2/L3/Rerank 的 LLM prompt
├── retrieval/      ← 检索层
│   ├── retriever.py    九步混合检索
│   ├── scoring.py      分数融合
│   ├── conflict_resolution.py  规则冲突过滤
│   └── lemmatization.py BM25 词形归并
├── stores/         ← 持久化层
│   ├── pgvector.py     PostgreSQL 主存储
│   ├── profile.py      L3 belief 存储
│   ├── entities.py     实体存储
│   ├── audit.py        审计事件
│   └── sqlite_history.py SQLite 变更历史
├── providers/      ← LLM/Embedding 抽象
└── api/            ← FastAPI 路由
```

### 三个数据库、六张核心表

| 存储 | 表 | 层 | 用途 |
|---|---|---|---|
| PostgreSQL | `memory_items` | L1+L2 | 语义事实 + 情景记忆（共用表，`memory_type` 区分） |
| PostgreSQL | `profile_beliefs` | L3 | 结构化用户信念 |
| PostgreSQL | `user_profile_snapshots` | L3 | 画像叙述文本 |
| PostgreSQL | `memory_entities` | 辅助 | 实体索引 |
| PostgreSQL | `memory_audit_events` | 观测 | 三层全事件审计 |
| SQLite | `history` + `messages` | 兼容 | Mem0 OSS 兼容历史 + 最近 K 条消息 |

### Migration 文件与表的对应关系

| 迁移文件 | 建表/改表 | 说明 |
|---|---|---|
| `001_initial.sql` | `memory_items` | 核心记忆表（含 HNSW 向量索引、唯一约束） |
| `002_session_entities.sql` | `session_messages` + `memory_entities` | 会话消息表 + 实体表（`memory_type` 列也在此时添加） |
| `003_bm25.sql` | `memory_items` 新增 `text_lemmatized` | GIN 全文检索索引 |
| `004_layers.sql` | `profile_beliefs` + `user_profile_snapshots` | L3 结构化画像 |
| `005_episode_integrity.sql` | 部分 UNIQUE INDEX on `memory_items` | 每 scope 只允许一个活跃 episode |
| `006_memory_observability.sql` | `memory_audit_events` | 三层审计事件 |

---

## 二、L1 → L2 → L3 演化全链路

### 触发点

`add()` → `add_result()` → `_evolve_layers()`（`memory.py:541`）。每次 `add` 在 L1 事实落库后，立即触发 L2 聚合和 L3 蒸馏。

### 演化数据流

```
对话消息
   │
   ▼
┌─────────────────────────────────────────────────────┐
│ L1：七阶段提取（semantic_memory）                     │
│ ① parse 消息 → ② 取最近K条+画像 → ③ 检索相似旧记忆    │
│ → ④ LLM 提取事实 → ⑤ embed+hash去重 → ⑥ 入库         │
│ → ⑦ 实体链接                                        │
│                                                     │
│ 产物: StoredMemory × N（每条一个原子事实）             │
└──────────────────────┬──────────────────────────────┘
                       │ stored (L1 facts)
                       ▼
┌─────────────────────────────────────────────────────┐
│ L2：Episode 聚合（episodic_memory）                  │
│                                                     │
│ active = get_active_episode()      ← 查当前开放episode│
│ similarity = cosine(active.emb, mean_pool(new.emb)) │
│ judgment = LLM.judge(消息+新事实+active+相似度)        │
│    ├─ continues=true  → UPDATE active（合并摘要+ID）  │
│    ├─ continues=false → COMPLETE 旧的 + ADD 新episode │
│    └─ 无active       → ADD 新episode                │
│                                                     │
│ 产物: 1条 episode StoredMemory                       │
│   metadata: {episode_status, source_memory_ids[],    │
│              confidence, occurred_at/occurred_end}   │
└──────────────────────┬──────────────────────────────┘
                       │ episode_id
                       ▼
┌─────────────────────────────────────────────────────┐
│ L3：Belief 蒸馏（profile_beliefs）                    │
│                                                     │
│ cluster = 新L1事实 + 向量邻居旧L1（排除episodic）      │
│ candidates = LLM.distill(cluster)  →  belief 候选     │
│ 逐条 _apply_one：                                    │
│   校验 evidence_ids → stability降级检查 →             │
│   embed( subject|attribute|conditions ) →            │
│   _nearest(阈值0.82) 匹配已有 belief →                 │
│   按 decision 执行 INSERT/UPDATE/SUPERSEDE           │
│                                                     │
│ 产物: StoredBelief × N + 重建 narrative 快照          │
└─────────────────────────────────────────────────────┘
```

### L1 → L2 的衔接细节

L2 复用 `memory_items` 表，通过 `memory_type='episodic_memory'` + 部分 metadata 区分：

```sql
-- 005_episode_integrity.sql：每 scope 只允许一个活跃 episode
CREATE UNIQUE INDEX uq_memory_active_episode_scope
ON memory_items (tenant_id, user_id, occupant_id)
WHERE memory_type = 'episodic_memory'
  AND COALESCE(metadata->>'episode_status', 'active') = 'active';
```

episode 的 `metadata.source_memory_ids` 是指回 L1 的外键链（JSONB 数组），搜索时的 `_expand_episode_evidence`（`memory.py:973`）会沿这条链把 episode 关联的 L1 证据拉进 rerank 候选池。

### L2 的三种分支执行

| 条件 | 操作 | 审计事件 |
|---|---|---|
| `judgment.continues=True` 且 `active != None` | UPDATE 当前 episode（合并 source_memory_ids、用 LLM 新 summary 替换 content、重新 embedding） | L2 / UPDATE |
| `judgment.continues=False` 且 `active != None` | 旧 episode 标记 `episode_status=complete`，创建新 episodic_memory | L2 / COMPLETE + L2 / ADD |
| `active == None`（无活跃 episode） | 直接创建新 episodic_memory | L2 / ADD |

episode 的 `content_hash` 掺入 `active.id`（`memory.py:803`），即 `md5("episodic:" + id + ":" + summary)`——摘要更新时 hash 必变，绕过唯一约束允许反复更新。

### L2 → L3 的衔接细节

`_evolve_layers` 把 `episode_id` 传给 `distiller.distill()`。新 belief 的 `evidence_episode_ids` 字段记录这个关联——L3 信念同时持有 L1 记忆证据和 L2 情景证据双链。

### L3 内部的状态机

```
                    INSERT
                      │
        ┌─────────────┼──────────────┐
        ▼             ▼              ▼
     active ──CONFIRM──► active    active ──SUPERSEDE/REFINE──► superseded
     (升级conf/支持数)   (并存)         │                          │
        ▲             COEXIST→新active │                          │
        └──新证据合并─────◄───          ▼                          ▼
                              新 active belief          valid_from→valid_to
                                                            (时间窗口保留)
```

`status` 只有 `active`/`superseded` 两态，superseded 不删除——保留完整信念演化史，可回溯"用户偏好什么时候从 22 度变成 26 度"。

### L3 的五种 decision 执行矩阵

| decision | 匹配到旧 belief | 未匹配到（match=None） |
|---|---|---|
| **CREATE** | 相似度 >= 0.82: UPDATE 旧 belief（合并证据、取 max confidence、升级 stability） | INSERT 新 belief |
| **CONFIRM** | UPDATE 旧 belief | INSERT 新 belief |
| **REFINE** | 旧 belief 标记 superseded + INSERT 新 belief | INSERT 新 belief |
| **SUPERSEDE** | 旧 belief 标记 superseded + INSERT 新 belief | INSERT 新 belief |
| **COEXIST** | INSERT 新 belief（条件不同，两者并存） | INSERT 新 belief |

### L3 的两道 Python 硬防线

1. **evidence_ids 校验**：候选 belief 的 evidence 不在有效 L1 记忆集合内 → 直接丢弃（`distiller.py:173-176`）
2. **stability 降级**：LLM 标记 `recurring` 但证据全来自同一天 → 强制降级为 `episode`（`distiller.py:183-186`）

---

## 三、表设计逐张详解

### 1. `memory_items` — L1/L2 共用主表

> Migration: `001_initial.sql` + `002_session_entities.sql` + `003_bm25.sql`

```sql
CREATE TABLE memory_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- ▼ 隔离维度（6 个 scope 字段）
    tenant_id       TEXT NOT NULL,           -- 租户（OEM 级隔离）
    user_id         TEXT NOT NULL,           -- 用户（必填硬隔离）
    vehicle_id      TEXT NOT NULL DEFAULT '',
    occupant_id     TEXT NOT NULL DEFAULT 'primary',   -- 乘员（主驾/副驾）
    session_id      TEXT NOT NULL DEFAULT '',
    scene           TEXT NOT NULL DEFAULT '',          -- 场景标签
    source          TEXT NOT NULL DEFAULT 'conversation',
    -- ▼ 内容
    memory_type     TEXT NOT NULL DEFAULT 'semantic_memory',
                    -- semantic_memory | episodic_memory | procedural_memory
    content         TEXT NOT NULL,            -- 记忆正文
    content_hash    TEXT NOT NULL,            -- MD5，幂等去重键
    text_lemmatized TEXT NOT NULL DEFAULT '', -- BM25 用归并文本
    embedding       VECTOR(1024) NOT NULL,    -- HNSW 余弦索引
    embedding_model TEXT NOT NULL,           -- 模型可追溯
    embedding_dims  INTEGER NOT NULL,         -- CHECK=1024 防混用
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- ▼ 约束
    CONSTRAINT uq_memory_tenant_user_hash
        UNIQUE (tenant_id, user_id, content_hash)    -- 内容级幂等
);
```

#### 索引

| 索引名 | 列 | 类型 | 用途 |
|---|---|---|---|
| `idx_memory_tenant_user` | `(tenant_id, user_id)` | B-Tree | 隔离查询基线 |
| `idx_memory_tenant_user_created` | `(tenant_id, user_id, created_at DESC)` | B-Tree | 按时间排序 |
| `idx_memory_tenant_user_type` | `(tenant_id, user_id, memory_type)` | B-Tree | 按层过滤 |
| `idx_memory_vehicle` | `(vehicle_id)` | B-Tree | 车辆维度过滤 |
| `idx_memory_session` | `(session_id)` | B-Tree | 会话维度过滤 |
| `idx_memory_scene` | `(scene)` | B-Tree | 场景过滤 |
| `idx_memory_source` | `(source)` | B-Tree | 来源过滤 |
| `idx_memory_metadata` | `(metadata)` | GIN | JSONB 任意键查询 |
| `idx_memory_embedding_hnsw` | `(embedding)` | HNSW vector_cosine_ops | 向量近邻检索 |
| `idx_memory_text_lemmatized_fts` | `(to_tsvector('simple', text_lemmatized))` | GIN | 全文检索 |
| `uq_memory_active_episode_scope` | `(tenant_id, user_id, occupant_id) WHERE episodic+active` | Partial UNIQUE | episode 状态机完整性 |

#### 设计要点

- **scope 六字段平铺为列**而非塞进 JSONB：所有检索都带 `tenant_id+user_id` 过滤，列上建复合索引，支持过滤下推
- **`content_hash` 唯一约束**：`INSERT ... ON CONFLICT DO NOTHING`（`pgvector.py:294`）实现天然幂等——重复 add 不报错不重复写
- **`embedding_dims` CHECK=1024**：换 embedding 模型（不同维度）时不小心写旧库会被数据库拒绝
- **L1/L2 同表**：episode 也是一条"记忆"，可被语义检索召回；靠 `memory_type` 和部分唯一索引（005）维护状态机
- **双检索索引**：HNSW 向量近邻 + GIN 全文检索，对应九步检索的 Step 3（semantic）和 Step 4（keyword）

#### 典型行数据（L1 语义记忆）

```
id=550e8400-...  tenant_id=test_tenant  user_id=test_user_001
vehicle_id=test_vehicle_001  occupant_id=primary
session_id=sess_test_001  scene=driving  source=python_benchmark
memory_type=semantic_memory
content="空调温度设定为22度"
content_hash=a3f1...  text_lemmatized="空调 温度 设定 22 度"
embedding=[0.12, -0.34, ... 1024 floats]
metadata={
    "occurred_at": "2026-08-01T08:00:00+08:00",
    "observed_at": "...",
    "memory_type": "semantic_memory",
    "dataset_id": "smoke_test_v1"
}
```

#### 典型行数据（L2 情景记忆）

```
memory_type=episodic_memory
content="驾驶途中用户因热将空调调至22度并要求播放爵士乐"
content_hash=md5("episodic:" + id + ":" + summary)   ← 特殊哈希，允许摘要变
embedding=[...]  ← 对 summary 的 embedding
metadata={
    "episode_status": "active",
    "source_memory_ids": ["uuid-001","uuid-002","uuid-003","uuid-004"],
    "confidence": 0.85,
    "occurred_at": "2026-08-01T08:00:00+08:00",
    "occurred_end": "..."
}
```

---

### 2. `profile_beliefs` — L3 信念表

> Migration: `004_layers.sql`

```sql
CREATE TABLE profile_beliefs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    occupant_id         TEXT NOT NULL DEFAULT 'primary',
    -- ▼ SAV 结构（Subject-Attribute-Value）
    subject             TEXT NOT NULL DEFAULT 'User',
    attribute           TEXT NOT NULL,                   -- 自然语言属性键（开放）
    value               TEXT NOT NULL,                   -- 当前值
    conditions          JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 适用条件
    -- ▼ 生命周期
    stability  TEXT NOT NULL DEFAULT 'episode'
        CHECK (stability IN ('episode','recurring','identity')),
    status      TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','superseded')),
    valid_from  TIMESTAMPTZ,         -- 生效时间
    valid_to    TIMESTAMPTZ,         -- 失效时间（superseded 时写入）
    -- ▼ 证据与量化
    confidence          REAL NOT NULL DEFAULT 0.5,
    support_count       INTEGER NOT NULL DEFAULT 1 CHECK (>= 1),
    evidence_memory_ids   JSONB NOT NULL DEFAULT '[]',   -- 指向 L1 memory_items.id
    evidence_episode_ids  JSONB NOT NULL DEFAULT '[]',   -- 指向 L2 memory_items.id
    -- ▼ 匹配用向量（对 "subject|attribute|conditions" 键文本做 embedding）
    attribute_embedding  VECTOR(1024) NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### 索引

| 索引名 | 列 | 类型 | 用途 |
|---|---|---|---|
| `idx_beliefs_tenant_user` | `(tenant_id, user_id)` | B-Tree | 隔离基线 |
| `idx_beliefs_tenant_user_status` | `(tenant_id, user_id, status)` | B-Tree | 按 active/superseded 过滤 |
| `idx_beliefs_tenant_user_occupant` | `(tenant_id, user_id, occupant_id)` | B-Tree | 乘员维度 |
| `idx_beliefs_attribute_hnsw` | `(attribute_embedding)` | HNSW vector_cosine_ops | 写入时语义匹配（非用户检索） |

#### 设计要点

- **attribute 是开放自然语言**（如 `preferred_temperature`、`music_preference`），不是固定枚举——由 LLM 自由命名。靠 `attribute_embedding`（对 `subject|attribute|conditions` 键文本的 embedding）做语义等价合并，阈值 0.82
- **证据双链**：`evidence_memory_ids`（L1）+ `evidence_episode_ids`（L2），每个 belief 都能追溯到原始事实——LLM 幻觉防线
- **`valid_from`/`valid_to` 时间窗**：superseded 时不删行，而是关窗，保留用户偏好历史
- **`stability` 三级**：`identity`（身份级）> `recurring`（跨时间重复习惯）> `episode`（单次）。Python 硬检查降级
- **HNSW 索引在 `attribute_embedding` 上**：仅用于写入时合并匹配（`search_similar` top_k=1），用户检索永远不直接 ANN 这张表——L3 是结构化画像，不是检索集合

#### 典型行数据

```
subject="User"  attribute="preferred_temperature"  value="26°C"
conditions={"context":"driving"}
stability="recurring"  status="active"
confidence=0.8  support_count=3
evidence_memory_ids=["uuid-001","uuid-005","uuid-010"]
evidence_episode_ids=["ep-uuid-001"]
attribute_embedding=[...]  ← embed("User | preferred_temperature | {context:driving}")
valid_from=2026-09-01  valid_to=NULL
```

#### belief 是怎么写入这张表的（一次 add 的完整派生链）

每次 `add()` 在 L1 落库、L2 聚合完成后，`_evolve_layers` 立即触发 L3 蒸馏。整条链路：

```
对话消息
  │
  ▼
L1: LLM 提取 → N 条语义事实落库
  │
  ▼
L2: episode 判断（延续/关闭+新建）→ 得到 episode_id
  │
  ▼
L3: distill（distiller.py:92）
  ├─ cluster = 本次新 L1 事实
  │           + store.search(新事实embedding, top_k=12) 检索的旧 L1 邻居
  │           （排除 episodic 类型、排除已重复的）
  │
  ├─ LLM 读 cluster → 提议 belief 候选（subject/attribute/value/conditions/decision）
  │
  └─ 逐条 _apply_one（distiller.py:160）:
       embed(key) → _nearest(0.82) 匹配已有 belief
         ├─ CONFIRM/CREATE+匹配 → UPDATE（合并证据、升支持数）
         ├─ REFINE/SUPERSEDE → 旧 belief 关窗 + INSERT 新 belief
         ├─ COEXIST → INSERT（并存）
         └─ 无匹配 → INSERT
```

**几个关键解释**：

1. **cluster 不只包含本次新事实**：单次对话的证据往往不足以形成画像。比如用户这次说"空调调到26度"，只有一条新 L1；但库里已有"上次设过24度"的旧 L1——向量检索把这条旧邻居拉进 cluster，LLM 看到两条跨时间证据，才能提议 `stability=recurring` 并下 `REFINE` 决策。没有邻居扩展，单次行为最多只能生成 episode 级 belief。

2. **subject / attribute / conditions 全部由 LLM 生成**：Python 不命名、不修改，只做校验（非空、evidence_ids 指向真实 L1 记忆、stability 降级检查）。LLM 拥有自然语言自由度（`preferred_temperature` 或 `偏好温度` 都合法），语义等价合并完全依赖 `attribute_embedding` 的 0.82 阈值匹配。

3. **L2 和 L3 并列消费 L1**，不是链式依赖：L3 的 cluster 由 L1 组成，L2 只贡献 `episode_id`（挂到新 belief 的 `evidence_episode_ids` 上作为证据链）。两层各自独立 try/except，任何一层失败不影响另一层和已落库的 L1。

4. **UPDATE 分支的合并语义**（CONFIRM/CREATE 命中匹配时，`distiller.py:283-296`）：
   - `evidence_memory_ids` 去重合并（新旧证据取并集）→ `support_count` 随之增长
   - `confidence` 取新旧较大值
   - `value` 用新值覆盖
   - `stability` 取更高等级（但支持数不足 2 时降回 episode）
   - `attribute_embedding` 刷新为新 key 的向量

5. **attribute_embedding 匹配的是"属性槽位"而非"值"**：embedding 的输入是 `subject|attribute|conditions` 拼接的 key 文本，value 不参与。这保证"温度偏好"无论值是 22 度还是 26 度，都命中同一条 belief 槽位，值的变化走 REFINE/SUPERSEDE 分支处理。跨语言场景（`preferred_temperature` vs `偏好温度`）靠 BGE-M3 跨语言向量语义匹配，字符串比对无法做到。

---

### 3. `user_profile_snapshots` — 画像叙述

> Migration: `004_layers.sql`

```sql
CREATE TABLE user_profile_snapshots (
    tenant_id   TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    narrative   TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, user_id)
);
```

纯文本画像（`assemble_narrative` 把 active beliefs 拼成 `User preferred_temperature: 26°C` 格式的行）。每 `(tenant,user)` 一行，全量重建。用途：喂给 LLM——下次 `add` 的 Phase 0 把它作为 `profile_summary` 传给提取器；`search` 的 rerank 阶段也用它做语义重排参考。

#### Profile 是怎么得到的 —— 不再蒸馏，而是从 L3 beliefs 机械拼接

**重要澄清：profile 不经过 LLM。** 之前 L1→L2→L3 的"蒸馏"（distill）发生在 belief 生成阶段——LLM 从 L1 事实簇提议 SAV 信念。而 profile（叙述文本）只是 **L3 beliefs 的确定性投影**，每次 belief 落库后由 Python 全量重建：

```
beliefs 表有变更（distill 返回 applied > 0）
  │
  ▼
refresh_snapshot（distiller.py:140-148）
  ├─ store.list_active(tenant_id, user_id)     ← 取全部 status='active' 的 belief
  ├─ assemble_narrative(beliefs)               ← 纯 Python 拼接，不调 LLM
  └─ upsert_snapshot(...)                      ← 整行覆盖写入 user_profile_snapshots
```

`assemble_narrative`（`distiller.py:31-39`）的拼接规则：

```python
for item in beliefs:                       # 只取 active
    cond = item.conditions or {}
    suffix = f" ({json.dumps(cond, ...)})" if cond else ""
    lines.append(f"{item.subject} {item.attribute}: {item.value}{suffix}")
```

即每条 active belief 一行，格式 `Subject attribute: value (conditions)`。以库里这些 belief 为例：

```
profile_beliefs (active):
  User / preferred_temperature / 26°C / conditions={} / recurring
  User / music_preference / 爵士乐 / conditions={"season":"summer"} / episode

narrative =
"User preferred_temperature: 26°C
 User music_preference: 爵士乐 ({"season": "summer"})"
```

**读取路径**（`profile_view`，`distiller.py:150-158`）：检索时先查 `user_profile_snapshots` 缓存；没有缓存则即时 `assemble_narrative` 兜底。所以两个消费点拿到的都是同一份结构化画像：

| 消费点 | 时机 | 传入形式 |
|---|---|---|
| `add` Phase 0 | 提取前 | narrative 文本 → `extractor.extract(summary=...)`，帮 LLM 知道已知偏好、避免重复提取 |
| `search` rerank | 重排前 | `ProfileView`（narrative + 全部 active beliefs 的 SAV 列表）→ 见第四章 rerank prompt |

superseded 的 belief **永远不进 narrative**——画像只反映当前状态，历史偏好靠 `profile_beliefs` 表的 `valid_from/valid_to` 时间窗追溯。

---

### 4. `memory_entities` — 实体表

> Migration: `002_session_entities.sql`

```sql
CREATE TABLE memory_entities (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    entity_text     TEXT NOT NULL,              -- 原文（如"西湖"）
    entity_type     TEXT NOT NULL,              -- PROPER|QUOTED|TOPIC|IDENTIFIER
    normalized_text TEXT NOT NULL,              -- 小写规范化
    embedding       VECTOR(1024) NOT NULL,
    linked_memory_ids JSONB NOT NULL DEFAULT '[]',  -- 关联记忆 ID 集合
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_entity_tenant_user_norm
        UNIQUE (tenant_id, user_id, normalized_text)
);
```

**设计要点**：`normalized_text` 唯一约束做实体合并主键；`INSERT ON CONFLICT` 时自动 `jsonb_agg` 去重合并 `linked_memory_ids`。检索时 `boosts_for_query` 对查询提取实体 -> 匹配实体 -> 给关联记忆加分（权重 0.5）。

---

### 5. `memory_audit_events` — 审计表

> Migration: `006_memory_observability.sql`

```sql
CREATE TABLE memory_audit_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    memory_id   TEXT,                           -- 关联对象 ID（L1/L2 记忆或 L3 belief）
    layer       TEXT NOT NULL CHECK (layer IN ('L1','L2','L3')),
    object_type TEXT NOT NULL,                  -- memory_item | episodic_memory | profile_belief
    event       TEXT NOT NULL CHECK (event IN (
                    'ADD','UPDATE','CONFIRM',
                    'COMPLETE','SUPERSEDE','COEXIST','DELETE','FORGET')),
    old_data    JSONB,                          -- 变更前快照
    new_data    JSONB,                          -- 变更后快照
    reason      TEXT,                           -- 中文原因说明
    source      TEXT NOT NULL DEFAULT 'system', -- episode_builder | profile_distiller | api
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### 事件语义按层区分

| 层 | 事件 | 含义 |
|---|---|---|
| L1 | ADD | 新语义记忆写入 |
| L2 | ADD | 新 episode 创建 |
| L2 | UPDATE | episode 延续（摘要合并） |
| L2 | COMPLETE | 旧 episode 关闭 |
| L3 | ADD | 新 belief 创建（未匹配到已有） |
| L3 | CONFIRM | 已有 belief 被新证据确认增强 |
| L3 | SUPERSEDE | 旧 belief 被 superseded + 新 belief 创建 |
| L3 | COEXIST | 条件不同的新 belief 与旧 belief 并存 |

---

### 6. SQLite `history` + `messages`

> 文件: `stores/sqlite_history.py`

#### history 表

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT (UUID) | PRIMARY KEY |
| `memory_id` | TEXT | 关联记忆 ID |
| `old_memory` | TEXT | 变更前内容 |
| `new_memory` | TEXT | 变更后内容 |
| `event` | TEXT | ADD/UPDATE/DELETE |
| `created_at` | DATETIME | |
| `updated_at` | DATETIME | |
| `is_deleted` | INTEGER | 默认 0 |
| `actor_id` | TEXT | |
| `role` | TEXT | |

#### messages 表

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT (UUID) | PRIMARY KEY |
| `tenant_id` | TEXT | 隔离 |
| `user_id` | TEXT | 隔离 |
| `session_scope` | TEXT | `tenant_id=x&user_id=y&occupant_id=z&session_id=w` |
| `role` | TEXT | user/assistant/system |
| `content` | TEXT | |
| `name` | TEXT | |
| `created_at` | DATETIME | |

Mem0 OSS 兼容层。`session_scope` 格式见 `core/session.py:18`（URL-escape 拼接）。`messages` 保存最近 K=10 条对话，供 add 的 Phase 0 提取上下文。

---

## 四、数据流与字段映射关系

### 层间引用链

```
memory_audit_events.memory_id ──► memory_items.id (L1/L2)
                                 │      ▲
                                 │      └─ metadata.source_memory_ids (L2→L1)
                                 │
profile_beliefs.evidence_memory_ids ──► memory_items.id (L3→L1)
profile_beliefs.evidence_episode_ids ─► memory_items.id (L3→L2)
memory_entities.linked_memory_ids ────► memory_items.id (实体→L1)
sqlite history.memory_id ────────────► memory_items.id
```

### 内部 dataclass ↔ 表 ↔ API 模型三层转换

```
DB 行 (psycopg dict-row)
  ↔ StoredMemory / StoredBelief / StoredEntity  (dataclass, stores/base.py)
      ↔ MemoryItem / BeliefItem                  (Pydantic, core/models.py, to_public_dict 剥离 embedding)
          ↔ API JSON 响应
```

`StoredMemory` 多了 `score`（检索时填充的运行时字段，不落库）；`MemoryItem` 多了 `event`（ADD 标记）和 `score_details`（融合分明细）。embedding 在 `to_public_dict` 时被剔除——1024 维向量永不外泄给 API。

### 核心 dataclass 字段一览

#### StoredMemory（`stores/base.py:14`）

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `id` | `str` | (必填) | UUID |
| `content` | `str` | (必填) | 记忆正文 |
| `tenant_id` / `user_id` | `str` | (必填) | 隔离 |
| `vehicle_id` | `str` | `""` | |
| `occupant_id` | `str` | `"primary"` | |
| `session_id` / `scene` / `source` | `str` | `""` / `""` / `"conversation"` | |
| `memory_type` | `str` | `"semantic_memory"` | |
| `content_hash` | `str` | `""` | |
| `text_lemmatized` | `str` | `""` | |
| `embedding` | `list[float] \| None` | `None` | |
| `metadata` | `dict` | `{}` | |
| `score` | `float \| None` | `None` | 运行时检索分数 |
| `created_at` / `updated_at` | `datetime \| None` | `None` | |
| `embedding_model` / `embedding_dims` | `str \| None` / `int \| None` | `None` | |

#### StoredBelief（`stores/base.py:53`）

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `id` | `str` | (必填) | UUID |
| `tenant_id` / `user_id` | `str` | (必填) | |
| `occupant_id` | `str` | `"primary"` | |
| `subject` | `str` | `"User"` | SAV 主体 |
| `attribute` | `str` | `""` | SAV 属性 |
| `value` | `str` | `""` | SAV 值 |
| `conditions` | `dict` | `{}` | |
| `stability` | `str` | `"episode"` | episode/recurring/identity |
| `status` | `str` | `"active"` | active/superseded |
| `confidence` | `float` | `0.5` | |
| `support_count` | `int` | `1` | |
| `evidence_memory_ids` | `list[str]` | `[]` | → L1 |
| `evidence_episode_ids` | `list[str]` | `[]` | → L2 |
| `attribute_embedding` | `list[float] \| None` | `None` | |
| `valid_from` / `valid_to` | `datetime \| None` | `None` | |
| `score` | `float \| None` | `None` | 运行时匹配分数 |

#### AuditEvent（`stores/audit.py:44`）

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `id` | `str` | (必填) | UUID |
| `tenant_id` / `user_id` | `str` | (必填) | |
| `memory_id` | `str \| None` | `None` | |
| `layer` | `str` | `""` | L1/L2/L3 |
| `object_type` | `str` | `""` | |
| `event` | `str` | `""` | ADD/UPDATE/CONFIRM/... |
| `old_data` / `new_data` | `dict \| None` | `{}` | |
| `reason` | `str \| None` | `None` | |
| `source` | `str` | `"system"` | |
| `created_at` | `datetime \| None` | `None` | |

### 去重与幂等的三道防线

| 防线 | 机制 | 位置 |
|---|---|---|
| 写前 LLM 语义去重 | 提取时参考已有记忆列表 | `extractor.py` |
| 写前 hash 去重 | `drop_duplicate_texts` MD5 集合比对 | `deduplicator.py` |
| 写时 DB 约束 | `UNIQUE(tenant,user,content_hash) ON CONFLICT DO NOTHING` | `pgvector.py:294` |

---

## 五、检索链路与 rerank——LLM 收到什么

### 检索全景（九步 + rerank）

`search()` → `search_result()`（`memory.py:933`）的完整链路：

```
search(query)
  │
  ├─ Step 1-8: 纯计算，不调 LLM（retriever.py:48-164）
  │   ① 查询预处理（lemmatize + 实体提取）
  │   ② embed 查询
  │   ③ 向量检索过取 max(top_k*4, 60) 条
  │   ④ BM25 关键词检索
  │   ⑤ BM25 分数 sigmoid 归一化
  │   ⑥ 实体 boost（查询实体 → memory_entities 匹配 → 关联记忆加分）
  │   ⑦ 语义+关键词结果并集
  │   ⑧ score_and_rank 融合打分（semantic + BM25 + entity_boost + temporal）
  │      + conflict_resolution 冲突过滤（规则判定 superseded 残留）
  │
  ├─ episode 证据扩展（_expand_episode_evidence, memory.py:973）
  │   命中的 L2 episode 沿 metadata.source_memory_ids 拉回 L1 证据进候选池
  │
  ├─ 加载 Profile（L3 active beliefs + narrative 快照）
  │
  └─ Step 9: rerank —— 检索中唯一调 LLM 的地方（layers/reranker.py:23）
       传给 LLM：当前日期 + 查询原文 + 完整画像 + 候选池（默认 32 条 = rerank_candidate_limit）
       LLM 返回 selected_ids（有序子集）
       ↓
     top_k 条最终结果（含 score、metadata、is_current 标注）
```

### rerank prompt 实例（`reranker.py:52-58`）

以"空气质量不好，按老规矩调整"为例，LLM 收到的完整 user prompt：

```
## Current date
2026-09-07                              ← 让 LLM 能解析"上周"这类相对时间

## Query
外面的空气质量不好，按老规矩给我调整一下   ← 用户原话

## Profile                              ← L3 画像（narrative + active beliefs 列表）
{
  "narrative": "User preferred_temperature: 26°C\nUser music_preference: 爵士乐 ({"season": "summer"})",
  "beliefs": [
    {"subject":"User","attribute":"preferred_temperature","value":"26°C",
     "stability":"recurring","confidence":0.8},
    {"subject":"User","attribute":"music_preference","value":"爵士乐",
     "conditions":{"season":"summer"},"stability":"episode"}
  ]
}

## Candidates                           ← 过取后的候选记忆（默认 32 条）
[
  {"id":"0","layer":"semantic_memory","text":"车窗关闭",
   "occurred_at":"2026-07-12T09:00:00+08:00","score":0.61},
  {"id":"1","layer":"semantic_memory","text":"空调内循环开启",
   "occurred_at":"2026-07-12T09:00:00+08:00","score":0.58},
  {"id":"2","layer":"semantic_memory","text":"空气净化调到3档",
   "occurred_at":"2026-07-15T09:00:00+08:00","score":0.55},
  {"id":"3","layer":"episodic_memory","text":"空气质量差时用户要求关窗、内循环、净化3档",
   "occurred_at":"2026-07-15T09:00:00+08:00","score":0.52},
  {"id":"4","layer":"semantic_memory","text":"空调温度设定为22度",
   "occurred_at":"2026-08-01T08:00:00+08:00","score":0.49},
  ... 共 32 条，含无关项（如"用户去西湖游玩"）
]

# Output:
```

System prompt（`prompts.py:95` RERANK_SYSTEM_PROMPT）的指令要点：

- 从**语义**判断哪些候选真正有助于回答，不用关键词路由
- 相对时间（"上周"/"last week"）由 Current date + 候选 `occurred_at` 推算，`time_scope` 可返回 `{"from","to"}` 窗口
- **问"用户是谁/通常要什么"优先 profile beliefs；问"某次经历"优先 episodes**
- 丢弃 superseded 和无关项

LLM 返回：

```json
{"selected_ids": ["3", "0", "1", "2"], "time_scope": null}
```

Python 将 fake id 映射回真实 memory id，按此顺序取 top_k。上例中 id=4（过期的 22 度）被 LLM 排除——因为 Profile 里 `preferred_temperature=26°C` 已经说明 22 度不是当前值；"西湖游玩"因语义无关被排除。

### rerank 的价值

向量分只看文本相似度。LLM rerank 额外能理解：

| 能力 | 示例 |
|---|---|
| 指代消解 | "老规矩" = 那组车控操作记忆（id 0/1/2） |
| 时间窗推算 | "上周的餐厅" → 从 occurred_at 里筛出对应自然周 |
| 当前值判断 | 22 度 vs Profile 的 26°C → 排除过期值 |
| 画像优先 | "我一般听什么" → 直接选 profile 命中的音乐偏好 |

**beliefs 的 stability/confidence 如何参与这一步**：rerank prompt 的 Profile 段把每条 active belief 的 `stability` 和 `confidence` 一并传给 LLM（见上方实例 JSON）。它们不进任何打分公式，而是作为 LLM 判断"哪条画像可信、哪条候选过期"的依据——`recurring` + 高 confidence 的 belief 比 `episode` 级的更能压过候选池里的相似文本。这就是 L3 画像影响最终检索结果的唯一路径。

**容错**：rerank LLM 调用失败时降级返回向量排序的前 top_k（`memory.py:962`）；LLM 选中为空时同样兜底（`reranker.py:83`）。rerank 永不阻塞检索主链路。

---

## 六、设计评述

### 亮点

1. **L1/L2 同表**：episode 可被统一语义检索，`_expand_episode_evidence` 沿 `source_memory_ids` 拉证据，避免跨表 join；靠部分唯一索引（005）维护 episode 不变式
2. **L3 独立表**：belief 是 SAV 结构化数据，主键匹配用 embedding 但检索走 `list_active`，画像语义明确
3. **审计先行**：所有层写入都产生带前后快照的事件流，`safe_append` 失败静默，观测不侵入主链路
4. **evidence_ids 校验 + stability 降级**：两道 Python 硬防线对冲 LLM 不可靠性
5. **scope 六字段平铺**：列级索引支持高效的隔离过滤下推

### 已知弱点

1. **检索端冲突过滤与 L3 supersede 语义重叠**：`conflict_resolution.py` 的 13 条中文正则 + 硬编码属性映射，与 L3 的 supersede 状态有职责重叠——两套机制可能不一致
2. **`metadata` 承载过多关键状态**：`episode_status`、`occurred_at`、`source_memory_ids` 都在 JSONB 里，`->>'episode_status'` 无法用普通索引，只能靠 GIN
3. **`content_hash` 掺 episode id 的 hack**：同一 episode 的每次更新都产生新 hash，失去幂等意义
4. **实体提取中文覆盖面窄**：`_CJK_AFTER_HINT_RE` 只匹配 `名叫/去/到/导航到` 后跟 2-12 个汉字，大量中文实体无法被提取
5. **`_COCKPIT_PATTERNS` 硬编码归类风险**：`\d+度` 一律归到"空调温度"，可能与座椅角度、车窗开合度等冲突
