# DesayMem_mem0

德赛车载云端长期记忆系统，从 [Mem0 OSS](https://github.com/mem0ai/mem0) 2.0.18 源码迁移重构，使用 `desaymem` 命名空间独立运行。

> 本项目**不是** `mem0ai` 包的运行时依赖，也**不是** Mem0 云 API 的封装。

上游快照：Mem0 OSS `2.0.18`，commit `4fa48390`，Apache-2.0。详见 [docs/upstream-mem0.md](docs/upstream-mem0.md)、[docs/source-mapping.md](docs/source-mapping.md)。

> **面向管理层的汇报**：[docs/EXECUTIVE_REPORT.md](docs/EXECUTIVE_REPORT.md) — 一页纸看懂系统价值、当前进展和下一步

---

## 系统定位

| 痛点 | 座舱场景 | 解决方式 |
|------|----------|----------|
| 记不住 | 上次说了空调 22 度，下次又问 | L3 画像独立表直读，每轮检索必带 |
| 想不起 | "上周那次""老规矩"命中不了 | L2 事件层 + 实体链接 + LLM 精排时间换算 |
| 分不清 | 旧偏好和新偏好混在一起返回 | ADD-only 真源 + 冲突消解只返回最新值 |

```
对话输入
  → 写入七步（last-k → 近邻 → LLM 抽取 → 去重 → 入库 → 实体 → 派生 L2/L3）
  → 检索十阶段（归一化 → 向量+BM25 并集 → 组合打分 → 冲突消解 → LLM 精排）
  → 返回 { memories, profile }
```

技术栈：Python 3.10+ / FastAPI / PostgreSQL 16 + pgvector / SQLite / OpenAI 兼容 LLM (Qwen/DeepSeek) + Embedding (BGE-M3 1024d)

---

## 三层记忆架构

| 层 | 存储 | 是什么 | 对应 Mem0 没有的能力 |
|----|------|--------|---------------------|
| **L1 Fact** | `memory_items`（semantic_memory） | 原子事实，ADD-only 不可变 | Mem0 可被 UPDATE/DELETE 改写 |
| **L2 Episode** | `memory_items`（episodic_memory） | 一次经历的摘要，带 `occurred_at` 绝对时间 + `source_memory_ids` 指回 L1 | Mem0 无事件归组 |
| **L3 Profile** | `profile_beliefs`（独立表） | 用户当前画像，主键 O(1) 直读，带 `status`/`stability`/`conditions`/`evidence_memory_ids` | Mem0 无独立画像层 |

**LLM 提建议、Python 管写库**——LLM 输出 JSON belief，Python 两道硬校验后执行：证据 ID 必须真实存在；recurring 习惯需 ≥2 个不同自然日证据。L2/L3 均可从 L1 全量重建。

> 完整架构、写入七步、检索十阶段、冲突消解、精排容错等细节见 **[docs/TECHNICAL_REPORT.md](docs/TECHNICAL_REPORT.md)**

---

## 当前能力

- `add`：写入对话并抽取记忆事实（默认 ADD-only）；L1 入库后 best-effort 派生 L2 事件摘要 + L3 画像
- `memory_type=procedural_memory`：流程记忆写入
- `infer=false`：不抽事实，直接入库原文
- `search`：向量+BM25 双路并集 → 组合打分 → 冲突消解 → LLM 精排，响应附带 `profile`
- `GET /v1/users/{user_id}/profile`：当前画像（支持 `include_superseded`、`occupant_id`、`limit`）
- `GET /v1/users/{user_id}/memory-layers`：L1/L2/L3 三层统一观测查询
- `GET /v1/users/{user_id}/memory-events`：记忆演化审计事件（游标分页，支持层级/事件类型过滤）
- `history`：单条记忆 ADD/DELETE 事件
- `delete` / `delete_all`：清理记忆 + 实体 + 画像 + 审计
- 租户+用户隔离（检索与删除强制 `tenant_id + user_id`）
- 实体库：抽取、同义合并（≥0.95）、检索加权 boost
- 前后端分离 LLM Proxy（API Key 鉴权 + CORS 白名单 + 错误脱敏）
- 审计事件：L1/L2/L3 每次变更自动记录到 `memory_audit_events` 表，支持 SAFETY 降级（审计失败不阻断记忆写入）

**暂未实现**：Knowledge Memory、Skill 蒸馏、主动服务、多模态、图记忆、端云同步

---

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows
python -m pip install -e ".[dev]"
copy .env.example .env              # 填入 POSTGRES_DSN、LLM_*、EMBEDDING_*
python -m desaymem.cli apply        # 执行 SQL 迁移
python -m pytest -s -v              # 真实 LLM + PostgreSQL 测试
docker compose up --build -d        # 或 Docker 部署
```

健康检查：http://localhost:8000/health ｜ Swagger：http://localhost:8000/docs

服务器部署端口：

| 端口 | 服务 |
|------|------|
| 20140 | Qwen vLLM 模型服务 |
| 20141 | BGE-M3 Embedding 服务 |
| 20142 | DesayMem API（旧实例，不含观测接口） |
| 20143 | PostgreSQL |
| 20144 | DesayMem API（当前实例，含观测接口） |

前端默认连接 20144。

默认 embedding 维度 1024（BGE-M3）。切换维度需同步修改 `migrations/001_initial.sql` 的 `VECTOR(n)`。

---

## HTTP 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `POST` | `/v1/memories` | 写入对话并抽取记忆 |
| `POST` | `/v1/memories/search` | 语义检索（附带 profile） |
| `GET` | `/v1/users/{user_id}/memories` | 列出用户记忆 |
| `GET` | `/v1/users/{user_id}/profile` | 当前画像（支持 `include_superseded`、`occupant_id`、`limit`） |
| `GET` | `/v1/users/{user_id}/memories/{memory_id}/history` | 记忆变更历史 |
| `GET` | `/v1/users/{user_id}/memory-layers` | 统一 L1/L2/L3 三层记忆观测查询 |
| `GET` | `/v1/users/{user_id}/memory-events` | 记忆演化审计事件（游标分页） |
| `DELETE` | `/v1/users/{user_id}/memories/{memory_id}` | 删除单条 |
| `DELETE` | `/v1/users/{user_id}/memories?confirm=true` | 清空用户记忆（含审计数据） |

> 完整字段、示例和错误码见 [docs/api.md](docs/api.md)
> 三层观测与审计事件语义见 [docs/memory_observability.md](docs/memory_observability.md)
> 前后端交互文档见 [cockpit-frontend/docs/frontend-backend-interaction.md](https://github.com/psile/cockpit-frontend/blob/main/docs/frontend-backend-interaction.md)

---

## L1/L2/L3 记忆演化

三层记忆不是静态存储，而是随每次对话动态演化的。每次 `POST /v1/memories` 写入新对话后，系统自动执行：

```
对话写入
  → L1 ADD: 从对话中抽取原子事实，不可变追加写入
  → L2 UPDATE/ADD: 判断续写当前 episode 还是关闭旧的开新的
  → L3 CREATE/CONFIRM/SUPERSEDE/COEXIST: 蒸馏画像信念，处理偏好变更
  → 审计事件: 每一步操作记录到 memory_audit_events 表
```

### 演化事件类型

| 事件 | 层级 | 触发场景 |
|------|------|----------|
| ADD | L1/L2/L3 | 新增记忆/事件/信念 |
| UPDATE | L2 | episode 续写（continues=true） |
| CONFIRM | L3 | 新证据再次确认已有信念，support_count+1 |
| COMPLETE | L2 | 旧 episode 自然结束（continues=false），标记 complete |
| SUPERSEDE | L3 | 偏好变更，旧信念标记 superseded，新信念 active |
| COEXIST | L3 | 不同条件下的信念并存（如夏天 22 度 / 冬天 26 度） |
| DELETE | L1/L2 | 人工删除单条记忆 |
| FORGET | 预留 | 自动遗忘（当前未启用） |

### 观测接口

通过 `GET /memory-layers` 和 `GET /memory-events` 可以实时查看三层记忆的状态和变更历史：

- **memory-layers**：返回当前用户 L1/L2/L3 全量数据 + 统计数（l1_count/l2_active_count/l3_superseded_count 等）
- **memory-events**：返回审计事件倒序列表，支持按层级/事件类型过滤和游标分页

前端 cockpit-frontend 在每次对话写入或删除后自动刷新观测面板。

---

## 目录结构

```
DesayMem_mem0/
├── src/desaymem/
│   ├── api/routes/          # FastAPI 路由（memories, health）
│   ├── core/                # DesayMemory 编排、配置、模型、异常
│   ├── extraction/          # ADD-only 抽取、去重、实体抽取与链接
│   ├── retrieval/           # 十阶段混合检索、打分、冲突消解
│   ├── layers/              # L2 EpisodeBuilder + L3 ProfileDistiller + SemanticReranker
│   ├── providers/           # OpenAI 兼容 LLM / Embedding
│   ├── stores/              # pgvector / SQLite history / 实体 / 画像 / 审计
│   ├── services/            # 业务门面
│   └── sql/                 # 内嵌 SQL
├── migrations/              # 001~006 SQL 迁移
├── scripts/                 # Demo、模拟器、评测脚本、LLM Proxy
├── tests/                   # unit + integration + scenarios
├── docs/                    # 活跃文档
│   └── archived/            # 归档文档（历史参考）
├── data_cesi/               # 评测数据集与基线报告
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

---

## 脚本

| 脚本 | 用途 |
|------|------|
| `scripts/baseline_demo.py` | 基线冒烟（add → search → listed） |
| `scripts/cockpit_simulator.py` | 车机多轮对话模拟器（5 轮 Live LLM） |
| `scripts/run_yearlong_http_test.py` | 年度评测（13 用例：5 模糊 + 8 画像） |
| `scripts/generate_yearlong_cockpit_dataset.py` | 生成 365 天车机对话数据集 |
| `scripts/inspect_memories.py` | 查看已落库记忆（运维） |
| `scripts/compare_with_upstream.py` | 与 Mem0 OSS 定性对比 |
| `scripts/llm_proxy.py` | 前端 LLM 代理（鉴权 + 脱敏） |

---

## 文档

> **架构真源**：[TECHNICAL_REPORT.md](docs/TECHNICAL_REPORT.md) — 每次代码变更后同步更新此文件

### 活跃文档

| 文档 | 用途 | 何时更新 |
|------|------|----------|
| [TECHNICAL_REPORT.md](docs/TECHNICAL_REPORT.md) | 架构真源（三层 L1/L2/L3 + 写入七步 + 检索十阶段 + 冲突消解 + 精排） | **代码变更时同步** |
| [EXECUTIVE_REPORT.md](docs/EXECUTIVE_REPORT.md) | 面向管理层的执行摘要（一页纸：价值、进展、风险、下一步） | 版本发布时 |
| [api.md](docs/api.md) | HTTP API 接口文档 | 接口变更时 |
| [memory_observability.md](docs/memory_observability.md) | L1/L2/L3 观测接口与审计事件语义 | 观测/审计变更时 |
| [layers.md](docs/layers.md) | L2/L3 数据模型速查 | 表结构变更时 |
| [cloud-deploy.md](docs/cloud-deploy.md) | 云端部署指南 | 部署变更时 |
| [test-scenarios.md](docs/test-scenarios.md) | 浏览器 Demo 测试场景手册 | 场景变更时 |
| [MEM0_IMPROVEMENTS_FOR_BOSSES.md](docs/MEM0_IMPROVEMENTS_FOR_BOSSES.md) | 面向管理层的改进汇报 | 版本发布时 |
| [architecture_diagram.html](docs/architecture_diagram.html) | 交互式 6 分页架构图 | 架构变更时重新导出 |
| [source-mapping.md](docs/source-mapping.md) | DesayMem ↔ Mem0 OSS 文件映射 | 文件迁移时 |
| [upstream-mem0.md](docs/upstream-mem0.md) | Mem0 OSS 2.0.18 上游快照 | 上游升级时 |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | Apache-2.0 归属声明 | 新增依赖时 |

### 归档文档

> [docs/archived/](docs/archived/) — 旧版文档，仅供历史追溯，不再维护

| 文档 | 归档原因 |
|------|----------|
| `archived/TECHNICAL_ANALYSIS_2026-09-03.md` | 评审快照，已被 TECHNICAL_REPORT 覆盖 |
| `archived/PROJECT_OVERVIEW.md` | 架构描述停留在单层基线 |
| `archived/architecture.md` | mermaid 图不含 L2/L3 |
| `archived/baseline-design.md` | 旧七阶段写入流程，已被三层取代 |
| `archived/cockpit-memory-design.md` | 旧架构博客叙事 |
| `archived/cockpit-simulator.md` | 旧模拟器说明，已被 yearlong 测试取代 |

### 评测数据文档

| 文档 | 位置 | 用途 |
|------|------|------|
| 数据集说明 | `data_cesi/yearlong-fuzzy-memory-dataset.md` | 365 天对话数据集构造 |
| 测试指南 | `data_cesi/memory-add-search-test-guide.md` | add/search 接口测试 |
| 基线报告 | `data_cesi/reports/baseline_pre_layers_latest.md` | 旧架构基线 3/5 通过 |

---

## 文档维护规则

1. **架构真源唯一**：`TECHNICAL_REPORT.md` 是唯一架构真源，其他文档引用它而非重复描述
2. **代码变更先更文档**：改 `src/` 或 `migrations/` 后，检查 TECHNICAL_REPORT.md 和 api.md 是否需要同步
3. **过时即归档**：不再维护的文档移入 `docs/archived/`，不删除
4. **新增文档登记**：在上表注册后创建文件

---

## License

Apache License 2.0。含 Mem0 OSS 衍生代码，版权与来源见 [LICENSE](LICENSE) 与 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
