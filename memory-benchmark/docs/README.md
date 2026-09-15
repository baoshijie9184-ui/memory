# 记忆系统评测平台 — 文档总索引

> 统一入口：所有文档从这里找。新文档建立后必须在本索引登记。
> 最后更新：2026-09-15（§五 配置速查：5.6 端口全景 / 5.7 PG 双实例 / 5.8 存储位置全景）

## 一、平台与操作

| 文档 | 内容 | 何时看 |
|---|---|---|
| [Memory-Benchmark平台建立与目录规划.md](Memory-Benchmark平台建立与目录规划.md) | 工作区目录结构、环境约定（envs/eval、envs/lightmem、envs/embedding）、多系统多数据集总体规划 | 新人入门、新增目录/环境 |
| [Benchmark后端更新与重启操作手册.md](Benchmark后端更新与重启操作手册.md) | memory-llm（127.0.0.1:20140）等后端服务启停 | 评测前服务检查、服务挂了 |
| scripts/run_full.sh | 全量一键脚本：改顶部 4 变量（系统/数据集/模型/结果目录）即跑，自动建库+断点续跑+评测 | 跑全量首选入口 |

## 二、数据集（五套）

| 文档 | 数据集 | 内容 |
|---|---|---|
| [LoCoMo数据集详解.md](LoCoMo数据集详解.md) | locomo | 数据构成、题型、LoCoMo 全量库（conv-26/30/41…） |
| [VehicleMemBench数据集详解.md](VehicleMemBench数据集详解.md) | vehiclemembench | 官方格式、50 文件、tool_f1 口径 |
| [三数据集适配调研与施工记录.md](三数据集适配调研与施工记录.md) | **carmem / longmemeval / vehiclemembench / desaymem** | **核心施工日志 DS-1~DS-11**：适配全过程、坑与修复、**§7 全量跑操作手册**（跑全量照这个） |
| [记忆系统×数据集结果矩阵.md](记忆系统×数据集结果矩阵.md) | **全部五套** | **成绩总表**：每个系统×数据集的冒烟分数、结论与观察；全量结果也记这里 |
| [DesayMem多租户数据集接入方案.md](DesayMem多租户数据集接入方案.md) | desaymem（第五数据集） | 多租户隔离数据集质量分析、断言式评测设计、P0/P1/P2 优化计划、接入状态 |
| 数据集评测/ 子目录 | 各数据集 | VehicleMemBench 合成分析、DesayMem 纯 Python 环境指南 |
| datasets/DesayMem_multitenant_benchmark/README.md | desaymem 数据本体 | 数据组织（814 sessions/2 租户/6 用户/26 用例）、四组断言用例、官方 HTTP 评测方式 |

## 三、记忆系统与评测仓库

| 文档 | 内容 | 何时看 |
|---|---|---|
| [VehicleMem-Eval多系统接入改造记录.md](VehicleMem-Eval多系统接入改造记录.md) | datasets/VehicleMem-Eval 仓库架构：adapter × bridge 双维度解耦、MEMORY_SYSTEMS 注册表、加新方法/新数据集步骤 | 接入新系统/新数据集 |
| [LightMem-LoCoMo全量测试指南.md](LightMem-LoCoMo全量测试指南.md) | LightMem 管线（from_config/add_memory/summarize）、建库脚本用法、LoCoMo 全量测试 | 建离线库、调 LightMem 参数 |
| [2026-09-14-DesayMem_light车辆记忆P1代码分析与测试.md](2026-09-14-DesayMem_light车辆记忆P1代码分析与测试.md) | DesayMem_light 系统代码分析 | 了解该系统内部实现 |

## 四、当前状态快照（2026-09-15）

- **适配完成度**：5 数据集 × {none, mem0, structmem, structmem-nosummary} 冒烟全通，成绩见 [记忆系统×数据集结果矩阵.md](记忆系统×数据集结果矩阵.md)
  - 例外：none×longmemeval 超模型 16384 上下文（模型限制）；vehiclemem(desaymem 原生) 本机缺 edge 目录
  - desaymem（最新）：strict 11/26，真 FAIL 均为 LightMem 抽取层丢实体（详见 DS-11）
