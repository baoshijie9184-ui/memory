# DesayMem 近一年车机记忆 `memory.add / memory.search` 测试指南

## 1. 测试目标

本测试用于验证车载云端记忆系统是否能够从近一年连续车机对话和操作中形成稳定记忆，并处理以下模糊指令：

1. 我们经常去的亲子餐厅。
2. 根据我爱吃的菜，找个最近的地方。
3. 外面的空气质量不好，按老规矩给我调整一下。
4. 帮我找一下西餐厅，按照习惯进行点餐。
5. 根据上周的亲子餐厅，帮我预定一个差不多的餐厅。

测试脚本：`scripts/run_yearlong_memory_test.py`

输入文件：

- `data/yearlong_cockpit_sessions.jsonl`：495次完整驾驶会话。
- `data/yearlong_fuzzy_memory_gold.json`：模糊指令标准答案。
- `data/yearlong_cockpit_dialogue_transcript.txt`：供人工查看的对话文本，不用于程序导入。

输出文件：

- `data/yearlong_memory_test_report.json`：机器可读的测试报告。

## 2. 前置条件

确认 `.env` 已配置以下内容：

```env
LLM_MODEL=qwen3.8-flash
LLM_API_KEY=你的密钥
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

EMBEDDING_MODEL=qwen3.7-text-embedding-flash
EMBEDDING_API_KEY=你的密钥
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_DIMS=1024

POSTGRES_DSN=postgresql://用户名:密码@地址:5432/数据库名
HISTORY_DB_PATH=history.db
```

数据库和 pgvector 必须可用。进入项目根目录后安装项目：

```bash
pip install -e .
```

## 3. 第一步：离线校验

该步骤不调用大模型、不连接数据库、不会写入数据：

```bash
python scripts/run_yearlong_memory_test.py --mode dry-run
```

看到以下内容表示数据文件正常：

```text
"valid": true
"session_count": 495
"gold_case_count": 5
Dry-run通过
```

## 4. 第二步：导入核心场景数据

首次测试不要立即导入全部495次会话。先导入亲子餐厅、饮食偏好、空气质量、西餐习惯和相似餐厅等核心场景：

```bash
python scripts/run_yearlong_memory_test.py --mode import --profile core
```

脚本逐条调用：

```python
await memory.add(
    payload["messages"],
    user_id=payload["user_id"],
    tenant_id=payload["tenant_id"],
    vehicle_id=payload["vehicle_id"],
    session_id=payload["session_id"],
    scene=payload["scene"],
    source=payload["source"],
    metadata=payload["metadata"],
    infer=True,
)
```

固定测试身份：

```text
tenant_id = oem_desay_demo
user_id   = family_driver_001
vehicle_id = vehicle_demo_001
```

不要在导入完成后更换 `tenant_id` 或 `user_id`，否则搜索不到刚才的记忆。

### 快速冒烟测试

只导入5条：

```bash
python scripts/run_yearlong_memory_test.py --mode import --profile core --limit 5
```

使用原文入库、不调用LLM抽取：

```bash
python scripts/run_yearlong_memory_test.py --mode import --profile core --limit 5 --raw
```

`--raw` 只适合验证数据库和向量链路。正式测试模糊偏好时必须使用默认的 `infer=True`。

## 5. 第三步：执行 `memory.search`

核心数据导入成功后运行：

```bash
python scripts/run_yearlong_memory_test.py --mode search --top-k 10
```

脚本对每条模糊指令执行：

```python
hits = await memory.search(
    query,
    user_id="family_driver_001",
    tenant_id="oem_desay_demo",
    top_k=10,
    threshold=0.0,
)
```

搜索结果的正文键是 `content`，不是 `memory`：

```python
for hit in hits:
    print(hit["content"])
    print(hit["score"])
    print(hit["metadata"])
```

## 6. 一条命令完成导入和搜索

```bash
python scripts/run_yearlong_memory_test.py --mode all --profile core --top-k 10
```

运行完成后查看：

```text
data/yearlong_memory_test_report.json
```

如果需要重新开始，可以显式加入 `--reset`：

