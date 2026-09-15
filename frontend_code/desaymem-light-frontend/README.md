# DesayMem Light 前端 — 记忆写入观测台

供开发人员直观观察 DesayMem_light 记忆管线写入变化的调试前端。与后端完全分离，零依赖（Python 标准库）。

**配套后端**：[psile/DesayMem_light](https://github.com/psile/DesayMem_light)（迁移 v12+，含 L0 短期记忆层）

## 调用架构

```
浏览器（本地电脑或服务器）
  http://10.133.72.161:20147          ← 服务器上访问也可用 127.0.0.1:20147
│
├── 表/记忆数据（同源，无跨域）
│     GET  /api/tables, /api/tables/{table}
│     └── 只读 JSON 镜像 /data/pengshuang/memory-benchmark/data/desaymem_light/json_mirror/postgres/*.json
│         （由 DesayMem_light MirrorWorker 持续投影，实时反映 PG）
│
└── 后端 API（反向代理，规避浏览器 CORS —— DesayMem_light API 未配 CORS）
      POST /api/backend/v1/messages        → http://127.0.0.1:20148（202 异步）
      POST /api/backend/v1/memories/search → http://127.0.0.1:20148
```

端口 20147 在宿主机 Docker 端口映射范围内（20140–20149），本地电脑可直接访问。

## 界面布局（三栏）

| 栏 | 内容 |
|---|---|
| 登录页 | 车机点火风格：租户/车辆ID/驾驶员（司机A/乘客B/司机C/自定义）/乘员位置 —— **多用户隔离**（scope 四元组+session 决定记忆归属） |
| 左侧 | 用户卡片 + **数据库表面板**：按管线阶段分组的全部 PG 表（JSON 镜像），行数徽章实时刷新增长时绿色闪烁；点击表名展开行卡片（新行高亮，embedding 等大字段折叠） |
| 中间 | **车机对话**：检索记忆 → LLM 决策回复（决策面板+调试面板，开发者模式）→ 异步写入 user+assistant 两条消息（202）；「搜索：xxx」直接检索 |
| 右侧 | **管线观测**：总览（表行数、任务统计）、任务队列（memory_jobs 实时）、facts/events/cross_event 记忆卡片 |

表分组：接入层 session_messages/**short_term_memories** → 切片 topic_segments/topic_segment_messages → 记忆条目 memory_items → 证据关联 memory_evidence/memory_relations/memory_tag_links/tag_definitions → 任务队列 memory_jobs → 审计用量 memory_audit_events/llm_usage

## 车机对话链路

```
用户输入
  → Step1 检索记忆   POST /api/backend/v1/memories/search（scope 隔离，失败不阻断）
       四路召回：short_term（近期对话，202 后立即命中）+ facts + events + cross_events
  → Step2 LLM 决策   POST /api/llm/chat（server.py 组装车机决策 prompt，Key 不进浏览器）
       └ vLLM Qwen3-32B 输出 {decision, reply} JSON（intent/实体/偏好/缺失信息/tool_mode）
  → Step3 展示回复   决策面板（模拟模式黄标）+ 记忆调试面板（召回条目/耗时/token）
  → Step4 异步写入   user + assistant 两条消息 POST /api/backend/v1/messages（202）
       └ 左侧面板实时观察 session_messages → short_term_memories → topic_segments → memory_items 变化
```

### request_id 的 UUID 陷阱（踩坑记录）

页面若通过 `http://<服务器IP>:20147`（非 localhost 的 HTTP）访问，`crypto.randomUUID` 为 undefined——直接调用会导致检索请求静默失败（表现为召回 0 条）。index.html 内置 `uuid()` helper：优先 `crypto.randomUUID`，降级生成标准 v4 UUID（8-4-4-4-12 五段，后端按 UUID 严格校验，4 段会被 422 拒绝）。检索失败现在会 toast 提示，不再静默。

## 快速启动

```bash
# 前置：DesayMem_light 三进程（API 20148 + worker + mirror）+ PG 实例(20149) 已运行
cd /data/pengshuang/frontend_code/desaymem-light-frontend
python3 server.py                           # 默认端口 20147

# 自定义
MIRROR_ROOT=/path/to/json_mirror/postgres FRONTEND_PORT=20147 BACKEND_URL=http://127.0.0.1:20148 python3 server.py
```

环境变量（`server.py` 顶部均带默认值）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `FRONTEND_PORT` | 20147 | 监听端口（0.0.0.0） |
| `MIRROR_ROOT` | `/data/pengshuang/memory-benchmark/data/desaymem_light/json_mirror/postgres` | JSON 镜像目录（只读） |
| `BACKEND_URL` | `http://127.0.0.1:20148` | DesayMem Light API（反向代理目标） |
| `LLM_BASE_URL` | `http://127.0.0.1:20140/v1` | vLLM 端点 |
| `LLM_API_KEY` | `EMPTY` | vLLM Key（仅存于服务端，不进浏览器） |
| `LLM_MODEL` | `memory-llm` | 模型名 |
| `LLM_MAX_TOKENS` / `LLM_TEMPERATURE` / `LLM_TIMEOUT` | 256 / 0.2 / 30 | 生成参数 |

- 本地电脑浏览器打开 **http://10.133.72.161:20147**
- 服务器上打开 http://127.0.0.1:20147

## server.py API

| 路径 | 说明 |
|---|---|
| `GET /api/tables` | 所有表 `{table: {rows, table_version, mtime}}` |
| `GET /api/tables/{table}` | 单表完整镜像 `{"table_version": n, "rows": {pk: {data, version}}}` |
| `GET/POST /api/backend/{path}` | 反向代理到 DesayMem Light API（`BACKEND_URL`，默认 `http://127.0.0.1:20148`） |
| `POST /api/llm/chat` | 车机 LLM 对话代理：组装决策 prompt（参考 cockpit-frontend），转发 vLLM（`LLM_BASE_URL`/`LLM_API_KEY`），API Key 不进浏览器 |
| `GET /api/health` | 服务、镜像目录、后端地址信息 |

只读、无依赖（标准库 http.server），镜像文件由 MirrorWorker 原子写（tmpfile+os.replace），读取天然安全。

## 观察要点

1. 发送消息后 ~1s：**session_messages** 与 **short_term_memories** 各 +1（L0 同事务写入，此刻即可检索命中）
2. 缓冲达到 token 阈值后：**topic_segments**/**topic_segment_messages** 新增（切段 job completed）
3. 随后 **memory_items** 新增 fact 行；**memory_jobs** 依次出现 session_segment → fact_extract → event_build（completed）
4. 检索验证：`搜索：xxx` 右侧召回（scope 必须与写入一致，tenant/user/vehicle/occupant 全匹配才会命中——隔离设计）；近期对话组展示 short_term 原始消息（角色+score）
5. L3 画像：攒 10 个 event 触发 cross_event → profile_update（memory_audit_events 可见演化记录）

## 已验证（2026-09-10）

- 登录页（司机A/乘客B/司机C/自定义 + 乘员位置联动）→ scope 驱动写入与检索
- 车机对话全链路：检索（short_term+facts+events）→ LLM 决策 "好的，正在将空调温度调整为22度。"（intent=climate）→ user+assistant 两条 202 写入
- LLM 代理稳定性 5/5（空调/导航/音乐/座椅多意图），JSON 宽松提取兜底偶发截断
- `/api/tables` 12 表（含 short_term_memories）；检索 scope 四元组须与写入一致（跨 scope 返回空是隔离设计）
- L0 短期记忆：消息发送后立即检索即命中（无需等 worker 切段）；fact prompt v2 蒸馏出"用户喜欢听周杰伦的歌"（此前指令式偏好被 v1 丢弃）
- 非 localhost HTTP 访问下 uuid 降级路径验证通过（标准五段 v4 UUID，后端 200）