- **离线库**：data/lightmem/qdrant_new_datasets/（vmb/lme/carmem 微量 user_0 + desaymem 全量 10 collection）
- **待做**：① 全量跑（一键脚本 scripts/run_full.sh，或三数据集适配文档 §7）② desaymem P0 优化 + mem0 对照 ③ carmem Extraction 类别注入
- **GPU 注意**：白天全占，建库用 CUDA_VISIBLE_DEVICES=5；建库必须 nohup（前台超时被杀）

## 五、配置速查（环境在哪里配）

### 5.1 LLM / 嵌入模型 → `datasets/VehicleMem-Eval/config/models.yaml`
评测模型全在这配（`--model-config` 从这取名）。主力配置 **memory-llm**（models.yaml:39）：
```yaml
memory-llm:
  api_base: "http://127.0.0.1:20140/v1"        # 本机 Qwen 服务（vLLM 起）
  api_key: "boluoboluomi"
  model: "memory-llm"
  judge_model: "memory-llm"
  embedding_base: "http://127.0.0.1:20141/v1"  # bge-m3 嵌入服务
  embedding_model: "bge-m3"
  embedding_key: "boluoboluomi"
```
同文件还有 deepseek/openai 等 key 模板（api_key 也可用环境变量 LLM_API_KEY）。服务重启见 §一 操作手册。

### 5.2 数据集路径与冒烟限制 → `datasets/VehicleMem-Eval/config/datasets.yaml`
- 每个 `datasets.<name>`：data_file/history_dir/qa_dir（相对 VehicleMem-Eval 根）
- 冒烟限制：qa_limit / user_limit / pref_limit / file_range / case_limit，`--full` 临时清零

### 5.3 Python 环境 → `envs/`（严格分工，不能混用）
| 环境 | 用途 | 备注 |
|---|---|---|
| `envs/eval/bin/python` | 评测（run.py） | 缺 lightmem/tiktoken |
| `envs/lightmem/bin/python` | 建离线库（build_lightmem_lib.py） | 有 lightmem/qdrant_client，也能 import VehicleMem-Eval adapters |
| `envs/embedding` | 20141 嵌入服务本体 | 一般不动 |

### 5.4 建库脚本常量 → `systems/LightMem/experiments/locomo/build_lightmem_lib.py:39-44`
API 地址/key、llmlingua-2 与 bge-m3 本地模型路径，硬编码（与 add_locomo.py 一致）；改 LLM 端口要两处同步。

### 5.5 运行时环境变量与 per-system 配置

**per-system 覆盖文件**（不同算法配置不同，各归各文件）：`datasets/VehicleMem-Eval/config/systems/{系统名}.env`，run_full.sh 自动 source（无则跳过）。现有 structmem.env（建库 LLM：LIGHTMEM_API_BASE/KEY/MODEL）。新系统接入时建自己的 .env 即可，不用改主脚本。

手工跑命令时才需要带的环境变量：
| 变量 | 用途 | 何时必带 |
|---|---|---|
| `LIGHTMEM_LIB_ROOT` | structmem 离线库根目录 | structmem/nosummary 评测 |
| `LIGHTMEM_COLLECTION_PREFIX` | collection 前缀（数据集名+下划线，如 carmem_ / desaymem_） | 同上 |
| `TIKTOKEN_CACHE_DIR=/data/pengshuang/memory-benchmark/tmp/tiktoken_cache` | 防 tiktoken 联网下载超时 | 建库必带 |
| `CUDA_VISIBLE_DEVICES=5` | GPU 0-7 常全占，5 通常空闲 | 建库必查 nvidia-smi |

### 5.6 后端服务端口全景（2026-09-15 实测）

