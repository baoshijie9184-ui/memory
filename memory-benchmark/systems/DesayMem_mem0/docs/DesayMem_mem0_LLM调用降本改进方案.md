# DesayMem_mem0 LLM 调用降本改进方案

## 1. 改进目标

当前一次完整车机交互最多包含 5 次 LLM 调用：

| 阶段 | 当前调用次数 | 所属模块 |
|---|---:|---|
| 车机回复 | 1 | 上游车机 Agent |
| 检索 Rerank | 1 | `src/desaymem/layers/reranker.py` |
| L1 记忆抽取 | 1 | `src/desaymem/extraction/extractor.py:78` |
| L2 事件判断 | 1 | `src/desaymem/layers/episodes.py:50` |
| L3 画像蒸馏 | 1 | `src/desaymem/layers/distiller.py:104` |

这会带来三个问题：单轮响应链路长、云端 token 成本高、用户高频对话时 GPU 并发压力大。

本方案目标是：

```text
普通在线对话：1～2次 LLM
L2/L3：退出会话或累计多轮后异步处理
简单检索：不调用 LLM Rerank
无价值闲聊：不写长期记忆
```

## 2. 推荐总体方案

采用“在线轻量链路 + 后台分层演化”的结构。

```text
用户输入
→ 向量 + BM25 + 实体 + 时间检索
→ 车机 Agent 生成回复/调用工具
→ 轻量记忆门控
→ 保存原始问答
→ L1 抽取或接收 Agent 同步输出的候选记忆
→ 返回用户

后台任务
→ 按会话聚合 L1
→ 生成/更新 L2
→ 达到条件后蒸馏 L3
```

在线路径不再每轮生成 L2、L3，也不默认调用 LLM Rerank。

## 3. LLM 调用调整

| 功能 | 当前策略 | 改进策略 |
|---|---|---|
| 车机回复 | 每轮调用 | 保留 |
| L1 抽取 | 每轮调用 | 仅有记忆价值时调用，或与回复合并 |
| L2 | 每轮调用 | 会话结束或累计 5～10 轮调用一次 |
| L3 | 每轮调用 | 新证据达到阈值或定时批量调用 |
| Rerank | 每次 search 调用 | 默认关闭，只对复杂问题启用 |

目标调用量：

| 场景 | 当前 | 第一阶段改进 | 最终优化 |
|---|---:|---:|---:|
| 普通问答 | 5次 | 2次 | 1次 |
| 无记忆价值闲聊 | 5次 | 1次 | 1次 |
| 简单偏好查询 | 5次 | 1～2次 | 1次 |
| 会话结束 | 每轮均处理 | 额外1次 L2，按需1次 L3 | 后台批处理 |

## 4. 在线写入流程改造

### 4.1 增加记忆价值门控

在进入 `MemoryExtractor.extract()` 前判断本轮是否值得写长期记忆。

建议先采用低成本规则，不新增 LLM 调用：

```python
def should_extract_memory(messages: list[dict], metadata: dict) -> bool:
    text = " ".join(str(row.get("content", "")) for row in messages)

    if metadata.get("force_memory"):
        return True

    if metadata.get("tool_execution_status") == "success":
        return True

    memory_signals = (
        "喜欢", "不喜欢", "习惯", "以后", "默认", "记住",
        "家住", "公司在", "经常", "每次", "通常", "改成",
    )
    return any(signal in text for signal in memory_signals)
```

处理方式：

- 命中门控：保存消息并进行 L1 抽取；
- 未命中门控：只保存最近消息，不产生长期 L1；
- `force_memory=true`：用于用户明确说“记住”；
- 车辆操作只有工具执行成功后，才允许形成“已执行”事实。

建议新增：

```text
src/desaymem/extraction/gate.py
MemoryGate.should_extract()
```

在以下位置接入：

```text
src/desaymem/core/memory.py
DesayMemory.add_result()
```

### 4.2 第一阶段保留独立 L1 抽取

第一阶段不要立刻合并车机 Agent 和记忆抽取，先将每轮调用从 5 次降低到 2 次：

```text
车机回复 1次
L1抽取 0或1次
L2/L3 后台处理
Rerank 默认关闭
```

这样改造范围较小，且不会把记忆 Prompt 强耦合进车机主模型。

### 4.3 第二阶段合并回复与 L1 抽取

让车机 Agent 一次返回：

```json
{
  "reply": "好的，空调已调到22度。",
  "tool_calls": [
    {
      "name": "set_climate_temperature",
      "arguments": {"temperature": 22}
    }
  ],
  "memory_candidates": [
    {
      "text": "用户在驾驶时将空调设置为22度",
      "attributed_to": "user",
      "memory_kind": "operation"
    }
  ]
}
```

