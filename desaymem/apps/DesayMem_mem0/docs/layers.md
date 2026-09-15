# L2 事件摘要与 L3 用户画像

> 最后更新: 2026-09-03

DesayMem_mem0 原先只有一层：对话抽成原子事实，全部写入 `memory_items` 做向量检索。模糊指令（「经常」「上周那次」「老规矩」）会失败，因为一次经历被拆碎，稳定偏好和一次性事实挤在同一个 HNSW 里。这里的“上周”只存在于查询：L2 保存事件及其绝对发生时间，查询时再由精排 LLM 根据当前日期计算“上周”对应的时间范围。

本层在 **不引入关键词路由、不引入领域词表** 的前提下，加上两层派生记忆：

| 层 | 存什么 | 放哪 | 怎么读 |
|----|--------|------|--------|
| L1 事实 | 自包含事实句 | `memory_items`，`memory_type=semantic_memory` | 向量 + BM25 |
| L2 事件 | 一次经历的摘要 | **同一张** `memory_items`，`memory_type=episodic_memory` | 向量召回 |
| L3 画像 | 当前信念 | **另一张表** `profile_beliefs` | 按 `tenant_id+user_id` 直接读 |

L1 是真源。L2/L3 都可以从 L1 重建。LLM 只输出带证据 ID 的 JSON，Python 是唯一写库入口。

---

## 1. 为什么 L2 和 L3 存放位置不同

L2 必须能被「那次去公园 / 上周那顿饭」这类 query **语义召回**，所以摘要要有 embedding，和事实共用 pgvector。L2 不应把“上周”“昨天”等相对时间固化进摘要；它保存 `occurred_at`（事件开始）和续写时的 `occurred_end`（最新观察）绝对时间。相对时间窗口由检索精排阶段结合当前日期解释。

L3 回答的是「这个人现在是什么样」，访问方式是主键读取，不是 cosine top-k。若把信念也写成普通向量行，会再次被一年的事实淹没。`attribute_embedding` 只用于蒸馏时合并「同一条信念的不同说法」，不参与用户 query 的主召回。

---

## 2. 写入

```text
对话
  → 现有七阶段 ADD 抽取（Prompt 的 Summary 槽填入 L3 叙事）
  → L1 事实入库
  → EpisodeBuilder：对「当前未关闭事件 + 新事实」做一次 JSON 判断
        continues=true  → 更新同一条 episodic 行
        continues=false → 关闭旧事件，插入新摘要
  → ProfileDistiller：用新事实向量取近邻簇，LLM 产出 beliefs
        Python 校验 evidence_ids 后 CREATE / CONFIRM / SUPERSEDE / COEXIST
  → 用当前 active beliefs 拼叙事，写入 user_profile_snapshots
```

事件边界和习惯判断都由模型根据证据完成。时间间隔、向量相似度只作为 **上下文** 传给模型，不是关键词规则。

防幻觉不变量（与领域无关）：

- 信念的 `evidence_ids` 必须落在本簇真实 L1 id 上，否则丢弃。
- `stability=recurring` 但证据不足 2 条时，Python 降为 `episode`。一次经历不能变成习惯。

---

## 3. 检索

不做 query 路由表。每条 query：

1. 读取该用户全部 active L3（O(1)）。
2. 在 `memory_items` 上对 L1+L2 做现有九阶段召回（over-fetch）。
3. 精排模型根据 query 语义、当前日期、画像、候选绝对时间戳做选择；“上周”等相对时间在这里计算，而不是由 L2 生成。
4. 返回 `{ memories, profile }`。画像始终在结果里，不依赖向量是否打中。

精排失败时退回向量排序 + 画像，没有关键词 fallback 分类器。

---

## 4. 数据模型

### L2 `memory_items`（episodic_memory）

`content` 为摘要。`metadata` 约定：

```json
{
  "episode_status": "active | complete",
  "source_memory_ids": ["l1-uuid", "..."],
  "occurred_at": "2026-03-12T10:00:00+08:00",
  "confidence": 0.86
}
```

当前未关闭事件：同一 `tenant_id+user_id+occupant_id` 下 `episode_status=active` 且 `updated_at` 最新的一条。

### L3 `profile_beliefs`

开放字段，没有空调/导航之类的枚举：

- `subject` / `attribute` / `value`：自然语言
- `conditions`：证据里出现的约束（可空）
- `stability`：`episode` | `recurring` | `identity`
- `status`：`active` | `superseded`
- `evidence_memory_ids` / `evidence_episode_ids`

`user_profile_snapshots` 存该用户一份叙事文本，供抽取 Prompt 的 `## Summary` 使用。

---

## 5. 对模糊指令的作用

| 指令类型 | 靠哪一层 |
|----------|----------|
| 「上周类似的那次」 | 精排 LLM 将“上周”换算成绝对时间窗口，再从带 `occurred_at` 的 L2 候选中选择；需要时展开 `source_memory_ids` |
| 「经常 / 按习惯 / 老规矩」 | 只信 L3 `recurring` 且证据条数够的信念 |
| 「帮我开到老样子」 | L3 每轮必带，不必碰向量 |
| 「改成另一种」 | L1 旧事实保留；L3 新行 `active`，旧行 `superseded` |

---

## 6. 开关与迁移

环境变量：`ENABLE_EPISODES` / `ENABLE_PROFILE` / `ENABLE_RERANK`（默认 true）。

已有库需要执行：

```bash
python -m desaymem.cli apply
```

空库走 `migrations/*.sql`，`004_layers.sql` 会建 `profile_beliefs` 和 `user_profile_snapshots`。

HTTP：`GET /v1/users/{user_id}/profile`；`POST /v1/memories/search` 的响应现含 `profile`。`POST /v1/memories` 在抽出事实后可能附带 `episode` 与 `beliefs_applied`。

---

## 7. 明确不做

- 不按「上次 / 经常 / 改为」等词路由或过滤
- 不把 Edge 的 Skill / 工具轨迹搬到云端
- 不让 LLM 直接 UPDATE/DELETE L1 事实行
- 不把 L3 当作又一条 `semantic_memory` 丢进 HNSW
