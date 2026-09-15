# Memory Benchmark 平台建立与目录规划

本文用于在公司 H20 服务器上建立统一的记忆系统评测工作区，并规范后续接入 DesayMem、Mem0、MemoryOS 等记忆系统时的代码、数据、环境、配置和实验结果管理。

## 1. 建设目标

建立独立目录：

```text
/data/pengshuang/memory-benchmark/
```

该目录只用于：

- 记忆系统 Benchmark 开发与运行；
- 多个记忆库的统一接入和横向对比；
- 测试数据集管理；
- 不同 LLM、Embedding、参数组合的实验；
- 指标计算、实验结果保存和报告生成；
- 前端调试、接口联调及自动化测试。

原有目录：

```text
/data/pengshuang/desaymem/
```

继续作为稳定部署区，只负责生产/演示后端及共享模型服务，不放 Benchmark 仓库、评测数据和实验结果。

## 2. 两个工作区的职责边界

| 路径 | 定位 | 内容 |
|---|---|---|
| `/data/pengshuang/desaymem/` | 稳定部署区 | 正式 DesayMem 后端、PostgreSQL、LLM、Embedding、部署环境、运行日志 |
| `/data/pengshuang/memory-benchmark/` | 实验评测区 | 对比系统、测试集、实验配置、运行脚本、评测结果、分析报告 |

基本原则：

1. Benchmark 可以调用 `/data/pengshuang/desaymem/` 中已经启动的共享 LLM 和 Embedding 服务。
2. Benchmark 不直接修改正式部署代码和生产数据库。
3. 每个记忆系统使用独立数据库、独立 SQLite 文件或独立数据目录。
4. 代码、输入数据、实验输出分开保存。
5. 实验结果必须能够通过配置和版本信息复现。

## 3. 推荐的完整目录结构

```text
/data/pengshuang/memory-benchmark/
├── README.md
├── systems/
│   ├── DesayMem_mem0/
│   ├── mem0/
│   ├── MemoryOS/
│   └── adapters/
├── envs/
│   ├── desaymem/
│   ├── mem0/
│   └── memoryos/
├── datasets/
│   ├── raw/
│   ├── processed/
│   ├── ground_truth/
│   ├── splits/
│   └── README.md
├── configs/
│   ├── systems/
│   ├── models/
│   ├── experiments/
│   └── secrets.example.env
├── scripts/
│   ├── setup/
│   ├── start/
│   ├── stop/
│   ├── health/
│   ├── benchmark/
│   └── database/
├── data/
│   ├── desaymem/
│   ├── mem0/
│   └── memoryos/
├── results/
│   ├── raw/
│   ├── metrics/
│   ├── comparisons/
│   └── archive/
├── logs/
│   ├── services/
│   └── experiments/
├── reports/
│   ├── figures/
│   ├── tables/
│   └── releases/
├── docs/
│   ├── architecture/
│   ├── systems/
│   ├── datasets/
│   ├── metrics/
│   └── operations/
├── runner/
│   ├── adapters/
│   ├── evaluators/
│   ├── schemas/
│   └── tests/
└── tmp/
```

## 4. 每个目录的作用

### 4.1 `systems/`：被测记忆系统代码

每个系统使用一个独立子目录：

```text
systems/DesayMem_mem0
systems/mem0
systems/MemoryOS
```

建议每个目录都是独立 Git 仓库，保留其原始提交历史。不要把多个第三方仓库复制到同一个 Git 仓库中。

`systems/adapters/` 用于保存临时适配器；如果后续建立统一评测框架，正式适配器建议迁入 `runner/adapters/`。

当前 DesayMem Benchmark 仓库位置为：

```text
/data/pengshuang/memory-benchmark/systems/DesayMem_mem0
```

### 4.2 `envs/`：各系统独立 Python 环境

不同项目可能依赖不同版本的 Python、Pydantic、PyTorch 或向量数据库客户端，因此不要共用同一个虚拟环境。

当前 DesayMem 环境为：

```text
/data/pengshuang/memory-benchmark/envs/desaymem
```

未来可以分别建立：

```text
/data/pengshuang/memory-benchmark/envs/mem0
/data/pengshuang/memory-benchmark/envs/memoryos
```

虚拟环境可以删除重建，因此不能在其中保存代码、数据集和实验结果。

### 4.3 `datasets/`：只读评测输入

