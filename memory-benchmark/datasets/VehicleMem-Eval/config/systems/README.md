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