```bash
python scripts/run_yearlong_memory_test.py --mode all --profile core --reset
```

`--reset` 会删除 `oem_desay_demo + family_driver_001` 下的全部记忆，仅能用于合成测试用户。

## 7. 全量一年测试

核心场景通过后，再导入全部495次会话：

```bash
python scripts/run_yearlong_memory_test.py --mode import --profile full
```

之后运行：

```bash
python scripts/run_yearlong_memory_test.py --mode search --top-k 10
```

全量导入会进行大量LLM抽取和Embedding请求，耗时和费用明显高于核心场景测试。建议先使用核心场景定位问题。

## 8. 通过标准

| 指令 | Top-10应包含的证据 |
|---|---|
| 经常去的亲子餐厅 | 童梦森林、亲子、孩子喜欢 |
| 根据爱吃的菜找最近地点 | 粤菜、少油、不加香菜 |
| 空气不好按老规矩调整 | 关闭车窗、内循环、空气净化3档、23℃ |
| 西餐厅按习惯点餐 | 西冷牛排七分熟、蘑菇汤、柠檬水 |
| 根据上周亲子餐厅找类似餐厅 | 2026-08-29、童梦森林、儿童活动区、宝宝椅 |

脚本按关键词召回率判断：

```text
关键词召回率 >= 80%：通过
关键词召回率 < 80%：未通过
```

关键词只是自动化冒烟指标。正式评测还要人工检查：

- 是否召回了真正相关的证据。
- 是否把一次行为错误判断成“经常”。
- 是否忽略“上周”等时间限制。
- 是否虚构不存在的地点或经历。
- 是否未经确认直接执行点餐、预订或高影响车控。

## 9. `memory.search` 与最终车机回答的关系

`memory.search` 只负责召回证据，不负责完成全部业务动作：

```text
用户模糊指令
  → memory.search召回历史证据
  → Agent判断意图和置信度
  → 查询实时POI/菜单/空气质量
  → 生成车机回答
  → 用户确认
  → 调用导航、车控、点餐或预订Skill
```

例如“根据我爱吃的菜，找个最近的地方”：

- 记忆系统提供粤菜、清蒸鲈鱼、少油、不吃香菜等偏好。
- POI服务提供当前位置、距离、营业状态。
- Agent融合两类结果后才能回答“最近的餐厅”。

因此，仅调用 `memory.search` 不能真实完成“最近地点”“点餐”和“预订”。

## 10. 当前时间检索限制

当前代码导入历史数据时，数据库 `created_at` 使用导入时间。真实历史时间保存在：

```text
metadata.occurred_at
用户消息中的 [YYYY-MM-DD HH:MM]
```

因此当前“上周”主要依靠文本日期语义召回。正式版本应扩展：

```python
filters={
    "occurred_at_start": "2026-08-24T00:00:00+08:00",
    "occurred_at_end": "2026-08-30T23:59:59+08:00",
}
```

并同步修改 `SearchFilters`、API请求模型、pgvector查询和索引。否则无法保证严格的时间范围过滤。

## 11. 常见问题

### 搜索结果为空

依次检查：

1. 导入和搜索是否使用同一个 `tenant_id + user_id`。
2. PostgreSQL中是否已经写入 `memory_items`。
3. Embedding模型和维度是否与数据库一致。
4. 是否误用了过高的 `SEARCH_THRESHOLD`。

测试阶段可以使用：

```bash
python scripts/run_yearlong_memory_test.py --mode search --threshold 0.0
```

### 重复运行没有新增记忆

这是内容哈希去重的正常结果。需要完全重测时，对合成测试用户使用 `--reset`。

### 结果包含偏好但无法形成“老规矩”

说明系统只召回了原子记忆，尚未形成跨事件习惯。需要增加 Cross-event 聚合、画像归纳或 Skill 蒸馏层，不能只调整向量相似度。

### 脚本返回退出码1

代表至少一条导入失败，或至少一个模糊测试关键词召回率低于80%。详细原因查看 `yearlong_memory_test_report.json`。