| 端口 | 服务 | 进程/启动方式 | 状态 |
|---|---|---|---|
| **20140** | **memory-llm**（Qwen3-32B vLLM，评测主力 LLM） | `envs/llm/bin/vllm serve .../Qwen3-32B --served-model-name memory-llm --port 20140 --api-key boluoboluomi --max-model-len 16384` | 常驻 |
| **20141** | **bge-m3 嵌入服务**（评测+建库共用） | `envs/embedding/bin/uvicorn server:app --port 20141` | 常驻 |
| 20142 | DesayMem **生产**后端（10.133.72.161，勿动） | 生产环境 apps/DesayMem_mem0 | 勿混用 |
| **20143** | **PostgreSQL**（DesayMem 后端数据库） | `envs/postgres/bin/postgres -D data/postgres`；库：bench_desaymem（评测）/ desaymem（生产，勿动） | 常驻 |
| **20144** | **DesayMem Benchmark 后端**（评测用 API） | `cd systems/DesayMem_mem0 && uvicorn desaymem.api.main:app --port 20144`，screen 会话 `benchmark-desaymem` | 常驻 |
| 20148 | DesayMem_light 后端 API | `uvicorn desaymem_light.api.main:app --port 20148` | 常驻 |
| 20149 | PostgreSQL（DesayMem_light 专用实例） | 库：bench_desaymem_light / bench_desaymem_light_test / bench_vehicle_it | 常驻 |

### 5.7 PostgreSQL 双实例明细（2026-09-15 实测）

两个独立 postgres 进程，各配各的 PGDATA，互不相干：

**实例 1 — 端口 20143（DesayMem_mem0 生产+评测共用）**
- 进程：`envs/postgres/bin/postgres -D /data/pengshuang/desaymem/data/postgres`
- 对应方法：**DesayMem_mem0**（systems/DesayMem_mem0，端口 20142 生产 / 20144 评测）
- 库与使用者：
  | 库 | 使用者 | DSN |
  |---|---|---|
  | `desaymem` | **生产**（/data/pengshuang/desaymem/apps/DesayMem_mem0/.env） | postgresql://desaymem@127.0.0.1:20143/desaymem |
  | `bench_desaymem` | **Benchmark 评测**（/data/pengshuang/memory-benchmark/systems/DesayMem_mem0/.env） | postgresql://desaymem@127.0.0.1:20143/bench_desaymem |
- ⚠️ 同实例双库：评测迁移只许打 bench_desaymem，严禁碰 desaymem
- 会话历史 SQLite：评测 /data/pengshuang/memory-benchmark/data/desaymem/history.db；生产 /data/pengshuang/desaymem/data/history.db

**实例 2 — 端口 20149（DesayMem_light 专用）**
- 进程：`envs/postgres/bin/postgres -D /data/pengshuang/memory-benchmark/data/desaymem_light/postgres`
- 对应方法：**DesayMem_light**（systems/DesayMem_light，API 端口 20148）
- DSN（systems/DesayMem_light/.env）：postgresql://desaymem@127.0.0.1:20149/bench_desaymem_light
- 库：`bench_desaymem_light`（原评测库，26 表，未应用 017-022 迁移）/ `bench_desaymem_light_test`（22 迁移全量，API/worker 验证用）/ `bench_vehicle_it`（集成测试空库）
- 数据目录另含：history.db（会话 SQLite）+ json_mirror/

**不属于 PostgreSQL 的记忆方法存储**：
| 方法 | 存储 | 位置 |
|---|---|---|
| structmem / structmem-nosummary（LightMem） | Qdrant 目录模式（每 collection 独立 sqlite，无端口） | data/lightmem/qdrant_new_datasets/qdrant_post_update/（新数据集）+ qdrant_post_update/（LoCoMo 全量） |
| DesayMem_structmem（旧实验） | SQLite + json_mirror | data/structmem/（structmem.db） |
| mem0 bridge 评测 | qdrant 本地目录 | datasets/VehicleMem-Eval/results/mem_data/mem0_{model}/ |

### 5.8 存储位置全景（方法 × 数据 × 物理路径，2026-09-15 实测）

#### A. 数据库类

