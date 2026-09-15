# DesayMem 多租户年度车机交互评测集

模拟真实一年的多租户、多车、多用户、多座位的车机对话，测记忆系统在隔离、召回、抗噪三个维度的表现。

与 `DesayMem_yearlong_benchmark`（单用户单年）互补：那套测"记忆召回质量"，这套测"隔离正确性 + 召回质量"。

## 1. 数据组织

```
data/
├── manifest.json                    # 全局矩阵：会话数、标签分布、种子
├── eval_cases.json                  # 26 条评测用例（A/B/C/D 四组）
└── tenants/                         # 层级：tenant(车型) → vehicle(车辆) → user(用户)
    ├── oem_suv_family/              # 租户 1：家庭 SUV 车型
    │   ├── veh_suv_001/             # 主用 SUV
    │   │   ├── usr_father/sessions.jsonl + persona.json    # 474 条
    │   │   ├── usr_mother/sessions.jsonl + persona.json     # 92 条
    │   │   ├── usr_child/sessions.jsonl  + persona.json    # 69 条
    │   │   ├── usr_grandma/sessions.jsonl + persona.json   # 81 条
    │   │   └── _shared_multi_occupant.jsonl                 # 27 条（9 次旅程 × 3 乘员）
    │   └── veh_suv_002/             # 露营车（father 周末开）
    │       └── usr_father/sessions.jsonl                   # 25 条
    └── oem_sedan_biz/               # 租户 2：公司轿车（father 的公司配车）
        └── veh_sed_001/
            └── usr_father/sessions.jsonl                   # 46 条
```

共 814 条会话，时间跨度 2025-09-08 → 2026-09-07，seed=20260907 确定性生成。

## 2. 用户矩阵与画像差异

| 用户 | 座位 | 通勤 | 独有偏好（与家人互斥） |
|---|---|---|---|
| usr_father | driver | 德赛科技园，最快路线，空调23度+通勤轻音乐 | 童梦森林亲子餐厅、清蒸鲈鱼少油不要香菜、西冷七分熟套餐、空气差老规矩 |
| usr_mother | driver（father 出差时） | 惠州西湖，市内道路，空调24度+爵士歌单 | **麦田亲子餐厅**（与 father 的童梦森林互斥）、藜麦沙拉、**拿铁少糖燕麦奶** |
| usr_child | passenger_rear | — | 《汪汪队立大功》、草莓酸奶 |
| usr_grandma | passenger_front | — | 粤剧频道音量12、每周三市中心医院透析 |
| father@veh_suv_002 | driver | — | 露营模式：红花湖+后排放倒+空调24度 |
| father@veh_sed_001 | driver | 德赛科技园（话术与 SUV 完全相同） | 无独有——测跨租户去重行为 |

**互斥设计是隔离测试的判定依据**：同一查询"我们经常去的亲子餐厅"，father 的正确答案是童梦森林，mother 的正确答案是麦田。任何一个用户的记忆泄漏到另一个用户的结果里，就是隔离失败。

## 3. 会话数据格式

每行一个 cockpit_session。与 yearlong benchmark 相同的字段，新增 `occupant_id` 和 `cross_occupant`：

```json
{
  "record_type": "cockpit_session",
  "tenant_id": "oem_suv_family",
  "user_id": "usr_mother",
  "vehicle_id": "veh_suv_001",
  "session_id": "drive_20260115_0810_0233",
  "cross_occupant": false,
  "scene": "commute",
  "started_at": "2026-01-15T08:10:00+08:00",
  "ended_at": "2026-01-15T08:41:00+08:00",
  "lifecycle": [{"event": "ignition_on", ...}, {"event": "ignition_off", ...}],
  "occupants": ["driver"],
  "weather": {},
  "messages": [
    {"role": "user", "content": "[2026-01-15 08:10] 去西湖那边上班，空调和音乐照我的习惯"},
    {"role": "assistant", "content": "已导航至惠州西湖，避开高速走市内道路，空调24度并播放爵士歌单。"}
  ],
  "operations": [
    {"action": "navigate", "parameters": {"destination": "惠州西湖", "route_preference": "city_roads"}, "status": "success"},
    {"action": "set_climate", "parameters": {"temperature_c": 24, "fan_level": 2, "mode": "auto"}, "status": "success"},
    {"action": "play_media", "parameters": {"playlist": "爵士歌单", "volume": 16}, "status": "success"}
  ],
  "tags": ["通勤习惯", "mother"],
  "api_payload": {
    "tenant_id": "oem_suv_family",
    "user_id": "usr_mother",
    "vehicle_id": "veh_suv_001",
    "session_id": "drive_20260115_0810_0233",
    "occupant_id": "driver",
    "scene": "commute",
    "source": "synthetic_cockpit",
    "messages": [...同上 messages...],
    "metadata": {"occurred_at": "...", "operations": [...], "tags": [...], "synthetic": true},
    "infer": true
  }
}
```

