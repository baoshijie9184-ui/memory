# DesayMem Benchmark 后端更新与重启操作手册

本文用于维护服务器上的 **Benchmark 专用 DesayMem 后端**。适用于 GitHub 代码更新后拉取、迁移数据库、重启服务，以及从服务器和本地 Windows 验证接口。

## 1. 当前部署信息

| 项目 | Benchmark 环境 | 生产环境（不要混用） |
|---|---|---|
| 代码目录 | `/data/pengshuang/memory-benchmark/systems/DesayMem_mem0` | `/data/pengshuang/desaymem/apps/DesayMem_mem0` |
| Python 环境 | `/data/pengshuang/memory-benchmark/envs/desaymem` | `/data/pengshuang/desaymem/envs/backend` |
| API 端口 | `20144` | `20142` |
| PostgreSQL 地址 | `127.0.0.1:20143` | `127.0.0.1:20143` |
| PostgreSQL 数据库 | `bench_desaymem` | `desaymem` |
| SQLite 会话历史 | `/data/pengshuang/memory-benchmark/data/desaymem/history.db` | `/data/pengshuang/desaymem/data/history.db` |
| screen 会话 | `benchmark-desaymem` | `memory-backend` |

> **最重要的安全检查：** Benchmark 的 `POSTGRES_DSN` 必须以 `/bench_desaymem` 结尾。不要对生产数据库 `desaymem` 执行 Benchmark 迁移或测试。

## 2. 每次更新前先做检查

登录服务器后执行：

```bash
cd /data/pengshuang/memory-benchmark/systems/DesayMem_mem0
git status --short
git branch --show-current
git log -1 --oneline
```

- `git status --short` 没有输出：可以继续拉取。
- 如果有代码改动：先执行 `git diff` 检查，不要直接覆盖，也不要执行 `git reset --hard`。
- `.env` 一般不应提交到 Git；必须保留服务器自己的配置和密钥。

## 3. 快速更新：仅普通 Python 代码变化

如果确认此次更新没有修改依赖、数据库迁移文件或环境配置，可采用快速流程。

### 3.1 拉取代码

```bash
cd /data/pengshuang/memory-benchmark/systems/DesayMem_mem0
git pull --ff-only
git log -1 --oneline
```

### 3.2 重启服务

先让 `screen` 命令可用：

```bash
export PATH=/data/pengshuang/desaymem/envs/screen/bin:$PATH
screen -ls
screen -d -r benchmark-desaymem
```

进入旧服务窗口后：

1. 按 `Ctrl+C` 停止 Uvicorn。
2. 在同一个 screen 窗口中执行：

```bash
cd /data/pengshuang/memory-benchmark/systems/DesayMem_mem0
source /data/pengshuang/memory-benchmark/envs/desaymem/bin/activate
set -a
source .env
set +a
uvicorn desaymem.api.main:app --host 0.0.0.0 --port 20144 --workers 1
```

看到 `Uvicorn running on http://0.0.0.0:20144` 后，按 `Ctrl+A`，再按 `D`，退出并保持服务后台运行。

## 4. 完整更新：推荐的稳妥流程

如果不确定更新包含什么，或者更新涉及 `pyproject.toml`、依赖、`migrations/`、数据库模型，请执行完整流程。

### 4.1 拉取并更新安装

```bash
cd /data/pengshuang/memory-benchmark/systems/DesayMem_mem0
git status --short
git pull --ff-only
source /data/pengshuang/memory-benchmark/envs/desaymem/bin/activate
python -m pip install -e .
python -m pip check
```

`pip install -e .` 是可编辑安装。以后普通 Python 源码变化通常能直接生效，但仍必须重启正在运行的 Uvicorn 进程。

### 4.2 加载环境并核对数据库

```bash
set -a
source .env
set +a
printf '%s\n' "$POSTGRES_DSN"
printf '%s\n' "$HISTORY_DB_PATH"
```

预期应分别指向：

```text
postgresql://desaymem@127.0.0.1:20143/bench_desaymem
/data/pengshuang/memory-benchmark/data/desaymem/history.db
```

如果数据库名不是 `bench_desaymem`，立即停止，不要执行迁移。

### 4.3 执行数据库迁移

```bash
desaymem-migrate apply
desaymem-migrate check
```

- `apply` 会应用尚未执行的迁移；已执行过的迁移不应重复创建表。
- `check` 必须通过后再启动 API。
- 不要删除数据库、迁移记录或 `history.db` 来规避迁移错误。

### 4.4 重启 Uvicorn

```bash
export PATH=/data/pengshuang/desaymem/envs/screen/bin:$PATH
screen -ls
screen -d -r benchmark-desaymem
```

进入窗口后按 `Ctrl+C`，再执行：

```bash
cd /data/pengshuang/memory-benchmark/systems/DesayMem_mem0
source /data/pengshuang/memory-benchmark/envs/desaymem/bin/activate
set -a
source .env
set +a
uvicorn desaymem.api.main:app --host 0.0.0.0 --port 20144 --workers 1
```

最后按 `Ctrl+A`、`D` 分离窗口。

如果旧的 screen 会话已经不存在，可新建：

```bash
export PATH=/data/pengshuang/desaymem/envs/screen/bin:$PATH
screen -S benchmark-desaymem
```

然后在新窗口中执行上面的启动命令。

## 5. 在服务器上验证服务

服务器缺少 `curl` 和 `ss` 时，可以直接使用 Python 标准库。

### 5.1 健康检查

```bash
python - <<'PY'
from urllib.request import urlopen

url = "http://127.0.0.1:20144/health"
with urlopen(url, timeout=10) as response:
    print("HTTP状态：", response.status)
    print("接口返回：", response.read().decode("utf-8"))
PY
```

