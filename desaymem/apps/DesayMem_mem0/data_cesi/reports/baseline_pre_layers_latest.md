# 云端模糊记忆评测 — baseline_pre_layers

- 时间：2026-09-03T21:11:33+08:00
- 目标：`http://47.115.228.135/memory`
- 健康检查：`{"status": "ok", "app": "DesayMem_mem0", "database": "ok", "embedding_dims": 1024, "llm_model": "qwen3.8-flash", "embedding_model": "qwen3.7-text-embedding-flash"}`
- 数据：`D:\workspace\Desay_mem0_data\yearlong_cockpit_memory_dataset\yearlong_cockpit_sessions.jsonl`
- profile：core，导入会话：63
- 关键词通过：4/5

## 评测逻辑（后续同一套复测）

1. 只走 HTTP，不加载本地 `DesayMemory`，避免未上线代码污染结果。
2. 合成用户固定 `oem_desay_demo` / `family_driver_001`。
3. `--profile core` 只导入带习惯证据的会话（亲子/饮食/空气/西餐/相似餐厅）。
4. 每条 gold query 做一次 `POST /v1/memories/search`。
5. **关键词召回率 ≥ 80%** 记为数值通过，便于新旧版对比。
6. 额外诊断不计入通过率，但必须记录：一次收藏是否被抬成习惯、上周锚点是否出现、窗外到访是否泄漏、是否返回 `profile`/`episodic_memory`。

## 导入

```json
{
  "selected_sessions": 63,
  "succeeded": 63,
  "failed": 0,
  "new_memories": 92,
  "elapsed_seconds": 837.52,
  "failures": []
}
```

## 检索

### fuzzy_01 [PASS] 我们经常去的亲子餐厅

高频到访应指向童梦森林；童趣岛只收藏过一次，不能当成经常去。

- 关键词召回率：100%  命中：['童梦森林', '亲子', '孩子']  缺少：[]
- 红旗：无
- 诊断：`{"habit_hit": ["童梦森林"], "also_frequent_hit": ["麦田亲子"], "one_shot_trap_hit": [], "one_shot_is_top1": false, "preference_cues_hit": [], "operation_cues_hit": [], "order_cues_hit": [], "last_week_cues_hit": [], "similar_candidate_hit": [], "outside_window_leak": [], "profile_attached": false, "episode_hits": 0}`

  1. (semantic_memory, 0.6375176515988642) 用户于2026年8月22日带孩子去麦田亲子餐厅，再次强调需要有宝宝椅和儿童活动区。
  2. (semantic_memory, 0.6354541979557001) 用户于2025年11月1日带孩子去麦田亲子餐厅，再次强调需要有宝宝椅和儿童活动区。
  3. (semantic_memory, 0.6333988353380615) 用户于2026年8月29日带孩子去童梦森林亲子餐厅，距离3.2公里，强调需要有宝宝椅和儿童活动区。
  4. (semantic_memory, 0.6327932531479433) 用户于2025年11月29日带孩子去童梦森林亲子餐厅，再次强调需要有宝宝椅和儿童活动区。
  5. (semantic_memory, 0.6323421874937418) 用户于2026年7月11日带孩子去麦田亲子餐厅，再次强调需要有宝宝椅和儿童活动区。
  6. (semantic_memory, 0.6316249849619949) 用户于2025年10月18日带孩子去童梦森林亲子餐厅，距离3.2公里，强调需要有宝宝椅和儿童活动区。
  7. (semantic_memory, 0.6304690169567622) 用户于2026年6月27日带孩子去童梦森林亲子餐厅，再次强调需要有宝宝椅和儿童活动区。
  8. (semantic_memory, 0.6303588231622023) 用户于2026年4月18日带孩子去麦田亲子餐厅，再次强调需要有宝宝椅和儿童活动区。

### fuzzy_02 [FAIL] 根据我爱吃的菜，找个最近的地方

记忆只应给出饮食偏好；不能编造最近的具体餐厅。

