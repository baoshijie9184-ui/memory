# DesayMem 车机记忆系统 — 前后端交互文档

本文档说明前端 `cockpit-frontend` 与后端 `DesayMem_mem0` 的完整交互流程、各 ID 的作用、使用方法和注意事项。

## 目录

- [系统架构](#系统架构)
- [各 ID 含义与作用](#各-id-含义与作用)
- [前端登录流程](#前端登录流程)
- [对话决策流程](#对话决策流程)
- [记忆存储流程](#记忆存储流程)
- [记忆观测面板](#记忆观测面板)
- [LLM 代理职责](#llm-代理职责)
- [超时机制](#超时机制)
- [注意事项与常见问题](#注意事项与常见问题)

---

## 系统架构

```
浏览器 (http://127.0.0.1:8080)
  │
  ├── 记忆读写 ──→ DesayMem 后端 (http://10.133.72.161:20144)
  │                   └── PostgreSQL (20143) + Embedding (20141)
  │
  ├── 记忆观测 ──→ DesayMem 后端 (同上)
  │                   ├── /v1/users/{id}/memory-layers
  │                   └── /v1/users/{id}/memory-events
  │
  └── 对话生成 ──→ 本地 LLM 代理 (http://127.0.0.1:8767)
                      └── vLLM (http://10.133.72.161:20140/v1)
```

| 端口 | 服务 | 说明 |
|------|------|------|
| 8080 | 前端静态页面 | Python http.server 托管 index.html |
| 8767 | 本地 LLM 代理 | llm_proxy.py，隐藏 API Key，拼接结构化决策 prompt |
| 20140 | vLLM 模型服务 | Qwen 大模型，OpenAI 兼容接口 |
| 20141 | BGE-M3 Embedding | 仅供后端内部使用 |
| 20143 | PostgreSQL | 仅供后端内部使用 |
| 20144 | DesayMem 记忆后端 | 记忆读写 + 三层观测 API |

> 前端不直接访问 vLLM (20140)，API Key 只存在本地 `.env` 中，通过 8767 代理转发。

---

## 各 ID 含义与作用

以下 ID 贯穿前端登录、记忆存储、记忆查询全流程，必须保持一致。

| ID | 含义 | 前端来源 | 作用 |
|----|------|----------|------|
| `tenant_id` | 租户/项目标识 | 登录页"租户"输入框，默认 `oem_chery` | 多租户数据隔离，不同车厂数据互不可见 |
| `user_id` | 用户唯一标识 | 登录页"驾驶员"下拉框，如 `user_driver` | 同一租户下区分不同用户的记忆 |
| `vehicle_id` | 车辆标识 | 登录页"车辆 ID"输入框，默认 `vehicle_001` | 标识记忆发生于哪辆车，不用于数据隔离 |
| `occupant_id` | 乘员位置 | 登录页"乘员位置"下拉框：`driver`/`passenger`/`rear_left`/`rear_right` | 区分主驾/副驾/后排的记忆归属，查询时必须与存储时一致 |
| `session_id` | 会话标识 | 前端自动生成 `session_时间戳` | 标记同一会话内的记忆批次，熄火切换用户后重新生成 |

### 关键约束

- **tenant_id + user_id 是数据隔离的主键对**：后端所有查询都用这两个字段过滤。
- **occupant_id 必须匹配**：存记忆时用 `driver`，查记忆时也必须用 `driver`。如果存储和查询的 occupant_id 不一致，L1/L2/L3 面板会显示空白。
- **session_id 每次登录重新生成**：不影响记忆查询，只用于标记记忆来源会话。

---

## 前端登录流程

```
用户填写登录表单
  ├── 后端地址 (默认 http://10.133.72.161:20144)
  ├── LLM 代理地址 (默认 http://127.0.0.1:8767)
  ├── 租户 (默认 oem_chery)
  ├── 车辆 ID (默认 vehicle_001)
  ├── 驾驶员 (下拉选择或自定义)
  └── 乘员位置 (driver / passenger / rear_left / rear_right)
      │
      ▼
点击"点火启动"
      │
      ▼
GET {apiUrl}/health          ← 测试后端连通性
      │
      ├─ 失败 → 显示"连接失败"，不进入主界面
      │
      └─ 成功 → 进入主界面，执行以下加载：
            │
            ├── GET {apiUrl}/v1/users/{userId}/memories?tenant_id={tenant}&limit=100
            │     └─ 加载左侧"记忆总数"
            │
            ├── GET {apiUrl}/v1/users/{userId}/memory-layers?tenant_id={tenant}&occupant_id={occupant}&include_inactive=true
            │     └─ 加载右侧 L1/L2/L3 观测面板
            │
            └── GET {apiUrl}/v1/users/{userId}/memory-events?tenant_id={tenant}&limit=100
                  └─ 加载右侧"演化记录"
```

---

## 对话决策流程

用户在中间对话框发送一条消息后，前端执行三步：

### Step 1: 检索记忆 (10 秒超时)

```
POST {apiUrl}/v1/memories/search
Body: {
  tenant_id:   "oem_chery",
  user_id:     "user_driver",
  query:       "用户输入文本",
  top_k:       5
}
```

- 返回相关记忆列表 (content + score + memory_type)
- 超时 10 秒自动放弃，不阻断对话
- 检索失败不影响后续步骤

### Step 2: LLM 决策生成 (15 秒超时)

```
POST {llmProxyUrl}/chat
Body: {
  messages: [{ role: "user", content: "用户输入文本" }],
  memory_context: ["召回记忆1", "召回记忆2", ...],
  tool_mode: "simulation",
  vehicle_context: {
    current_location: null,
    vehicle_id: "vehicle_001",
    occupant_id: "driver"
  },
  tool_results: []
}
```

本地 LLM 代理收到请求后：

1. 拼接系统提示 `COCKPIT_DECISION_PROMPT`（要求模型返回结构化 JSON）
2. 把记忆上下文、工具模式、车机上下文拼进 user 消息
3. 转发到 vLLM `http://10.133.72.161:20140/v1/chat/completions`
4. 解析模型返回的 JSON，校验 decision + reply 字段
5. 返回给前端：`{ reply, decision, usage, latency_ms }`

vLLM 参数 (由 `.env` 控制)：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `LLM_MODEL` | `memory-llm` | 服务器模型名称 |
| `LLM_MAX_TOKENS` | `256` | 含结构化决策 + 用户回答 |
| `LLM_TEMPERATURE` | `0.2` | 低温度，输出稳定 |
| `LLM_ENABLE_THINKING` | `false` | 关闭 Qwen3 思考链 |
| `LLM_TOP_P` | `0.8` | 采样参数 |

- 超时 15 秒，显示"决策超时（15秒），模型服务可能繁忙"
- LLM 失败时显示兜底回复"车机助手暂时无法回答，请稍后重试"

### Step 3: 异步存储记忆 (不阻塞 UI)

仅在 Step 2 成功时执行：

```
POST {apiUrl}/v1/memories
Body: {
  tenant_id:   "oem_chery",
  user_id:     "user_driver",
  vehicle_id:  "vehicle_001",
  occupant_id: "driver",
  session_id:  "session_1693...",
  scene:       "driving",
  messages: [
    { role: "user", content: "用户输入" },
    { role: "assistant", content: "车机回答" }
  ],
  infer: true
}
```

- `infer: true` 触发后端自动提取 L1 事实 + L2 情景 + L3 画像
- 存储完成后自动刷新记忆总数 + 观测面板
- 存储失败只在 console 记录，不弹错误提示

---

## 记忆观测面板

右侧面板展示数据库中真实存储的记忆及其演化历史。仅在开发者模式开启时显示完整内容。

### 接口调用

| 时机 | 接口 | 作用 |
|------|------|------|
| 登录时 | `GET /v1/users/{id}/memory-layers` | 加载 L1/L2/L3 全量数据 |
| 登录时 | `GET /v1/users/{id}/memory-events` | 加载审计事件 |
| 对话存储后 | 同上两个接口 | 自动刷新 |
| 删除记忆后 | 同上两个接口 | 自动刷新 |

### memory-layers 请求参数

```
GET {apiUrl}/v1/users/{userId}/memory-layers
  ?tenant_id=oem_chery
  &occupant_id=driver              ← 必须与存储时一致
  &include_inactive=true           ← 包含已结束/已替代的数据
  &l1_limit=500
  &l2_limit=500
  &l3_limit=500
```

### memory-layers 返回结构

```json
{
  "tenant_id": "oem_chery",
  "user_id": "user_driver",
  "stats": {
    "l1_count": 3,
    "l2_count": 1,
    "l2_active_count": 1,
    "l2_complete_count": 0,
    "l3_count": 2,
    "l3_active_count": 2,
    "l3_superseded_count": 0
  },
  "l1": [ { "id": "...", "content": "...", "source": "conversation", ... } ],
  "l2": [ { "id": "...", "content": "...", "metadata": { "episode_status": "active" }, ... } ],
  "l3": [ { "attribute": "...", "value": "...", "status": "active", "stability": "...", ... } ],
  "generated_at": "2026-09-06T..."
}
```

### 三层记忆含义

| 层级 | 表 | 含义 | 生成时机 |
|------|-----|------|----------|
| L1 | memory_items (semantic_memory) | 原子事实，从对话中抽取 | POST /v1/memories (infer=true) |
| L2 | memory_items (episodic_memory) | 情景事件，由 L1 归纳 | 后端自动从 L1 生成 |
| L3 | profile_beliefs | 用户画像信念，由 L1/L2 蒸馏 | 后端自动从 L1/L2 生成 |

### 审计事件类型

| 事件 | 层级 | 含义 |
|------|------|------|
| ADD | L1/L2/L3 | 新增记忆 |
| UPDATE | L2 | 情景更新 |
| CONFIRM | L3 | 信念被再次确认 |
| COMPLETE | L2 | 情景事件结束 |
| SUPERSEDE | L3 | 旧信念被新信念替代 |
| COEXIST | L3 | 新信念与旧信念共存 |
| DELETE | L1/L2 | 记忆被删除 |
| FORGET | 保留 | 自动遗忘（当前未启用） |

---

## LLM 代理职责

`llm_proxy.py` 是本地运行的 FastAPI 服务，位于前端和 vLLM 之间：

1. **隐藏 API Key**：Key 只存在本地 `.env`，不暴露给浏览器
2. **拼接结构化 prompt**：注入 `COCKPIT_DECISION_PROMPT` 系统提示，要求模型返回 JSON
3. **清理模型输出**：去除 Qwen3 thinking 标签、Markdown 代码块包裹
4. **校验 JSON 结构**：用 Pydantic 验证 decision + reply 字段完整性
5. **强制 tool_mode**：覆盖模型返回的 tool_mode，防止模型伪造执行模式

### 代理不做什么

- 不存储任何数据
- 不做记忆检索（那是前端直接调后端）
- 不做记忆提取/画像（那是后端 POST /v1/memories 内部完成）

---

## 超时机制

| 环节 | 超时 | 行为 |
|------|------|------|
| 前端 → 后端记忆检索 | 10 秒 | 静默放弃，对话继续（无记忆上下文） |
| 前端 → LLM 代理 | 15 秒 | 显示"决策超时（15秒），模型服务可能繁忙" |
| LLM 代理 → vLLM | 30 秒 | 返回错误，前端显示兜底回复 |
| 前端 → 观测面板加载 | 无超时 | 失败显示错误 + 重试按钮 |

超时后的兜底行为：

- 记忆检索超时：对话照常进行，但不带历史记忆上下文
- LLM 决策超时：显示"车机助手暂时无法回答"，不写入记忆
- 观测面板加载失败：不影响对话，面板显示错误信息 + 重试按钮

---

## 注意事项与常见问题

### occupant_id 必须一致

**最常见的坑**：存储记忆时 occupant_id 是 `driver`，但查询 memory-layers 时如果没传 occupant_id，后端不过滤 occupant_id（查全部）。如果后端版本较旧（硬编码 `primary`），则查不到数据。

当前前端已在 memory-layers URL 中传 `occupant_id={state.occupant}`，确保与存储一致。

### 后端必须执行数据库迁移

观测面板依赖 `memory_audit_events` 表（migrations/006）。如果后端未执行迁移：

```bash
desaymem-migrate apply
desaymem-migrate check
```

则 `/memory-events` 接口会报错，演化记录标签页显示加载失败。

### 后端版本必须包含 memory-layers 接口

如果 `/memory-layers` 返回 404，说明后端版本太旧，需要 `git pull` 更新并重启。

### LLM_MAX_TOKENS 的两个层

| 场景 | 服务 | token 限制 | 用途 |
|------|------|-----------|------|
| 前端车机回答 | llm_proxy → vLLM (20140) | 256 | 结构化决策 + 简短回答 |
| 后端记忆提取 | DesayMem 后端 → vLLM (20140) | 2000 | L1 抽取 + L2 归纳 + L3 蒸馏 |

前端的 256 不影响后端的记忆提取能力。后端有自己的独立 LLM 配置。

### 模型思考链泄露

如果界面出现"首先检查历史记忆…"等分析过程文字：

1. 确认 `.env` 中 `LLM_ENABLE_THINKING=false`
2. 重启本地 8767 代理
3. 确认 vLLM 支持 `chat_template_kwargs.enable_thinking` 参数

### CORS 问题

- 前端通过 `http://127.0.0.1:8080` 启动，避免 `file://` 的 CORS 问题
- 后端 (20144) 需允许 `http://127.0.0.1:8080` 和 `http://localhost:8080` 的跨域请求
- 本地 LLM 代理 (8767) 已配置 `allow_origins=["*"]`，无 CORS 问题

### API Key 不一致

LLM 代理返回 401/403 时：

1. 检查 `.env` 中 `LLM_API_KEY` 是否与服务器 vLLM 配置一致
2. 访问 `http://127.0.0.1:8767/health` 查看代理状态和 Key 掩码
3. 直接测试 vLLM：`curl -H "Authorization: Bearer YOUR_KEY" http://10.133.72.161:20140/v1/models`

### 切换用户

点击"熄火 / 切换用户"会：

1. 清空前端状态（memories、obsLayers、obsEvents）
2. 重置观测面板为"登录后加载记忆观测数据"
3. 显示登录界面

重新登录时 `session_id` 会重新生成，但 `tenant_id`/`user_id`/`occupant_id` 由表单决定。切换不同用户可以看到不同用户的记忆。
