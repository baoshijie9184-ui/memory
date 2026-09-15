# DesayMem H20 部署与维护文档

本文档集记录当前已经验证通过的公司 H20 部署方案，适用于后续开发、联调、升级和故障处理。

> 模型准确名称为 **Qwen3-32B**。历史交流中出现的“Qwen3.8”只是口头称呼，并非当前模型目录名称。

## 1. 当前架构

```text
Windows 前端 cockpit-frontend
  ├─ http://10.133.72.161:20142  → DesayMem 记忆后端
  └─ http://127.0.0.1:8767       → 本地 LLM Proxy
                                      └─ http://10.133.72.161:20140/v1 → Qwen3-32B

DesayMem 记忆后端
  ├─ http://127.0.0.1:20140/v1 → Qwen3-32B：提取、重排、画像、Episode
  ├─ http://127.0.0.1:20141/v1 → BGE-M3：1024 维向量
  ├─ 127.0.0.1:20143           → PostgreSQL 16 + pgvector
  └─ /data/pengshuang/desaymem/data/history.db → SQLite 会话历史
```

## 2. 服务清单

| 服务 | 端口 | 监听地址 | screen 会话 | 作用 |
|---|---:|---|---|---|
| Qwen3-32B | 20140 | `0.0.0.0` | `llm` | OpenAI 兼容 LLM |
| BGE-M3 | 20141 | `0.0.0.0` | `embedding` | OpenAI 兼容 Embedding |
| DesayMem API | 20142 | `0.0.0.0` | `memory-backend` | 记忆读写检索 |
| PostgreSQL | 20143 | `127.0.0.1` | 非 screen/pg_ctl | 结构化记忆和向量 |
| 本地 LLM Proxy | 8767 | Windows `127.0.0.1` | 本地进程 | 对话生成、隐藏 Key |
| SSH | 2214 | 服务器入口 | — | 服务器登录 |

服务器 IP：`10.133.72.161`；可用业务端口段：`20140–20149`。

## 3. 目录清单

| 内容 | 路径 |
|---|---|
| 项目根目录 | `/data/pengshuang/desaymem` |
| Qwen 模型 | `/data/pengshuang/desaymem/models/Qwen3-32B` |
| BGE-M3 模型 | `/data/pengshuang/desaymem/models/bge-m3` |
| LLM 环境 | `/data/pengshuang/desaymem/envs/llm` |
| Embedding 环境 | `/data/pengshuang/desaymem/envs/embedding` |
| 后端环境 | `/data/pengshuang/desaymem/envs/backend` |
| PostgreSQL 环境 | `/data/pengshuang/desaymem/envs/postgres` |
| screen 环境 | `/data/pengshuang/desaymem/envs/screen` |
| 后端仓库 | `/data/pengshuang/desaymem/apps/DesayMem_mem0` |
| PostgreSQL 数据 | `/data/pengshuang/desaymem/data/postgres` |
| SQLite 历史 | `/data/pengshuang/desaymem/data/history.db` |
| 日志目录 | `/data/pengshuang/desaymem/logs` |

## 4. 推荐阅读顺序

1. [01_LLM_Qwen3-32B部署.md](01_LLM_Qwen3-32B部署.md)
2. [02_Embedding_BGE-M3部署.md](02_Embedding_BGE-M3部署.md)
3. [03_数据库_PostgreSQL与SQLite.md](03_数据库_PostgreSQL与SQLite.md)
4. [04_记忆后端部署与配置.md](04_记忆后端部署与配置.md)
5. [05_后端原理与数据流.md](05_后端原理与数据流.md)
6. [06_前端适配与联调.md](06_前端适配与联调.md)
7. [07_GitHub更新与发布.md](07_GitHub更新与发布.md)
8. [08_日常运维与故障排查.md](08_日常运维与故障排查.md)
9. [09_安全备份与后续优化.md](09_安全备份与后续优化.md)

## 5. 每日最短检查

```bash
export PATH=/data/pengshuang/desaymem/envs/screen/bin:$PATH
screen -ls

/data/pengshuang/desaymem/envs/postgres/bin/pg_isready \
  -h 127.0.0.1 -p 20143
```

本地 PowerShell：

```powershell
Invoke-RestMethod -NoProxy http://10.133.72.161:20142/health
```

## 6. 重要安全说明

- 文档不记录任何真实 API Key；真实 Key 仅写入权限为 `600` 的 `.env` 或交互式环境变量。
- 当前记忆后端 20142 尚未提供独立鉴权，适合临时内网联调，不适合公网暴露。
- PostgreSQL 只监听 `127.0.0.1`，不要对公司网络开放。
- 已在聊天或截图中出现过的 Key，正式使用前建议轮换。