记忆后端新增“可信候选写入”模式，直接校验、去重、向量化，不再调用 L1 抽取模型。

但必须满足：

- 使用 JSON Schema 强制输出格式；
- 后端不完全信任候选，仍校验字段和用户作用域；
- 工具执行失败时不能保存“操作已完成”；
- 保留现有独立抽取作为降级路径。

## 5. L2 异步化

当前 `_evolve_layers()` 在每次 add 后调用 `_upsert_episode()`，导致每轮执行一次 L2 LLM。

改为以下任一条件触发：

```text
session 明确结束
连续 10 分钟无新消息
同一 session 累计 5～10 轮
上游发送 flush_session=true
```

新增任务表或队列：

```json
{
  "task_type": "BUILD_EPISODE",
  "tenant_id": "oem_a",
  "user_id": "user_001",
  "session_id": "trip_001",
  "status": "pending",
  "retry_count": 0
}
```

后台 Worker 一次读取该会话尚未归档的 L1，整体生成 L2：

```text
同一会话 8 轮对话
当前：8次 L2 LLM
改进：会话结束后1次 L2 LLM
```

建议新增：

```text
src/desaymem/jobs/models.py
src/desaymem/jobs/repository.py
src/desaymem/jobs/worker.py
src/desaymem/jobs/episode_job.py
```

原 `_upsert_episode()` 可以继续复用，只需要支持一次传入一批尚未归档的 L1。

## 6. L3 条件触发和批处理

L3 不需要每轮更新。建议按“画像槽”累计证据，再决定是否调用 Distiller。

触发条件：

| 条件 | 是否触发 L3 |
|---|---|
| 用户明确说“我喜欢/以后默认” | 立即或高优先级异步触发 |
| 同类行为跨2个不同日期 | 触发 |
| 同一会话重复操作 | 不立即升级长期偏好 |
| 画像可能冲突，如22度→24度 | 触发 |
| 普通事实且无画像价值 | 不触发 |
| 累计10条未处理画像证据 | 批量触发 |

后台一次可以处理多个相关 L1：

```text
用户最近一周的音乐证据
→ 一次 Distiller 调用
→ 区分明确偏好、行为频率、推断偏好
→ 批量 CREATE/CONFIRM/COEXIST/SUPERSEDE
```

建议给 L1 增加：

```json
{
  "profile_processed": false,
  "profile_slot": "music.artist_preference",
  "evidence_type": "explicit_preference"
}
```

处理成功后标记 `profile_processed=true`，避免重复消耗。

## 7. Rerank 按复杂度启用

默认使用现有：

```text
向量召回 + BM25 + 实体加分 + 时间分 + 冲突消解
```

下列简单查询不调用 LLM Rerank：

```text
我喜欢多少度？
我喜欢哪个歌手？
公司地址在哪里？
```

只有以下情况启用：

- “上周那次”“之前类似的”等时间指代；
- 多跳问题；
- 候选分数接近，第一名和第二名差值小于阈值；
- 同时命中多个冲突值；
- 查询需要组合 L1、L2 和 L3。

示例路由：

```python
def need_llm_rerank(query: str, hits: list) -> bool:
    complex_signals = ("上周", "之前那次", "为什么", "后来", "经常", "最常")
    if any(word in query for word in complex_signals):
        return True
    if len(hits) >= 2 and abs((hits[0].score or 0) - (hits[1].score or 0)) < 0.05:
        return True
    return False
```

建议修改：

```text
src/desaymem/core/memory.py:933-971
DesayMemory.search_result()
```

将全局 `enable_rerank` 改为：

```text
rerank_mode = off | auto | always
```

生产默认使用 `auto`。

## 8. 配置项设计

建议在 Settings 中增加：

```env
MEMORY_GATE_ENABLED=true
MEMORY_GATE_MODE=rule

L1_EXTRACTION_MODE=separate
# separate | agent_candidates | hybrid

EPISODE_EVOLUTION_MODE=async
EPISODE_MAX_PENDING_TURNS=8
EPISODE_IDLE_TIMEOUT_SECONDS=600

PROFILE_EVOLUTION_MODE=async
PROFILE_MIN_EVIDENCE=2
PROFILE_BATCH_SIZE=20

RERANK_MODE=auto
RERANK_SCORE_GAP_THRESHOLD=0.05

MEMORY_JOB_MAX_RETRIES=3
```

开发阶段可使用同步模式方便调试，生产阶段默认异步。

## 9. API 调整

保持现有 `/v1/memories` 兼容，新增可选字段：

