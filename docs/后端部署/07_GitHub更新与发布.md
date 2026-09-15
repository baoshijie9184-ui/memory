# GitHub 更新与发布

## 1. 日常最简流程

普通 Python 业务代码更新时：

```bash
cd /data/pengshuang/desaymem/apps/DesayMem_mem0
git status --short
git pull --ff-only origin main
```

然后进入后端：

```bash
export PATH=/data/pengshuang/desaymem/envs/screen/bin:$PATH
screen -d -r memory-backend
```

`Ctrl+C` 后重启：

```bash
source /data/pengshuang/desaymem/envs/backend/bin/activate
uvicorn desaymem.api.main:app \
  --host 0.0.0.0 --port 20142 --workers 1
```

普通更新就是：**git pull → 重启记忆后端**。Qwen、Embedding、PostgreSQL 通常无需重启。

## 2. 什么时候需要额外操作

| GitHub 变化 | 附加操作 |
|---|---|
| 仅 `.py` 代码 | 重启后端 |
| `pyproject.toml`/依赖文件 | 重新 `pip install -e .` |
| 新增 `migrations/*.sql` | `desaymem-migrate apply` |
| `.env.example` 新增变量 | 手工补入 `.env` |
| LLM provider 参数改变 | 可能重启后端，不一定重启 vLLM |
| Qwen 启动参数/模型改变 | 重启 `llm` |
| Embedding server/模型改变 | 重启 `embedding`，必要时重建向量 |

## 3. 标准更新流程

```bash
cd /data/pengshuang/desaymem/apps/DesayMem_mem0
git fetch origin
git log --oneline HEAD..origin/main
git diff --stat HEAD..origin/main
git pull --ff-only origin main

source /data/pengshuang/desaymem/envs/backend/bin/activate
python -m compileall -q src
desaymem-migrate apply
desaymem-migrate check
```

依赖变化时：

```bash
python -m pip install \
  -i https://pypi.tuna.tsinghua.edu.cn/simple \
  -e .
python -m pip check
```

不要随意执行全局 `pip install -U`，避免升级破坏已经验证的版本组合。

## 4. 本地修改冲突

如果 `git status --short` 有输出，先看：

```bash
git diff
```

不要使用 `git reset --hard`。最佳做法是在开发电脑将“关闭思考、中文保持、Embedding batch”等修改提交并推到 GitHub，使服务器始终保持干净工作区。

## 5. 回滚

更新前记录：

```bash
git rev-parse HEAD
```

如果新版本异常，优先通过 GitHub 修复并再次拉取。紧急情况下，可切到已知提交后重启：

```bash
git switch --detach <已知正常的完整提交SHA>
```

恢复主线：

```bash
git switch main
git pull --ff-only origin main
```

数据库迁移回滚不能简单依赖 Git 回滚；涉及 schema 变化时必须按迁移说明处理并提前备份。

