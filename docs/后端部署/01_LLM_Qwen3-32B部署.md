# Qwen3-32B 部署与维护

## 1. 当前配置

- GPU：物理 4 号 H20，约 96 GB 显存
- 模型：`/data/pengshuang/desaymem/models/Qwen3-32B`
- vLLM：0.28.0
- PyTorch：2.13.0+cu130
- 模型别名：`memory-llm`
- 服务端口：20140
- 上下文窗口：16384 tokens
- 最大并发序列：3
- 后端最大输出：2000 tokens
- 思考模式：调用方通过 `chat_template_kwargs.enable_thinking=false` 关闭

16K 的原因：原 8K 配置下，记忆提取提示词约 6193 tokens，再请求 2000 输出会达到至少 8193，超过 8192。16K 可保留约 8K 的用户消息和历史空间。

## 2. 启动

```bash
export PATH=/data/pengshuang/desaymem/envs/screen/bin:$PATH
screen -S llm

source /data/pengshuang/desaymem/envs/llm/bin/activate
read -rsp "请输入模型 API Key：" VLLM_API_KEY
echo
export VLLM_API_KEY

CUDA_VISIBLE_DEVICES=4 vllm serve \
  /data/pengshuang/desaymem/models/Qwen3-32B \
  --served-model-name memory-llm \
  --host 0.0.0.0 \
  --port 20140 \
  --api-key "$VLLM_API_KEY" \
  --dtype bfloat16 \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.84 \
  --max-model-len 16384 \
  --max-num-seqs 3
```

看到 `Application startup complete` 后，按 `Ctrl+A`，再按 `D`。

## 3. 验证

```bash
read -rsp "请输入模型 API Key：" VLLM_API_KEY
echo
export VLLM_API_KEY

python - <<'PY'
import json, os, urllib.request
req = urllib.request.Request(
    "http://127.0.0.1:20140/v1/models",
    headers={"Authorization": f"Bearer {os.environ['VLLM_API_KEY']}"},
)
with urllib.request.urlopen(req, timeout=30) as r:
    data = json.load(r)
print(json.dumps(data, ensure_ascii=False, indent=2))
PY
```

应看到 `memory-llm` 和 `max_model_len: 16384`。

## 4. 参数解释与容量

| 参数 | 当前值 | 说明 |
|---|---:|---|
| `gpu-memory-utilization` | 0.84 | vLLM 可使用的显存比例 |
| `max-model-len` | 16384 | 输入与输出合计上限 |
| `max-num-seqs` | 3 | 同时调度序列上限，不等于固定 QPS |
| `tensor-parallel-size` | 1 | 单卡部署 |
| `dtype` | bfloat16 | H20 适用 |

已观测到模型权重约 61 GiB。8K 时 KV cache 约 15.59 GiB、容量约 63372 tokens；切换到 16K 后，满上下文理论并发约 3，因此选择 `max-num-seqs=3`。实际吞吐仍与输入长度、输出长度和请求混合有关。

## 5. 关闭思考模式

vLLM 服务本身不强制关闭，而由请求传入：

```json
"chat_template_kwargs": {
  "enable_thinking": false
}
```

后端 OpenAI SDK 使用：

```python
"extra_body": {
    "chat_template_kwargs": {"enable_thinking": False}
}
```

关闭思考适合结构化记忆提取、重排和画像任务，减少延迟、token 消耗和 JSON 被思考文本污染的风险。它不会关闭模型的基础推理能力。

## 6. 调参原则

- 16K + 3 并发是当前单 H20 的平衡配置。
- 若 OOM，先将显存比例降到 `0.82`，再考虑降低并发。
- 不建议直接升 32K：满窗口理论并发会接近 1，影响交互吞吐。
- 用户超长输入应在应用层截断、分块或摘要，不能只依赖扩大模型窗口。
- `LLM_MAX_TOKENS=2000` 是输出上限，不是数据库保存长度。

## 7.切换QWEN3.8
```python
source /data/pengshuang/desaymem/envs/llm/bin/activate

read -rsp "请输入 vLLM API Key: " VLLM_API_KEY
echo
export VLLM_API_KEY

CUDA_VISIBLE_DEVICES=4 vllm serve \
  /data/pengshuang/desaymem/models/Qwen3.8-27B \
  --served-model-name memory-llm \
  --host 0.0.0.0 \
  --port 20140 \
  --api-key "$VLLM_API_KEY" \
  --dtype bfloat16 \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.80 \
  --max-model-len 16384 \
  --max-num-seqs 2 \
  --reasoning-parser qwen3
```

