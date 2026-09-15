# 近一年车机模糊记忆测试集

`scripts/generate_yearlong_cockpit_dataset.py` 生成从每次点火到熄火的连续一年合成数据。数据不是均匀随机文本，而是用重复、变更、近期强化和反例构成习惯证据链。

## 生成

```bash
python scripts/generate_yearlong_cockpit_dataset.py
```

输出：

- `data/yearlong_cockpit_sessions.jsonl`：逐行一个完整驾驶会话，可使用每行的 `api_payload` 调用 `POST /v1/memories`。
- `data/yearlong_fuzzy_memory_gold.json`：5条核心模糊指令的意图、证据、标准结果、澄清条件和硬失败条件。

## 为什么日期同时存在于 metadata 和文本

当前 `POST /v1/memories` 不允许调用方覆盖数据库 `created_at`。批量导入旧数据时，所有数据库创建时间都会接近导入时间。因此生成器同时：

1. 将真实发生时间写入 `metadata.occurred_at`；
2. 在用户消息前加入 `[YYYY-MM-DD HH:MM]`；
3. 保留完整 `started_at/ended_at`。

这能让现有语义检索看到日期，但严格的“上周”过滤最终仍应在检索层增加 `occurred_at` 范围过滤，不能长期依赖模型从文本猜时间。

## 评测原则

模糊记忆测试需要同时判断召回和决策：

- “经常”：至少需要多次、跨时间的重复证据，不能把一次到访称为习惯。
- “最近”：需要当前位置和实时 POI 数据；记忆系统只负责提供菜系、菜品、忌口和价格偏好。
- “老规矩”：召回一组稳定车控操作，但是否自动执行取决于预授权和安全策略。
- “按照习惯点餐”：先选餐厅、校验菜单，再确认数量与价格，不能只靠记忆直接下单。
- “上周类似餐厅”：先做自然周时间过滤，再抽取相似特征，最后确认日期、人数等预订参数。

任何编造经历、忽略时间范围、把一次行为误判为稳定习惯，或未经确认直接预订，都计为硬失败。