导入时直接 POST `api_payload` 到 `POST /v1/memories`。

### 多乘员共享旅程（`_shared_multi_occupant.jsonl`）

一次家庭周六出行拆成 3 条记录，同 session_id、不同 user_id/occupant_id：

```
[usr_father, driver]          "今天全家去麦田吧，孩子想去"           → 导航麦田
[usr_child,  passenger_rear]  "我要看汪汪队"                        → 播放汪汪队
[usr_grandma, passenger_front] "放粤剧吧，音量调到12"                → 粤剧频道
```

共 9 次旅程 × 3 条 = 27 条。每条独立 POST，验证写入路径按 user/occupant 分流。

## 4. 真实性设计（与 v1 模板化数据的区别）

| 机制 | 实现 |
|---|---|
| 对话变体 | 每场景 3-4 种说法，rng 确定性轮换 |
| 出差周 | 一年 4 周 father 缺席，mother 接管通勤（mother 会话集中在这些周） |
| 休假周 | 一年 2 周全家无任何会话 |
| 偶发偏离 | 17 条单次偏离（25度空调/换路线/换新闻/不带孩子的随便吃），不应形成稳定记忆 |
| 闲聊 | 25 条天气/时间/新闻闲聊，无记忆价值 |
| 模糊指代 | "老规矩""跟平时一样""上次那家"等指代性话术贯穿各场景 |
| 时间演化 | 亲子餐厅 father/mother 轮换主导（42 天周期），grandma 每周三固定行程 |

## 5. 场景分布（16 种）

```
commute 459 | child_request 69 | hospital_trip 50 | dining_preference 31
opera_time 31 | family_shared_trip 27 | family_dining 26 | coffee_run 26
chit_chat 25 | camping 25 | deviation 17 | western_dining 12
charging 6 | poor_air_quality 6 | maintenance 3 | recommendation_feedback 1
```

## 6. 评测用例（26 条，四组）

### A 组：隔离断言（9 条）

| ID | 断言内容 |
|---|---|
| iso_01 | father@sedan 租户搜"通勤习惯"，不出现 SUV 租户内容（童梦森林/麦田/露营） |
| iso_02 | father@SUV 搜"通勤"，返回德赛科技园，不出现公司配车/露营车内容 |
| iso_03 | father 搜"咖啡拿铁"，不出现 mother 的拿铁偏好（跨用户隔离） |
| iso_04 | mother 搜"西餐点单"，不出现 father 的西冷七分熟（mother 无西餐数据） |
| iso_05 | **双断言**：同查询"我们经常去的亲子餐厅"，father→童梦森林，mother→麦田 |
| iso_06 | father(driver) 的 profile 里不出现粤剧（grandma 的副驾偏好） |
| iso_07 | child 搜"我想看的动画片"，返回汪汪队，不返回通勤轻音乐/爵士 |
| iso_08 | grandma 搜"周三的行程"，返回市中心医院 |
| iso_09 | father 搜"医院行程"，不出现透析/市中心医院（grandma 的行程不泄漏） |

### B 组：filter 语义（4 条，固化后端行为）

| ID | 测什么 | 预期 |
|---|---|---|
| flt_01 | 不带 vehicle filter 搜"空调习惯" | 返回跨车记忆（后端默认不隔离车辆） |
| flt_02 | 带 vehicle_id=veh_suv_001 filter | 只返回 SUV1 记忆，不泄漏 SUV2/SED1 |
| flt_03 | 带 occupant_id=driver filter 搜"媒体偏好" | 只返回 driver 的通勤轻音乐，不返回汪汪队/粤剧 |
| flt_04 | father 在 SUV1 和 sedan 说完全相同的通勤话术 | 每租户各留一份（去重键含 tenant_id，跨租户独立） |

