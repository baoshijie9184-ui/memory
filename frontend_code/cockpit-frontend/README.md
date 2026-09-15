# DesayMem 车机记忆系统 - 前端

车机记忆系统前端，与 DesayMem 后端完全分离，互不影响。

## 整体调用架构

```
浏览器前端 (http://127.0.0.1:8080)
├── 记忆读写 → http://10.133.72.161:20142
└── 对话生成 → http://127.0.0.1:8767
    └── Qwen vLLM → http://10.133.72.161:20140/v1
```

- **浏览器**直接访问服务器记忆后端 (20142) 进行记忆读写
- **浏览器**对话消息发送到本地 LLM 代理 (8767)
- **本地代理**携带 API Key 转发到服务器 vLLM (20140)，API Key 不暴露给浏览器

## 公司服务器端口说明

| 端口 | 服务 | 说明 |
|------|------|------|
| 20140 | Qwen vLLM 模型服务 | OpenAI 兼容接口 `/v1/chat/completions`，由本地 llm_proxy.py 调用 |
| 20141 | BGE-M3 Embedding 服务 | 由记忆后端内部使用，前端不直接访问 |
| 20142 | DesayMem 记忆后端 | 记忆读写 API，由浏览器直接访问 |
| 20143 | PostgreSQL | 仅供服务器内部使用，外部不可访问 |
| 8767  | Windows 本地 LLM 代理 | llm_proxy.py，隐藏 API Key |
| 8080  | Windows 本地前端静态页面 | Python http.server 托管 index.html |

> **20140 vs 20142**：20140 是 Qwen 大模型服务（生成对话回复），20142 是记忆后端（存储/检索记忆）。前端不直接访问 20140，而是通过本地 8767 代理间接调用。

## 快速启动

1. 编辑 `.env`，填入与服务器 vLLM 一致的 API Key
2. 双击 `start.bat`
3. 浏览器自动打开 http://127.0.0.1:8080，点火启动即可

## 文件说明

```
cockpit-frontend/
  index.html       前端页面（三栏布局：用户卡片 / 对话区 / 记忆库）
  llm_proxy.py     本地 LLM 代理（隐藏 API Key，Mem0 OSS proxy 模式）
  .env             API Key 与模型配置（不提交 Git）
  .env.example     配置模板
  start.bat        一键启动脚本
  requirements.txt Python 依赖清单
```

## 本地安装依赖

```bash
pip install -r requirements.txt
```

依赖：fastapi、uvicorn、httpx、pydantic

## `.env` 配置方法

从模板复制并编辑：

```bash
copy .env.example .env
```

配置内容：

```
LLM_API_KEY=请填写与服务器vLLM一致的API_Key
LLM_MODEL=memory-llm
LLM_BASE_URL=http://10.133.72.161:20140/v1
PROXY_PORT=8767
LLM_ENABLE_THINKING=false
LLM_MAX_TOKENS=256
LLM_TEMPERATURE=0.2
LLM_TOP_P=0.8
COCKPIT_TOOL_MODE=simulation
```

- `LLM_API_KEY`：必须与服务器 vLLM 配置的 API Key 一致
- `LLM_MODEL`：服务器上的模型名称，默认 `memory-llm`
- `LLM_BASE_URL`：服务器 vLLM 地址
- `PROXY_PORT`：本地代理监听端口
- `LLM_ENABLE_THINKING`：关闭 Qwen3 思考模式，避免输出分析过程
- `LLM_MAX_TOKENS`：车机对话回答最大 token 数（256，含结构化决策+简短回答）
- `LLM_TEMPERATURE`：回答温度（0.2，更稳定）
- `LLM_TOP_P`：采样参数（0.8）
- `COCKPIT_TOOL_MODE`：车机工具模式（simulation/production）

> `.env` 已被 `.gitignore` 忽略，不会提交到 Git。

### 车机回答 vs 记忆后端 token 限制

| 场景 | 服务 | token 限制 | 说明 |
|------|------|-----------|------|
| 前端车机回答 | 本地 llm_proxy.py → vLLM (20140) | 256 | 结构化决策摘要 + 简短用户回答 |
| DesayMem 记忆提取 | 服务器记忆后端 (20142) → vLLM (20140) | 2000 | 记忆提取、画像、总结，需要更大上下文 |

前端车机回答的 `LLM_MAX_TOKENS=256` 不影响记忆后端的提取、画像和总结能力。记忆后端有自己的 `LLM_MAX_TOKENS=2000` 配置。

## `start.bat` 启动方法

双击 `start.bat`，脚本会：

