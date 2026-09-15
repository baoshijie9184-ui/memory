# 设计 Step 6：九步检索与返回结构

## 1. 检索目标

在不调用 Qwen3-32B 的情况下，返回与当前问题相关的 Fact、Event、Cross-Event 和最新 Profile，并严格执行租户、用户、车辆、乘员、Tag 和时间范围。

一次检索只调用一次 BGE-M3 query embedding。

## 2. 请求结构

```json
{
  "query": "我平时开车喜欢听什么？",
  "tenant_id": "desay",
  "user_id": "user-001",
  "vehicle_id": "vehicle-001",
  "occupant_id": "driver",
  "session_id": "session-001",
  "tags": ["周杰伦"],
  "time_from": null,
  "time_to": null,
  "top_k": 10,
  "vehicle_only": false,
  "explain": false
}
```

必填：`query、tenant_id、user_id、vehicle_id、occupant_id`。`session_id` 只用于补充短期上下文，不替代用户身份过滤。

## 3. 九步检索

沿用 `DesayMem_mem0` 的九步检索骨架，并扩展到三个记忆类型。

### Step 1：校验与预处理

- 校验完整身份字段、query 长度和 top_k 上限。
- 对 query 做 FTS 预处理和已有实体提取。
- 不调用 LLM 做 query rewrite。

### Step 2：生成一次 Query Embedding

- 调用 BGE-M3 得到 1024 维向量。
- 校验维度和有限数值；失败则允许降级为 FTS-only，并在响应中标记。

### Step 3：构造硬过滤条件

- 强制：`tenant_id、user_id、occupant_id、status=active`。
- 默认车辆范围：`vehicle_id = 当前车辆 OR vehicle_id IS NULL`。
- `vehicle_only=true`：只允许当前 `vehicle_id`。
- 可选：`memory_type、occurred_at、tags`。
- Tag 使用 `EXISTS memory_tag_links` 过滤，不拼接进 query 文本。

所有过滤进入 SQL/向量查询，不在召回后用 Python 补过滤，避免越权数据进入候选集。

### Step 4：语义召回

- 分别召回 `fact/event/cross_event`。
- 每类 over-fetch：`max(目标配额 × 4, 20)`，而不是无条件读取大量数据。
- 返回 cosine similarity 和必要字段。

### Step 5：FTS 关键词召回

- 使用 PostgreSQL FTS 对同一过滤范围查询。
- 保存原始 FTS rank。
- 当前实现应称为 FTS；除非后续安装真正 BM25 扩展，否则不对外宣称 PostgreSQL 原生 BM25。

### Step 6：实体加分与分数归一

- 复用 Mem0 实体链接，根据 query 中实体为已关联记忆提供小幅加分。
- 归一化语义、FTS、实体分数。
- 不因实体命中绕过硬过滤。

### Step 7：候选集合并与去重

- 按 `memory_id` 合并语义和 FTS 候选。
- Fact 内容哈希去重；Event/Cross-Event 按 ID 去重。
- 保留各路原始分数，供 `explain=true` 使用。

### Step 8：综合排序与时间冲突消解

- 复用 `DesayMem_mem0` 的 semantic + FTS + entity + temporal 综合排序。
- 时间仅作为小权重的新近度信号，不能压过语义相关性。
- 对同 `memory_key` 的明确更新执行 supersede 过滤。
- 多值偏好继续共存，不按属性名强制只保留一个值。

### Step 9：类型配额、Profile 和上下文组装

- 按类型配额截断，避免 Fact 独占全部结果。
- 直接读取当前 scope 最新 `profile_snapshots`，不做向量搜索。
- 可选读取当前 session 最近消息作为短期上下文。
- 按 token 预算生成最终 Agent Context，并返回来源 ID。

## 4. 默认配额

```text
Fact          top 5，最多 800 tokens
Event         top 3，最多 500 tokens
Cross-Event   top 2，最多 400 tokens
Profile       最新 1 个，最多 300 tokens
系统标记/间隔              约 100 tokens
总计上限                  2000 tokens
```

某层不足时可以把剩余 token 让给其他层，但 Profile 上限保持 300 tokens。超限时按综合分数逐条截断，不从记忆文本中间裁切。

## 5. 时间查询

- 明确传入 `time_from/time_to` 时，对 `occurred_at/occurred_end` 做区间相交过滤。
- 没有显式时间参数时只使用轻量 temporal score，不自行推测“最近、以前、上周”。
- 相对时间应由调用方或 `DesayMem_mem0` 已有时间解析模块转成绝对时间后传入。
- `created_at` 只用于审计，不能替代事件发生时间过滤。

## 6. Tag 过滤

- 多个 Tags 默认采用 AND：结果必须拥有全部请求标签。
- 后续可增加 `tag_mode=any`，V1 API 暂不开放，减少分支。
- Tag 过滤作用于 Fact/Event/Cross-Event；Profile 不通过 Tag 检索。

## 7. 返回结构

```json
{
  "request_id": "search-001",
  "degraded": false,
  "memories": {
    "facts": [
      {
        "id": "fact-id",
        "content": "用户喜欢周杰伦",
        "occurred_at": "2026-09-01T02:00:00Z",
        "score": 0.86,
        "source_topic_id": "topic-id"
      }
    ],
    "events": [],
    "cross_events": []
  },
  "profile": {
    "snapshot_id": "profile-snapshot-id",
    "version": 3,
    "summary": "用户通常喜欢周杰伦和陶喆。"
  },
  "agent_context": "...",
  "usage": {
    "embedding_calls": 1,
    "llm_calls": 0,
    "context_tokens": 1260
  }
}
```

默认不返回 embedding、完整 metadata 和全部证据链。调用 `GET /memories/{id}/lineage` 时再按需展开：

```text
Cross-Event -> Event -> Fact -> Topic -> Message
Profile -> Profile Evidence -> Event -> Fact -> Message
```

## 8. explain 模式

`explain=true` 仅用于测试和调试，额外返回：

```text
semantic_score
fts_score
entity_boost
temporal_score
final_score
applied_filters
filtered_as_superseded
```

它不增加 LLM 调用。生产接口应通过权限控制 explain，避免泄露内部信息。

## 9. 性能和降级

- BGE-M3 不可用：降级为 FTS + Entity，`degraded=true`。
- FTS 不可用：使用向量检索。
- Profile 不存在：返回 null，不临时调用 LLM生成。
- Cross-Event 尚未生成：正常返回 Fact/Event。
- 检索超时：返回已完成的层和 `partial=true`，不编造缺失结果。

默认超时和并发限制通过配置管理；各步骤耗时写入 metrics，不把每次检索完整结果持久化，避免云端磁盘快速增长。

## 10. 本步明确不做

- 不调用 Qwen3-32B 改写或重排。
- 不跨 tenant、user 或 occupant 检索。
- 不默认检索其他车辆的专属记忆。
- 不把 Profile 混入向量候选竞争。
- 不保存每次完整检索响应到数据库和 JSON。