预期结果是 HTTP `200`，并包含数据库、LLM 和 Embedding 相关状态。

### 5.2 查看运行日志

```bash
export PATH=/data/pengshuang/desaymem/envs/screen/bin:$PATH
screen -d -r benchmark-desaymem
```

观察完毕后按 `Ctrl+A`、`D`，不要按 `Ctrl+C`，否则会停止服务。

## 6. 从本地 Windows 验证

假设服务器地址为 `10.133.72.161`：

```powershell
Test-NetConnection 10.133.72.161 -Port 20144
Invoke-RestMethod http://10.133.72.161:20144/health
```

若 TCP 成功但 HTTP 失败，重点检查：

- Uvicorn 是否使用 `--host 0.0.0.0`，而不是 `127.0.0.1`。
- screen 内的服务是否仍在运行。
- 服务器防火墙、云安全组或网络代理是否允许 `20144`。
- 本地访问的是 Benchmark 端口 `20144`，而不是生产端口 `20142`。

前端的默认记忆后端地址应配置为：

```text
http://10.133.72.161:20144
```

具体是否需要附加 `/memory`，应以当前前端的代理设计为准；不要同时在前端 base URL 和接口路径中重复添加。

## 7. 更新后的最小验收清单

每次更新至少确认：

- [ ] `git pull --ff-only` 成功。
- [ ] 当前提交号符合预期。
- [ ] `.env` 仍指向 `bench_desaymem`。
- [ ] `HISTORY_DB_PATH` 是绝对路径。
- [ ] `desaymem-migrate check` 通过。
- [ ] screen 中只有预期的 Benchmark 服务在 `20144` 运行。
- [ ] 服务器本机 `/health` 返回 200。
- [ ] Windows 本地 `/health` 可访问。
- [ ] 至少执行一次记忆写入和搜索测试。
- [ ] 前端能读取 L1、L2、L3 状态接口。
- [ ] 生产后端 `20142` 未受影响。

## 8. 常见问题

### 8.1 拉取后代码没有生效

最常见原因是旧 Uvicorn 进程仍在运行。拉取代码不会自动重启已加载的 Python 进程，必须停止并重新启动 Benchmark screen 中的服务。

### 8.2 `Address already in use`

说明 `20144` 已被旧进程占用。先进入原 screen 会话并按 `Ctrl+C`，不要直接重复启动第二个实例。

### 8.3 `Could not import module`

正确入口是：

```text
desaymem.api.main:app
```

并确保已经进入仓库目录、激活 Benchmark 虚拟环境、执行过 `pip install -e .`。

### 8.4 迁移报 SQL 语法错误

先确认已拉取最新修复，再检查报错对应的 `migrations/*.sql`。SQL 文件里的说明文字必须以 `--` 注释；不要通过删除整个数据库来解决单个迁移文件错误。

### 8.5 L1/L2/L3 页面为空

先区分“数据库没有数据”和“前端没有取到数据”：

1. 确认写入请求使用的 `tenant_id`、`user_id`、`vehicle_id`、`occupant_id` 与查询完全一致。
2. 查看写入接口是否返回 `201 Created` 和 `extracted > 0`。
3. 查看服务日志中 LLM、Embedding、Profile distillation 是否报错。
4. 直接调用后端 memory-layers/observability 接口，确认后端有数据后再排查前端。
5. L2/L3 并不保证每次对话都会立即产生；它们取决于情景归纳、画像提炼及相关阈值。

### 8.6 Git 有本地改动，无法拉取

执行：

```bash
git status --short
git diff
```

先确认改动归属。需要保留的改动应提交到单独分支或制作补丁；不要随意执行 `git reset --hard`、覆盖 `.env` 或删除数据文件。

## 9. 哪些改动需要做什么

| 改动类型 | 拉取代码 | `pip install -e .` | 数据库迁移 | 重启 API |
|---|---:|---:|---:|---:|
| 普通 `.py` 源码 | 是 | 通常不需要 | 不需要 | 必须 |
| `pyproject.toml` / 依赖 | 是 | 必须 | 视情况 | 必须 |
| `migrations/*.sql` | 是 | 建议 | 必须 | 必须 |
| `.env` 配置 | 不一定 | 不需要 | 视配置 | 必须 |
| 仅前端代码 | 后端不需要 | 不需要 | 不需要 | 后端不需要 |

## 10. 日常最短操作版

确定只是普通后端代码更新时，只需要：

```bash
cd /data/pengshuang/memory-benchmark/systems/DesayMem_mem0
git status --short
git pull --ff-only
export PATH=/data/pengshuang/desaymem/envs/screen/bin:$PATH
screen -d -r benchmark-desaymem
```

进入 screen 后按 `Ctrl+C`，重新执行：

```bash
source /data/pengshuang/memory-benchmark/envs/desaymem/bin/activate
set -a
source .env
set +a
uvicorn desaymem.api.main:app --host 0.0.0.0 --port 20144 --workers 1
```

启动成功后按 `Ctrl+A`、`D`。随后分别从服务器和本地访问 `/health` 验证。

## 11. 安全与维护建议

- 不要把 `.env`、API Key、数据库密码提交到 GitHub。
- 如果密钥曾出现在截图、日志或公开仓库中，应立即轮换。
- Benchmark 和生产服务必须使用不同的 API 端口、数据库名和 SQLite 文件。
- 更新前记录当前提交号；出现问题时优先修复或回滚 Git 提交，不要重置数据库。
- 重要测试前备份 `bench_desaymem` 和 Benchmark 的 `history.db`。
- 长期运行建议后续改用 `systemd` 管理服务；它比 screen 更适合自动重启、开机启动和集中日志。