| 方法 | 存储类型 | 物理路径 | 端口/说明 |
|---|---|---|---|
| DesayMem_mem0（生产） | PostgreSQL 库 `desaymem` | `/data/pengshuang/desaymem/data/postgres/base/`（PGDATA 整体） | 20143 |
| DesayMem_mem0（评测） | PostgreSQL 库 `bench_desaymem` | 同上（同实例不同 database） | 20143 |
| DesayMem_mem0 会话历史 | SQLite | 评测 `/data/pengshuang/memory-benchmark/data/desaymem/history.db`；生产 `/data/pengshuang/desaymem/data/history.db` | 无 |
| DesayMem_mem0 JSON 镜像 | JSON 文件 | `/data/pengshuang/memory-benchmark/data/desaymem/json_mirror/`（bench_desaymem / bench_desaymem_test / desaymem / history 子目录） | 导出/审计用 |
| DesayMem_light | PostgreSQL 库 `bench_desaymem_light` 等 3 库 | `/data/pengshuang/memory-benchmark/data/desaymem_light/postgres/base/`（PGDATA 整体） | 20149 |
| DesayMem_light 会话历史 | SQLite | `/data/pengshuang/memory-benchmark/data/desaymem_light/history.db` | 无 |
| DesayMem_light JSON 镜像 | JSON 文件 | `/data/pengshuang/memory-benchmark/data/desaymem_light/json_mirror/postgres/`（memory_items / memory_evidence / cross_event_checkpoints 等） | |

> PostgreSQL 数据看不到单独"库文件"——一个 PGDATA 的 base/ 下每数字目录是一个 database，备份/搬动以整个 PGDATA 为单位。

#### B. 向量库类（Qdrant 目录模式，无端口，每 collection 独立 sqlite）

| 库根 | 内容 | 用途 |
|---|---|---|
| `data/lightmem/qdrant_post_update/` | conv-26/30/41… + 各 _summary（LoCoMo 10 对话全量，805 条/对话） | structmem × locomo 评测 |
| `data/lightmem/qdrant_pre_update/` | 同上的 pre 副本（summarize 输入快照） | 不直接用 |
| `data/lightmem/qdrant_new_datasets/qdrant_post_update/` | **新数据集微量库**：vehiclemembench_user_0（264）、longmemeval_user_0（294）、carmem_user_0（22）+ **desaymem 全量 10 collection**（desaymem_usr_father 529 条等，含粗/细粒度） | structmem × 新四数据集评测（`LIGHTMEM_LIB_ROOT` 指向这里） |
| `data/lightmem/qdrant_new_datasets/qdrant_pre_update/` | 同上 pre 副本 | 不直接用 |
| 真实数据在 | 每 collection 的 `{库根}/{collection}/collection/{collection}/storage.sqlite` | bridge 直读此文件 |

#### C. 旧实验与评测产物

| 内容 | 物理路径 |
|---|---|
| DesayMem_structmem 旧实验（SQLite） | `data/structmem/structmem.db` + `data/structmem/json_mirror/`、logs/ |
| mem0 bridge 评测存储（qdrant 目录） | `datasets/VehicleMem-Eval/results/mem_data/mem0_memory-llm/` |
| 全部评测结果（json+txt 明细，按 数据集_模型_系统_时间戳 命名） | `datasets/VehicleMem-Eval/results/`（冒烟与全量都在） |
| LightMem 检索冒烟产物 | `data/lightmem/search_smoke_results/` |

#### D. 环境与缓存

| 内容 | 物理路径 |
|---|---|
| 三个 python 环境 | `envs/eval`、`envs/lightmem`、`envs/desaymem`（+desaymem 侧 envs/llm、envs/embedding、envs/postgres） |
| tiktoken 缓存（建库必带 TIKTOKEN_CACHE_DIR） | `/data/pengshuang/memory-benchmark/tmp/tiktoken_cache` |
| 本地模型 | `/data/pengshuang/desaymem/models/`（Qwen3-32B、bge-m3、llmlingua-2） |




注意事项：
- 20142/20143 的**生产库 desaymem 绝对不要跑 benchmark 迁移**（操作手册安全检查）
- 重启 20140/20141 各服务见 `Benchmark后端更新与重启操作手册.md`
- 多租户数据集官方 HTTP 评测默认打 20142（`--base-url` 可切 20144）
- 存储物理路径详见 §5.8

## 六、文档维护规则

1. 新文档放 docs/（或 docs/数据集评测/），建完在本索引加一行
2. 施工过程写进「三数据集适配调研与施工记录.md」的 DS-N 日志（N 递增，勿改历史条目）
3. 新数据集：先写「XX接入方案.md」（分析+设计+状态 checklist），施工细节进 DS-N
4. 文档里的命令必须可直接复制执行（含环境变量、绝对路径）
5. 过时信息：直接更新对应文档，并在本索引"最后更新"改日期