- `raw/`：原始数据，只读保存，不直接修改；
- `processed/`：完成清洗、标准化和字段转换的数据；
- `ground_truth/`：标准答案、相关记忆标注、冲突关系和期望行为；
- `splits/`：训练、验证、测试或不同场景划分；
- `README.md`：数据来源、许可证、字段说明、版本和处理流程。

建议数据集采用统一字段：

```text
case_id
tenant_id
user_id
vehicle_id
occupant_id
session_id
timestamp
scene
messages/events
query
expected_memories
expected_answer
tags
```

测试类别建议至少覆盖：

- 单条事实记忆；
- 多轮会话记忆；
- 时间检索；
- 新旧偏好冲突；
- 模糊指令；
- L1 事实、L2 情景、L3 画像；
- 多用户与多乘员隔离；
- 长期画像更新与遗忘；
- 错误记忆、重复记忆和无关记忆抑制；
- 多模态记忆（后续接入图片、音频和车辆信号）。

### 4.4 `configs/`：可版本化的实验配置

- `systems/`：各记忆系统的公开参数；
- `models/`：LLM、Embedding、Reranker 配置；
- `experiments/`：每次实验的组合配置；
- `secrets.example.env`：环境变量模板，不包含真实密钥。

真实 `.env` 和 API Key 不应提交到 GitHub。

### 4.5 `scripts/`：运维和实验入口

- `setup/`：初始化目录、环境、数据库；
- `start/`：启动各系统；
- `stop/`：停止各系统；
- `health/`：健康检查；
- `benchmark/`：批量写入、检索、评分；
- `database/`：数据库创建、清理和备份。

每个脚本应明确目标系统和数据库，避免使用一个模糊的 `start.sh` 同时操作多个服务。

### 4.6 `data/`：系统运行时数据

该目录保存本地数据库、缓存和系统运行状态，例如：

```text
data/desaymem/history.db
data/mem0/
data/memoryos/
```

当前 DesayMem Benchmark 的 SQLite 文件应为：

```text
/data/pengshuang/memory-benchmark/data/desaymem/history.db
```

`data/` 与 `datasets/` 不同：

- `datasets/` 是测试输入；
- `data/` 是被测系统运行后生成的状态。

### 4.7 `results/`：实验输出

- `raw/`：原始 API 返回、召回列表、模型回答和逐样本时延；
- `metrics/`：聚合指标；
- `comparisons/`：多系统、多模型对比结果；
- `archive/`：已冻结实验。

不要覆盖历史结果。每次运行建立唯一目录，例如：

```text
results/raw/20260907_103000_desaymem_qwen3_32b_v1/
```

### 4.8 `logs/`：日志

- `services/`：API、LLM、Embedding 等长期服务日志；
- `experiments/`：单次 Benchmark 执行日志。

日志用于排查失败，但不应代替结构化结果文件。

### 4.9 `reports/`：对外报告

- `figures/`：图表；
- `tables/`：论文或汇报表格；
- `releases/`：可交付的 Markdown、PDF 或演示材料。

### 4.10 `docs/`：设计与维护文档

建议分别维护架构、系统接入、数据集、指标、部署运维说明，避免所有内容堆在一个 README 中。

### 4.11 `runner/`：统一评测框架

后续最值得重点建设的目录：

- `adapters/`：把不同系统封装为统一 `add/search/reset/health` 接口；
- `evaluators/`：计算召回率、准确率、冲突处理、时延等指标；
- `schemas/`：统一数据集、请求、响应和结果格式；
- `tests/`：评测框架自身的自动化测试。

### 4.12 `tmp/`：临时文件

仅存放可随时删除、能够重新生成的中间文件，不存放唯一数据。

## 5. 一次性创建目录

执行以下命令不会删除现有文件，只会补齐不存在的目录：