```json
{
  "memory_candidates": [],
  "force_memory": false,
  "flush_session": false,
  "tool_results": [],
  "evolution_mode": "async",
  "idempotency_key": "trip_001_turn_008"
}
```

建议返回：

```json
{
  "memories": [],
  "extracted": 0,
  "memory_gate": "skipped",
  "episode_status": "queued",
  "profile_status": "not_triggered",
  "job_ids": ["job_uuid"]
}
```

这样前端和测试人员能够区分“没有记忆价值”和“后台处理失败”。

## 10. 失败与降级策略

| 失败位置 | 降级方式 |
|---|---|
| L1 抽取失败 | 保存原始消息，任务进入重试队列 |
| L2 失败 | 保留 L1，稍后重试，不影响在线回答 |
| L3 失败 | 保留 L1/L2，不更新画像 |
| Rerank 失败 | 返回混合排序结果 |
| 队列不可用 | 写数据库任务表，恢复后继续处理 |
| Agent 未返回候选 | 回退到独立 L1 抽取 |
| 工具执行失败 | 保存“执行失败”事件，不保存“已成功设置”事实 |

任务必须使用幂等键，避免网络重试导致重复 L1、L2 或 L3。

## 11. 是否需要 Redis

第一阶段不强制引入 Redis。可以先使用 PostgreSQL 任务表，通过 `FOR UPDATE SKIP LOCKED` 实现简单 Worker：

```text
memory_jobs
id / task_type / scope / payload / status / retry_count / next_run_at
```

当任务规模、Worker 数量和实时性要求明显提高后，再引入 Redis Streams、Celery 或 Kafka。这样可减少早期部署复杂度。

## 12. 分阶段实施

### 第一阶段：低风险降本

1. 默认关闭全量 LLM Rerank，增加 `auto` 模式；
2. 增加规则型 MemoryGate；
3. L2/L3 增加开关，普通 add 不同步演化；
4. 增加手动 `flush_session` 接口；
5. 保留独立 L1 抽取。

预期普通轮次由 5 次降至 1～2次。

### 第二阶段：后台任务

1. 增加 PostgreSQL `memory_jobs`；
2. L2 会话结束批处理；
3. L3 按证据阈值批处理；
4. 增加重试、幂等和任务状态接口。

### 第三阶段：回复与抽取合并

1. Agent 输出 `reply + tool_calls + memory_candidates`；
2. 后端校验并直接写入候选；
3. 低置信度候选回退到独立抽取；
4. 对比一次生成与独立抽取的准确率。

## 13. 测试方案

必须新增：

```text
tests/unit/test_memory_gate.py
tests/unit/test_rerank_router.py
tests/unit/test_memory_jobs.py
tests/integration/test_async_evolution.py
tests/scenarios/test_low_cost_cockpit_flow.py
```

关键测试：

| 用例 | 预期 |
|---|---|
| “今天天气不错” | 不抽取 L1 |
| “我喜欢周杰伦” | 触发 L1 |
| “播放周杰伦”且工具失败 | 不生成成功操作事实 |
| 8轮同一会话 | 只调用1次 L2 |
| 两天重复22度 | 触发 L3 recurring |
| 简单精确查询 | 不调用 Rerank |
| “上周那次去哪了” | 启用复杂查询流程 |
| Worker 重复消费 | 不产生重复数据 |

## 14. 验收指标

| 指标 | 建议目标 |
|---|---:|
| 普通对话记忆后端 LLM 调用 | ≤1次/轮 |
| 无记忆价值对话后端 LLM 调用 | 0次/轮 |
| 完整链路 LLM 调用 | 通常1～2次/轮 |
| L2 调用降低 | ≥80% |
| L3 调用降低 | ≥80% |
| 简单 search 不使用 Rerank 比例 | ≥80% |
| L1 Recall/F1 | 不低于当前基线 |
| Search Recall@5 | 下降不超过1个百分点 |
| 在线 add P95 | 明显低于当前同步三层流程 |
| 异步任务最终成功率 | ≥99.5% |

## 15. 最终建议

近期最合理的落地版本是：

```text
在线：混合检索（无LLM Rerank）+ 车机回复 + 按需L1抽取
离线：会话结束生成L2 + 多证据触发L3
```

不要第一步就把回复和 L1 抽取完全合并。先完成 Rerank 自动路由、MemoryGate 和 L2/L3 异步化，建立准确率与调用量基线；确认效果稳定后，再合并 Agent 回复与 L1 候选输出。

该路线改造风险较低，能够先将普通一轮交互从最多5次 LLM 调用降到1～2次，同时保留现有 L1/L2/L3 架构和审计能力。