- 关键词召回率：33%  命中：['粤菜']  缺少：['少油', '香菜']
- 红旗：无
- 诊断：`{"habit_hit": [], "also_frequent_hit": [], "one_shot_trap_hit": [], "one_shot_is_top1": false, "preference_cues_hit": ["粤菜"], "operation_cues_hit": [], "order_cues_hit": [], "last_week_cues_hit": [], "similar_candidate_hit": [], "outside_window_leak": [], "profile_attached": false, "episode_hits": 0}`

  1. (semantic_memory, 0.585535395228951) 麦田亲子餐厅距离用户当前位置5.8公里。
  2. (semantic_memory, 0.5797410477861012) 小象花园家庭餐厅距离用户当前位置7.4公里。
  3. (semantic_memory, 0.5468727024672116) 用户于2026年9月1日收藏了童趣岛家庭餐厅，计划下次预订。该餐厅距用户4.1公里，提供儿童活动区、宝宝椅和停车场，主营粤菜简餐，人均约110元。用户认为该餐厅与其上周去的餐厅相似。
  4. (semantic_memory, 0.5111828239410767) 用户于2026年4月18日确认麦田亲子餐厅孩子很喜欢且停车方便，决定下次亲子用餐优先考虑该餐厅。
  5. (semantic_memory, 0.508052050677928) 用户于2025年12月13日确认麦田亲子餐厅孩子很喜欢且停车方便，决定下次亲子用餐优先考虑该餐厅。
  6. (semantic_memory, 0.505436882306292) 用户于2026年7月11日确认麦田亲子餐厅孩子很喜欢且停车方便，决定下次亲子用餐优先考虑该餐厅。
  7. (semantic_memory, 0.5041132511084061) 用户于2026年5月30日确认麦田亲子餐厅孩子很喜欢且停车方便，决定下次亲子用餐优先考虑该餐厅。
  8. (semantic_memory, 0.5040357649247793) 用户于2026年3月7日确认麦田亲子餐厅孩子很喜欢且停车方便，决定下次亲子用餐优先考虑该餐厅。

### fuzzy_03 [PASS] 外面的空气质量不好，按老规矩给我调整一下

召回关窗+内循环+净化3档+23度这一组；记忆层不负责是否自动执行。

- 关键词召回率：100%  命中：['车窗', '内循环', '空气净化', '3档', '23']  缺少：[]
- 红旗：无
- 诊断：`{"habit_hit": [], "also_frequent_hit": [], "one_shot_trap_hit": [], "one_shot_is_top1": false, "preference_cues_hit": [], "operation_cues_hit": ["车窗", "内循环", "空气净化", "3档", "23"], "order_cues_hit": [], "last_week_cues_hit": [], "similar_candidate_hit": [], "outside_window_leak": [], "profile_attached": false, "episode_hits": 0}`

  1. (semantic_memory, 0.6520795089581326) 用户于2026年2月27日17:10因外部空气质量指数（AQI）为177，指示助手按照既定协议处理，即关闭车窗、切换至内循环并将空气净化调至3档。
  2. (semantic_memory, 0.6328032933843601) User's preferred protocol for handling poor air quality (AQI 200) is to close car windows, switch to internal air circulation, and set the air purifier to level 3.
  3. (semantic_memory, 0.6302278449868138) 用户于2026年8月31日17:40因外部空气质量指数（AQI）为170，指示助手按照既定协议处理，即关闭车窗、切换至内循环并将空气净化调至3档。
  4. (semantic_memory, 0.6191488493798477) 用户于2026年8月20日17:10因外部空气质量指数（AQI）为196，指示助手按照既定协议处理，即关闭车窗、切换至内循环并将空气净化调至3档。
  5. (semantic_memory, 0.6109350538185855) 用户于2025年12月2日17:40因外部空气质量指数（AQI）为158，指示助手按照既定协议处理，即关闭车窗、切换至内循环并将空气净化调至3档。
  6. (semantic_memory, 0.5964146946245552) 用户于2026年1月29日17:40因外部空气质量指数（AQI）为162，指示助手按照既定协议处理，即关闭车窗、切换至内循环并将空气净化调至3档。
  7. (semantic_memory, 0.5954936161052866) 用户于2025年12月31日17:25因外部空气质量指数（AQI）为165，指示助手按照既定协议处理，即关闭车窗、切换至内循环并将空气净化调至3档。
  8. (semantic_memory, 0.4353738287025692) 用户于2026年7月18日计划前往橡木厨房用餐，并确认沿用其标准点餐习惯：西冷牛排七分熟、蘑菇汤、柠檬水不加冰，且备注不要香菜。

### fuzzy_04 [PASS] 帮我找一下西餐厅，按照习惯进行点餐

召回固定套餐即可；未选店就声称点餐成功算硬失败（本脚本只测记忆召回）。