```bash
mkdir -p /data/pengshuang/memory-benchmark/systems/adapters
mkdir -p /data/pengshuang/memory-benchmark/envs
mkdir -p /data/pengshuang/memory-benchmark/datasets/raw
mkdir -p /data/pengshuang/memory-benchmark/datasets/processed
mkdir -p /data/pengshuang/memory-benchmark/datasets/ground_truth
mkdir -p /data/pengshuang/memory-benchmark/datasets/splits
mkdir -p /data/pengshuang/memory-benchmark/configs/systems
mkdir -p /data/pengshuang/memory-benchmark/configs/models
mkdir -p /data/pengshuang/memory-benchmark/configs/experiments
mkdir -p /data/pengshuang/memory-benchmark/scripts/setup
mkdir -p /data/pengshuang/memory-benchmark/scripts/start
mkdir -p /data/pengshuang/memory-benchmark/scripts/stop
mkdir -p /data/pengshuang/memory-benchmark/scripts/health
mkdir -p /data/pengshuang/memory-benchmark/scripts/benchmark
mkdir -p /data/pengshuang/memory-benchmark/scripts/database
mkdir -p /data/pengshuang/memory-benchmark/data/desaymem
mkdir -p /data/pengshuang/memory-benchmark/data/mem0
mkdir -p /data/pengshuang/memory-benchmark/data/memoryos
mkdir -p /data/pengshuang/memory-benchmark/results/raw
mkdir -p /data/pengshuang/memory-benchmark/results/metrics
mkdir -p /data/pengshuang/memory-benchmark/results/comparisons
mkdir -p /data/pengshuang/memory-benchmark/results/archive
mkdir -p /data/pengshuang/memory-benchmark/logs/services
mkdir -p /data/pengshuang/memory-benchmark/logs/experiments
mkdir -p /data/pengshuang/memory-benchmark/reports/figures
mkdir -p /data/pengshuang/memory-benchmark/reports/tables
mkdir -p /data/pengshuang/memory-benchmark/reports/releases
mkdir -p /data/pengshuang/memory-benchmark/docs/architecture
mkdir -p /data/pengshuang/memory-benchmark/docs/systems
mkdir -p /data/pengshuang/memory-benchmark/docs/datasets
mkdir -p /data/pengshuang/memory-benchmark/docs/metrics
mkdir -p /data/pengshuang/memory-benchmark/docs/operations
mkdir -p /data/pengshuang/memory-benchmark/runner/adapters
mkdir -p /data/pengshuang/memory-benchmark/runner/evaluators
mkdir -p /data/pengshuang/memory-benchmark/runner/schemas
mkdir -p /data/pengshuang/memory-benchmark/runner/tests
mkdir -p /data/pengshuang/memory-benchmark/tmp
```

检查结构：

```bash
find /data/pengshuang/memory-benchmark -maxdepth 2 -type d | sort
```

## 6. 当前 DesayMem Benchmark 环境

当前规划如下：

| 项目 | 配置 |
|---|---|
| 仓库 | `/data/pengshuang/memory-benchmark/systems/DesayMem_mem0` |
| Python 环境 | `/data/pengshuang/memory-benchmark/envs/desaymem` |
| API 监听 | `0.0.0.0:20144` |
| PostgreSQL 服务 | `127.0.0.1:20143` |
| Benchmark 数据库 | `bench_desaymem` |
| SQLite 历史 | `/data/pengshuang/memory-benchmark/data/desaymem/history.db` |
| LLM 服务 | `127.0.0.1:20140/v1` |
| Embedding 服务 | `127.0.0.1:20141/v1` |
| screen 会话 | `benchmark-desaymem` |

生产/演示后端仍使用：

| 项目 | 配置 |
|---|---|
| 仓库 | `/data/pengshuang/desaymem/apps/DesayMem_mem0` |
| API 端口 | `20142` |
| PostgreSQL 数据库 | `desaymem` |

> Benchmark 的 `.env` 中，`POSTGRES_DSN` 必须以 `/bench_desaymem` 结尾，不能指向 `/desaymem`。

## 7. 克隆和更新被测系统

### 7.1 克隆 DesayMem

```bash
cd /data/pengshuang/memory-benchmark/systems
git clone git@github.com:psile/DesayMem_mem0.git
```

### 7.2 后续更新

```bash
cd /data/pengshuang/memory-benchmark/systems/DesayMem_mem0
git status --short
git pull --ff-only
```

如果 `git status --short` 有输出，先使用 `git diff` 查看本地改动，不要直接执行 `git reset --hard`。

### 7.3 接入其他系统

```bash
cd /data/pengshuang/memory-benchmark/systems
git clone <mem0仓库地址> mem0
git clone <MemoryOS仓库地址> MemoryOS
```

第三方系统尽量保持原仓库不修改。必要的请求格式转换放入统一 Adapter，只有确认属于系统本身的问题时才建立明确的补丁分支。

## 8. Python 环境创建规范

示例：创建 DesayMem 环境。

```bash
python3 -m venv /data/pengshuang/memory-benchmark/envs/desaymem
source /data/pengshuang/memory-benchmark/envs/desaymem/bin/activate
cd /data/pengshuang/memory-benchmark/systems/DesayMem_mem0
python -m pip install -e .
python -m pip check
```