1. 检查 Python 是否安装
2. 检查 `.env`，不存在则从 `.env.example` 复制
3. 检查 API Key 是否仍为占位符
4. 安装缺失的 Python 依赖
5. 启动本地 LLM 代理 (端口 8767)
6. 启动本地静态文件服务器 (端口 8080)
7. 打开浏览器 http://127.0.0.1:8080

启动完成后显示：

```
Frontend:       http://127.0.0.1:8080
LLM Proxy:      http://127.0.0.1:8767
Memory Backend: http://10.133.72.161:20142
LLM Server:     http://10.133.72.161:20140/v1
```

如果端口 8767 或 8080 已被占用，脚本会跳过对应服务的启动。

## 手动启动

```bash
pip install -r requirements.txt
python llm_proxy.py                          # 终端 1：启动 LLM 代理
python -m http.server 8080 --bind 127.0.0.1  # 终端 2：启动静态服务器
# 浏览器打开 http://127.0.0.1:8080
```

## PowerShell 连通性测试

### 健康检查 — 记忆后端

```powershell
Invoke-RestMethod http://10.133.72.161:20142/health
```

### 健康检查 — 本地 LLM 代理

```powershell
Invoke-RestMethod http://127.0.0.1:8767/health
```

### 连通性测试 — vLLM 模型服务

```powershell
Invoke-WebRequest http://10.133.72.161:20140/v1/models -Headers @{Authorization="Bearer YOUR_API_KEY"}
```

### 记忆写入验证

```powershell
$body = @{
    tenant_id = "oem_chery"
    user_id   = "user_test"
    vehicle_id = "vehicle_001"
    occupant_id = "driver"
    session_id = "session_test"
    scene = "driving"
    messages = @(
        @{ role = "user"; content = "我开车时喜欢把空调调到22度" }
    )
    infer = $true
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Post -Uri http://10.133.72.161:20142/v1/memories -ContentType "application/json" -Body $body
```

### 记忆检索验证

```powershell
$body = @{
    tenant_id = "oem_chery"
    user_id   = "user_test"
    query     = "空调温度"
    top_k     = 5
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://10.133.72.161:20142/v1/memories/search -ContentType "application/json" -Body $body
```

## CORS 排查

前端通过 `http://127.0.0.1:8080` 启动，避免 `file://` 页面的 `Origin null` 问题。

服务器记忆后端 (20142) 需要允许以下来源的跨域请求：

- `http://127.0.0.1:8080`
- `http://localhost:8080`

如果浏览器控制台出现 CORS 错误，检查服务器端 20142 的 CORS 配置是否包含上述来源。

本地 LLM 代理 (8767) 已配置 `allow_origins=["*"]`，不存在 CORS 问题。

## API Key 不一致的排查

如果 LLM 代理返回 HTTP 401/403 错误，说明 `.env` 中的 `LLM_API_KEY` 与服务器 vLLM 配置的 Key 不一致。

排查步骤：

1. 检查 `.env` 中 `LLM_API_KEY` 是否正确
2. 访问 `http://127.0.0.1:8767/health` 查看代理状态和 Key 掩码
3. 用 curl/PowerShell 直接测试服务器 vLLM：

```powershell
Invoke-WebRequest http://10.133.72.161:20140/v1/models -Headers @{Authorization="Bearer YOUR_API_KEY"}
```

## 思考过程泄露排错

如果界面输出模型思考过程（如"首先检查历史记忆…接下来按照规则…"）：

1. 检查 `.env` 中 `LLM_ENABLE_THINKING=false`
2. 确认重启了本地 8767 代理（见下方重启命令）
3. 确认旧的代理进程已经停止
4. 检查 vLLM 是否接受 `chat_template_kwargs.enable_thinking`（如果返回 HTTP 400，说明服务器不支持该参数）

## Windows 重启代理

```cmd
netstat -ano | findstr :8767
taskkill /PID <上面查到的PID> /F
start.bat
```

或者直接关闭"LLM Proxy"窗口后重新运行 `start.bat`。

## 结构化车机决策与开发者模式

### 为什么不直接展示原始思考链

模型原始思考链（`think` 内容）是模型内部分析过程，包含大量试错、规则引用和自言自语，不适合展示给开发者或用户。本项目关闭 Qwen3 思考模式（`enable_thinking=false`），改为让模型生成结构化决策摘要——经过整理、可验证的 JSON 格式信息。

### 结构化车机决策摘要是什么

每次车机回答包含三层：

1. **车机决策过程**（开发者可见）：意图、实体、缺失信息、记忆依据、当前决策、下一步工具、工具模式、决策说明、置信度
2. **记忆与性能调试**（开发者可见）：召回记忆数量及 score、检索耗时、模型耗时、总耗时、token 用量、记忆写入状态
3. **用户最终回答**（所有模式可见）：1-2 句简短中文，适合语音播报