- 关键词召回率：100%  命中：['西冷牛排', '七分熟', '蘑菇汤', '柠檬水']  缺少：[]
- 红旗：无
- 诊断：`{"habit_hit": [], "also_frequent_hit": [], "one_shot_trap_hit": [], "one_shot_is_top1": false, "preference_cues_hit": [], "operation_cues_hit": [], "order_cues_hit": ["西冷", "七分熟", "蘑菇汤", "柠檬水"], "last_week_cues_hit": [], "similar_candidate_hit": [], "outside_window_leak": [], "profile_attached": false, "episode_hits": 0}`

  1. (semantic_memory, 0.6817663562528635) 用户于2025年12月13日计划前往蓝岸西餐厅用餐，并确认沿用其标准点餐习惯：西冷牛排七分熟、蘑菇汤、柠檬水不加冰，且备注不要香菜。
  2. (semantic_memory, 0.6664204323652607) 用户于2026年6月17日计划前往蓝岸西餐厅用餐，并确认沿用其标准点餐习惯：西冷牛排七分熟、蘑菇汤、柠檬水不加冰，且备注不要香菜。
  3. (semantic_memory, 0.658847215634804) 用户于2026年3月16日计划前往蓝岸西餐厅用餐，并确认沿用其标准点餐习惯：西冷牛排七分熟、蘑菇汤、柠檬水不加冰，且备注不要香菜。
  4. (semantic_memory, 0.6475998008380016) 用户于2026年5月17日计划前往湖畔牛排馆用餐，并确认沿用其标准点餐习惯：西冷牛排七分熟、蘑菇汤、柠檬水不加冰，且备注不要香菜。
  5. (semantic_memory, 0.6373780470842976) 用户于2026年2月13日计划前往湖畔牛排馆用餐，并确认沿用其标准点餐习惯：西冷牛排七分熟、蘑菇汤、柠檬水不加冰，且备注不要香菜。
  6. (semantic_memory, 0.6345118254205762) 用户于2025年11月12日计划前往湖畔牛排馆用餐，并确认沿用其标准点餐习惯：西冷牛排七分熟、蘑菇汤、柠檬水不加冰，且备注不要香菜。
  7. (semantic_memory, 0.6340312844007853) User's dining habits at 蓝岸西餐厅 include ordering sirloin steak medium (七分熟), mushroom soup, lemon water without ice, and requesting no cilantro.
  8. (semantic_memory, 0.6316349571299377) 用户于2025年10月12日计划前往橡木厨房用餐，并确认沿用其标准点餐习惯：西冷牛排七分熟、蘑菇汤、柠檬水不加冰，且备注不要香菜。

### fuzzy_05 [PASS] 根据上周的亲子餐厅，帮我预定一个差不多的餐厅

上周锚点是 2026-08-29 童梦森林；8-22 麦田不在该自然周；童趣岛是相似候选不是上周到访。

- 关键词召回率：100%  命中：['2026-08-29', '童梦森林', '儿童活动区', '宝宝椅']  缺少：[]
- 红旗：无
- 诊断：`{"habit_hit": [], "also_frequent_hit": [], "one_shot_trap_hit": [], "one_shot_is_top1": false, "preference_cues_hit": [], "operation_cues_hit": [], "order_cues_hit": [], "last_week_cues_hit": ["2026-08-29", "08-29", "童梦森林"], "similar_candidate_hit": ["童趣岛"], "outside_window_leak": ["麦田亲子", "小象花园"], "profile_attached": false, "episode_hits": 0}`

  1. (semantic_memory, 0.7011563564685107) 用户于2026年9月1日收藏了童趣岛家庭餐厅，计划下次预订。该餐厅距用户4.1公里，提供儿童活动区、宝宝椅和停车场，主营粤菜简餐，人均约110元。用户认为该餐厅与其上周去的餐厅相似。
  2. (semantic_memory, 0.6749770117762431) 用户于2026年4月18日确认麦田亲子餐厅孩子很喜欢且停车方便，决定下次亲子用餐优先考虑该餐厅。
  3. (semantic_memory, 0.6744920530481151) 用户于2026年7月11日确认麦田亲子餐厅孩子很喜欢且停车方便，决定下次亲子用餐优先考虑该餐厅。
  4. (semantic_memory, 0.6697544427336088) 用户于2026年8月29日确认童梦森林亲子餐厅孩子很喜欢且停车方便，决定下次亲子用餐优先考虑该餐厅。
  5. (semantic_memory, 0.6686030551572042) 用户于2026年3月7日确认麦田亲子餐厅孩子很喜欢且停车方便，决定下次亲子用餐优先考虑该餐厅。
  6. (semantic_memory, 0.6683246101571939) 用户于2026年8月8日确认童梦森林亲子餐厅孩子很喜欢且停车方便，决定下次亲子用餐优先考虑该餐厅。
  7. (semantic_memory, 0.6671240966920637) 用户于2026年3月21日确认小象花园家庭餐厅孩子很喜欢且停车方便，决定下次亲子用餐优先考虑该餐厅。
  8. (semantic_memory, 0.6668789066037746) 用户于2026年2月7日确认小象花园家庭餐厅孩子很喜欢且停车方便，决定下次亲子用餐优先考虑该餐厅。