其他系统重复同样方式，但必须使用新的环境目录。

记录环境版本：

```bash
python --version
python -m pip freeze > /data/pengshuang/memory-benchmark/configs/systems/desaymem-requirements-lock.txt
```

## 9. 数据库隔离方案

当前 DesayMem Benchmark 与生产服务可以共用同一个 PostgreSQL 进程和端口 `20143`，但必须使用不同数据库：

```text
生产：desaymem
评测：bench_desaymem
```

未来每个对比系统建议使用独立数据库：

```text
bench_desaymem
bench_mem0
bench_memoryos
```

不要让不同系统共用相同表，也不要依靠 `tenant_id` 代替数据库级隔离。

每次运行前记录：

```text
system_name
database_name
dataset_version
run_id
```

### 9.1 是否可以清空 Benchmark 数据库

可以，但只能清理 Benchmark 数据库，且应由专门脚本完成。推荐流程：

1. 核对数据库名必须以 `bench_` 开头；
2. 备份需要保留的实验；
3. 清理该系统的 Benchmark 数据库及本地运行数据；
4. 重新执行数据库迁移；
5. 再运行下一组实验。

不要清理 `desaymem` 生产数据库。清库脚本必须加入数据库名保护，不接受空变量或通配符。

## 10. 服务和端口规划

| 服务 | 当前端口 | 用途 |
|---|---:|---|
| vLLM LLM | `20140` | 多个 Benchmark 系统共享推理 |
| BGE-M3 Embedding | `20141` | 多个系统共享向量编码 |
| 正式 DesayMem API | `20142` | 生产/演示 |
| PostgreSQL | `20143` | 多数据库实例 |
| Benchmark DesayMem API | `20144` | DesayMem 评测 |
| 其他系统 API | `20145` 起 | Mem0、MemoryOS 等 |

每新增服务前先登记端口，避免不同 screen 会话争用同一端口。

## 11. 统一 Adapter 设计

不同记忆系统的 API 差异很大，评测框架不应直接为每个系统写一套数据集。建议统一为以下能力：

```python
class MemoryAdapter:
    def health(self): ...
    def reset(self, namespace): ...
    def add(self, sample): ...
    def search(self, query): ...
    def get_state(self, identity): ...
    def close(self): ...
```

统一输入身份字段：

```text
tenant_id + user_id + vehicle_id + occupant_id + session_id
```

统一输出至少包含：

```text
memory_id
content
score
memory_type
created_at
updated_at
source
raw_response
latency_ms
```

Adapter 只做协议转换，不应悄悄修改记忆内容或替被测系统完成检索逻辑。

## 12. 指标规划

### 12.1 效果指标

- Recall@K：标准记忆是否进入前 K 条；
- Precision@K：召回记忆中相关内容比例；
- MRR / nDCG：相关记忆排序质量；
- QA Accuracy：最终回答是否正确；
- Temporal Consistency：是否优先使用最新有效偏好；
- Conflict Resolution Accuracy：新旧冲突处理准确率；
- User Isolation Accuracy：是否发生跨用户记忆泄漏；
- Duplicate Rate：重复记忆比例；
- Abstention Accuracy：无相关记忆时是否正确拒绝引用。

### 12.2 分层记忆指标

- L1：事实提取准确率、重复率、更新准确率；
- L2：情景归纳完整性、时间关联、跨轮关联准确率；
- L3：画像/偏好准确率、稳定性、更新和遗忘合理性。

### 12.3 工程指标

- 写入时延；
- 检索时延；
- 首 Token 时延和总响应时延；
- LLM Token 消耗；
- GPU/CPU/内存占用；
- 数据库存储增长；
- 并发吞吐；
- API 错误率。

## 13. 单次实验目录规范

建议运行编号：

```text
YYYYMMDD_HHMMSS_系统_模型_数据集版本
```

例如：

```text
20260907_103000_desaymem_qwen3_32b_vehiclemem_v1
```

一次实验保存：

```text
results/raw/<run_id>/
├── manifest.json
├── config.yaml
├── predictions.jsonl
├── retrievals.jsonl
├── errors.jsonl
├── timing.jsonl
└── summary.json
```

`manifest.json` 至少记录：