开发者模式下两个调试卡片默认折叠，点击展开。关闭开发者模式后只显示最终回答。

### simulation 和 production 的区别

| 模式 | 说明 |
|------|------|
| simulation | 展示模拟的车机工具计划，标记"模拟决策，未执行真实车机操作"。不能声称操作已完成 |
| production | 只展示真实获得的状态和真实执行的工具结果。只有收到真实工具成功结果后才能声称操作完成 |

当前版本只模拟工具规划，没有真实执行车辆操作。

### 哪些信息来自真实系统

- 检索记忆内容和 score：来自记忆后端 (20142) 真实返回
- 模型名称：来自代理真实返回
- token 用量：来自 vLLM 真实 usage
- 耗时：由程序真实计算
- current_location：来自 vehicle_context（当前为 null，未接入定位）

### 哪些信息属于模拟计划

- next_tools：下一步计划工具（如地图搜索、路线规划），尚未执行
- action：计划动作（如询问位置），不是已执行结果
- tool_mode=simulation：标记当前为模拟模式

### 如何关闭开发者模式

顶栏右侧"开发者模式"复选框，取消勾选即可。状态保存在 localStorage，刷新后保持。

### 哪些内容会写入记忆

只有有效的用户输入（user 消息）和最终车机回答（assistant reply）会写入记忆。

**不会写入记忆的内容：**
- decision 决策摘要
- developerDebug 调试信息
- retrievedMemories 检索记忆
- explanation 决策说明
- 原始 thinking
- 工具计划
- 错误信息

模型调用失败时不写入记忆。

### 当前架构与后续接入

当前版本流程：
```
用户请求 → 记忆检索 → 结构化模拟决策 → 最终用户回答
```

后续接入真实工具时的目标流程：
```
用户请求 → 记忆检索 → 结构化决策 → 工具调用（定位/地图/导航/空调/音乐）→ 工具结果 → 最终用户回答
```

接入真实工具时，将 `COCKPIT_TOOL_MODE` 改为 `production`，并在前端 `vehicle_context` 中传入真实定位、车辆状态，在 `tool_results` 中传入真实工具执行结果。

## 默认服务地址

| 配置项 | 默认值 | 可修改 |
|--------|--------|--------|
| 后端记忆服务地址 | http://10.133.72.161:20142 | 登录页输入框 |
| LLM 代理地址 | http://127.0.0.1:8767 | 登录页输入框 |

## 记忆观测面板

右侧面板为"记忆观测"区域，展示当前用户在数据库中真实保存的全部记忆及其演化历史。与中间对话区域（回答"本轮为什么这样回答"）不同，右侧区域回答"数据库长期保存了什么、如何变化"。

### 标签页

| 标签 | 展示内容 |
|------|----------|
| 总览 | L1/L2/L3 总数、L2 active/complete、L3 active/superseded、今日演化事件数、自动遗忘状态、隔离测试 |
| L1 事实 | 全部 L1 原子事实：content、source、scene、occupant_id、metadata、删除按钮 |
| L2 情景 | 全部 L2 事件：摘要、episode_status、occurred_at、confidence、来源 L1 |
| L3 画像 | 全部 L3 信念：attribute、value、conditions、stability、status、confidence、evidence |
| 演化记录 | 审计事件倒序：时间、层级、事件类型、旧→新、reason、source，支持过滤 |

### 开发者模式

观测面板完整内容仅在开发者模式开启时显示。关闭时显示简化版"已启用记忆"。

### 接口依赖

- `GET /v1/users/{id}/memory-layers` — 加载 L1/L2/L3
- `GET /v1/users/{id}/memory-events` — 加载演化记录
- `POST /v1/memories` 写入后自动刷新观测
- `DELETE` 删除后自动刷新观测

### 数据刷新流程

1. 登录：/health → 加载记忆 → /memory-layers → /memory-events → 渲染
2. 对话写入后：刷新 memory-layers + memory-events
3. 删除后：同上
4. 观测失败：不影响对话，显示错误+重试按钮

### 常见错误排查

| 问题 | 解决 |
|------|------|
| 右侧"加载失败" | 后端未应用 migrations/006，或 /memory-layers 返回错误 |
| L1/L2/L3 均空 | tenant_id 不匹配或用户无记忆 |
| 演化记录空 | 审计表未创建，执行 desaymem-migrate apply |
| L2 来源"已不可用" | 来源 L1 已删除，正常行为 |
| L3"已被替代" | belief 被 SUPERSEDE，旧 belief 仍保留 |
