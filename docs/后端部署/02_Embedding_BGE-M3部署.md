# BGE-M3 Embedding 部署

## 1. 当前配置

- 模型：`/data/pengshuang/desaymem/models/bge-m3`
- 环境：`/data/pengshuang/desaymem/envs/embedding`
- 服务代码：`/data/pengshuang/desaymem/apps/embedding/server.py`
- 端口：20141
- 模型别名：`bge-m3`
- 输出维度：1024
- 单文本最大长度：1024 tokens
- 单请求最大文本数：32
- 内部 batch size：4
- FP16、GPU4、单 worker，并使用锁串行执行 GPU encode

“最大输入 32”指一次 HTTP 请求最多 32 段文本，不是每段只能有 32 tokens，因此不会直接损害单条记忆质量。

## 2. 启动

```bash
export PATH=/data/pengshuang/desaymem/envs/screen/bin:$PATH
screen -S embedding

source /data/pengshuang/desaymem/envs/embedding/bin/activate
read -rsp "请输入 Embedding API Key：" EMBEDDING_API_KEY
echo
export EMBEDDING_API_KEY

CUDA_VISIBLE_DEVICES=4 uvicorn server:app \
  --app-dir /data/pengshuang/desaymem/apps/embedding \
  --host 0.0.0.0 \
  --port 20141 \
  --workers 1
```

如果实际 `server.py` 内使用不同的环境变量名，以代码读取的名称为准。成功后 `Ctrl+A`、`D`。

## 3. 本机验证

```bash
python - <<'PY'
import json, os, urllib.request
body = json.dumps({
    "model": "bge-m3",
    "input": ["用户喜欢26度空调", "用户偏好温暖的车内环境"],
    "encoding_format": "float"
}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:20141/v1/embeddings",
    data=body,
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.environ['EMBEDDING_API_KEY']}"
    },
)
with urllib.request.urlopen(req, timeout=120) as r:
    result = json.load(r)
print("向量数量:", len(result["data"]))
print("向量维度:", len(result["data"][0]["embedding"]))
PY
```

预期维度为 1024。

## 4. 完整性检查

模型目录应包含配置、分词器和权重。当前曾确认 `pytorch_model.bin` 大约 2165.93 MiB，且本地推理输出 `(3, 1024)`。若模型加载成功并能生成 1024 维向量，一般不需要因为同时存在 ONNX 或其他格式文件而重新下载。

## 5. 质量和性能建议

- 写入和查询必须使用同一个 embedding 模型和同一维度。
- 更换模型或维度后，旧向量不可直接混用，需要重新向量化。
- 短记忆不需要把 1024 tokens 全部用满。
- 长对话应先由 LLM 提取为自包含记忆，再进行 embedding。
- 后端批量 HTTP 分块设为 32、服务内部 batch size 设为 4，是接口容量与 GPU 推理容量的两级控制。