### C 组：模糊召回（10 条，按画像分组）

father 5 条（亲子餐厅/饮食偏好/空气差/西餐/露营）+ mother 3 条（拿铁/通勤/健康轻食）+ grandma 1 条（粤剧）+ child 1 条（零食）。

### D 组：陷阱（3 条）

| ID | 陷阱 | 断言 |
|---|---|---|
| trap_01 | 偶发 25 度不应覆盖习惯 23 度 | "空调习惯温度"Top1 是 23 不是 25 |
| trap_02 | 偶发换新闻不应覆盖通勤轻音乐 | "通勤听什么"Top1 是轻音乐不是新闻 |
| trap_03 | 闲聊不形成记忆 | "最近的天气怎么样"不应召回 26度/晴 |

## 7. 运行

```bash
# 1. 验证数据集（本地，无服务器）
python scripts/run_multitenant_http_test.py --mode dry-run

# 2. 重新生成数据集
python scripts/generate_multitenant_dataset.py --seed 20260907

# 3. 单元测试（判定逻辑，无服务器）
python -m pytest tests/test_multitenant_eval.py -v

# 4. 完整评测（需后端可达）
python scripts/run_multitenant_http_test.py --mode all --label full_v1

# 5. 只跑隔离组（A+B，最快出结论）
python scripts/run_multitenant_http_test.py --mode isolation --label iso_only

# 6. 只跑召回组（C+D）
python scripts/run_multitenant_http_test.py --mode recall --label recall_only

# 7. 只导入
python scripts/run_multitenant_http_test.py --mode import
```

### 评测流程（--mode all）

```
1. 按时间排序加载全部 814 条会话
2. 对每个 (tenant, user) 先 DELETE /v1/users/{user}/memories?tenant_id=...（共 7 个组合）
3. 逐条 POST /v1/memories（导入 814 条，每条触发 LLM 提取）
4. 按 eval_cases.json 逐条执行：
   - search：POST /v1/memories/search → must/must_not/not_top1 判定
   - search_dual：同一查询打两个 scope，分别判定
   - profile：GET /v1/users/{user}/profile?occupant_id=driver → must_not 判定
   - dedup_check：GET /v1/users/{user}/memories 拉两租户 → 计数断言
5. 输出 reports/{label}_{ts}.json + .md（按组统计 + 逐用例明细）
```

### 判定规则

| 断言类型 | 含义 | 失败示例 |
|---|---|---|
| `must` | Top-K 中必须命中（content 或 metadata） | father 搜亲子餐厅没有童梦森林 |
| `must_not` | Top-K 全部结果中不得出现 | mother 的拿铁出现在 father 的结果 |
| `not_top1` | Top1 不得是该项（出现在后面允许） | "空调习惯温度"Top1 是偶发 25 度 |
| `not_top1_a/b` | 双断言版的 Top1 互斥 | mother 搜亲子餐厅 Top1 是童梦森林（泄漏） |
| `vehicle_leak` | filter 模式下结果不得来自其他车辆 | flt_02 结果里有 SUV2 的记忆 |

## 8. 后端隔离机制速查（为什么这样设计用例）

| 后端事实 | 代码依据 | 对应用例 |
|---|---|---|
| 搜索硬过滤只有 tenant_id + user_id | pgvector.py search() WHERE | iso_01~09 |
| vehicle_id/occupant_id 是可选 filter（硬 WHERE） | `_filter_sql()` 支持 6 个字段 | flt_01~03 |
| 去重键 (tenant_id, user_id, content_hash)，不含 vehicle | migrations/001 唯一约束 | flt_04 |
| L2 episode 按 (tenant, user, occupant) 隔离 | migrations/005 唯一索引 | shared 旅程 + iso_06 |
| L3 beliefs 有 occupant_id 列 | distiller.py + profile.py | iso_06 |
| DELETE 只能按 user 全删 | memory.py delete_by_user() | 导入前 reset |

## 9. 依赖

- Python >= 3.12
- httpx、pytest
- 后端运行在 `--base-url`（默认 http://10.133.72.161:20142）