- run_id；
- 开始和结束时间；
- 系统名称及 Git commit；
- Adapter 版本；
- 数据集名称、版本及校验值；
- LLM/Embedding/Reranker 名称；
- 数据库名称；
- 参数配置；
- GPU 型号；
- 成功、失败和跳过样本数。

## 14. 公平对比原则

1. 使用相同原始数据、查询和标准答案。
2. 明确哪些系统使用相同 LLM/Embedding，哪些使用原生推荐配置。
3. “统一组件对比”和“系统最佳配置对比”分成两组实验。
4. 每组实验从干净的独立数据库开始。
5. 固定随机种子、温度和最大输出长度。
6. 记录失败，不要自动隐藏或删除异常样本。
7. 预热请求与正式计时分开。
8. 至少重复多轮，报告均值、P50、P95，而不是只报告最好结果。

## 15. 推荐建设阶段

### 第一阶段：跑通 DesayMem 基线

- 固定 Benchmark 数据库；
- 完成 add、search、reset、health；
- 跑通 L1/L2/L3 状态查看；
- 建立一份小规模黄金测试集；
- 输出逐样本结果和基础指标。

### 第二阶段：统一评测框架

- 完成统一 Schema；
- 实现 `DesayMemAdapter`；
- 建立统一 Runner 和 Evaluator；
- 自动生成 manifest 和汇总表。

### 第三阶段：接入 Mem0 和 MemoryOS

- 每个系统建立独立环境、数据库和端口；
- 实现对应 Adapter；
- 先做功能一致性测试，再做规模化对比。

### 第四阶段：模型与多模态实验

- 对比 Qwen3-32B、Qwen3.8-27B 等模型；
- 保持后端接口和数据集尽量不变，只切换模型配置；
- 增加图像、音频、车辆状态输入；
- 评测多模态事实、情景关联和画像形成能力。

### 第五阶段：自动化与报告

- 一条命令完成清理、启动、写入、检索、评分；
- 自动采集资源与时延；
- 自动生成图表、Markdown 和 PDF 报告；
- 建立冻结结果和可复现实验档案。

## 16. Git 仓库规划

建议新建一个独立的评测主仓库，只管理以下内容：

```text
runner/
configs/
scripts/
docs/
datasets/ 中可公开的小型数据或生成脚本
```

不要把以下内容提交到该仓库：

```text
envs/
data/
logs/
tmp/
大模型权重
真实密钥
大型原始数据集
第三方 systems 仓库内容
```

`systems/DesayMem_mem0`、`systems/mem0`、`systems/MemoryOS` 保持独立 Git 仓库。评测主仓库可以在文档中记录它们的 commit，后续成熟时再考虑使用 Git submodule 或锁定清单。

推荐 `.gitignore` 至少包含：

```gitignore
envs/
data/
logs/
tmp/
results/raw/
.env
*.db
*.log
__pycache__/
.pytest_cache/
```

关键汇总指标和最终报告是否提交，可根据文件大小和团队协作方式决定。

## 17. 备份和数据安全

- 原始数据集和 ground truth 必须有版本或校验值；
- Benchmark 数据库清理前先确认数据库名；
- 重要实验结束后冻结配置、结果和 commit；
- `.env` 和 API Key 不进入结果文件；
- 用户数据需要脱敏，尤其是位置、联系人、车辆和身份信息；
- `tmp/` 可以清理，`datasets/raw/` 和冻结结果不可随意删除；
- 生产数据库和 Benchmark 数据库分别备份。

## 18. 当前下一步建议

建议按以下顺序继续：

1. 补齐目录结构；
2. 确认 DesayMem Benchmark 服务 `20144` 可以 add/search；
3. 修复并验证前端 L1/L2/L3 观测接口；
4. 建立第一版统一数据 Schema；
5. 实现 `DesayMemAdapter`；
6. 用 20～50 条黄金样本跑通端到端流程；
7. 再接入 Mem0；
8. 最后接入 MemoryOS 和多模态模型，避免同时排查多个系统。

## 19. 最终目标结构

完成后，整个服务器形成两套清晰体系：

```text
/data/pengshuang/desaymem/
└── 稳定服务：模型、Embedding、PostgreSQL、正式 DesayMem 后端

/data/pengshuang/memory-benchmark/
└── 评测平台：多记忆系统、统一数据集、统一 Runner、结果与报告
```

这样后续替换 Qwen 模型、接入新的记忆库或增加多模态数据时，只需要新增系统适配器和实验配置，不需要重新破坏已有的部署环境。
