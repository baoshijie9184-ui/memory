# per-system 环境覆盖目录

每个记忆系统一个 `{系统名}.env`，run_full.sh 运行前自动 source（不存在则跳过）。
用于各系统差异化的配置（API 地址/模型名/库路径等），避免堆在主脚本里。

例：structmem.env
```
LIGHTMEM_API_BASE=http://127.0.0.1:20140/v1
LIGHTMEM_API_KEY=boluoboluomi
LIGHTMEM_LLM_MODEL=memory-llm
```
评测端模型统一走 ../models.yaml（--model-config），此处只放建库/系统特有配置。

Mem0 使用独立的 `envs/mem0`。它按照官方
`/data/pengshuang/memory-benchmark/benchmark/memory-benchmarks/docker/mem0/requirements.txt` 创建，
把官方 Docker 镜像中的 Python 依赖转换成本项目可直接调用的虚拟环境。
如果项目内没有 `benchmark` 目录，脚本会先把
`https://github.com/mem0ai/memory-benchmarks.git` 克隆到这里：

```text
/data/pengshuang/memory-benchmark/benchmark/memory-benchmarks
```

```bash
cd /data/pengshuang/memory-benchmark
bash scripts/setup_mem0_env.sh
```

官方 Docker requirements 仍引用已经删除的 `feat/v3-pipeline` 分支。安装脚本
会保留其中其他依赖，并从官方 `mem0ai/mem0` 仓库的稳定标签 `v2.0.20`
安装 Mem0。可通过 `MEM0_GIT_REF` 显式覆盖版本。

`mem0.env` 会让 `run_full.sh` 自动使用该环境。评测仍通过 `Mem0Bridge`
进程内调用 `Memory`，Qdrant 使用本地目录模式，不需要启动 Docker。
