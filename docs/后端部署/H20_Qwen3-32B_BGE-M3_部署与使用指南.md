# 单张 H20 部署 Qwen3-32B 与 BGE-M3：部署、调用与运维指南

版本：v1.0 ｜ 整理日期：2026-09-04

本文依据本次实际部署过程整理，面向车载记忆系统开发、联调和后续维护。对话中曾称“qwen3.8”，实际下载、加载和调用的模型是 **Qwen/Qwen3-32B**，服务别名为 **memory-llm**。本文不包含真实 API Key，所有凭据由维护者自行填写。

## 1. 当前部署结果与范围

已验证 Qwen3-32B 可以在 GPU 4 上运行，并通过办公电脑访问模型列表和执行对话。BGE-M3 已完成本地加载测试，输出 `(3, 1024)`；用户随后确认接口部署可用。本文复录部署方案，没有重新连接服务器或独立进行压力测试。

| 项目 | 当前配置 |
|---|---|
| 服务器 IP | 10.133.72.161 |
| SSH 端口 | 2214，仅用于登录 |
| 分配的应用端口范围 | 20140–20149 |
| LLM 端口 | 20140 |
| Embedding 端口 | 20141 |
| 记忆后端建议端口 | 20142，尚不代表已迁移或启动 |
| GPU | 物理 GPU 4，NVIDIA H20 |
| 工作根目录 | /data/pengshuang/desaymem |
| 后台会话工具 | GNU screen |
| 访问协议 | 当前内网 HTTP + Bearer API Key |

SSH 2214 与模型接口端口是不同用途。不能把 SSH 端口填成模型 URL。最早使用的 8001/8002 已被本次对外部署的 20140/20141 替代。容器如有宿主机映射，以管理员实际配置为准；监听端口本身不会自动建立映射或放行防火墙。

## 2. 系统职责与调用关系

| 组件 | 做什么 | 不负责什么 |
|---|---|---|
| Qwen3-32B | 对话、记忆事实提取、总结、冲突判断等文本生成 | 不自动持久保存用户记忆 |
| BGE-M3 | 将文本转换为 1024 维归一化稠密向量 | 不生成对话回复，不直接保存向量 |
| 记忆后端 | 用户隔离、写入、检索、时间状态管理、调用模型 | 不能仅凭更换 URL 自动兼容所有旧配置 |
| 数据库 | 保存记忆文本、元数据、向量与历史 | 不替代模型推理 |

调用一次“请记住我的空调偏好”到 LLM，只是一次生成请求。只有记忆后端显式执行写入流程，偏好才会落库。BGE-M3 此处仅使用稠密向量能力，不提供其稀疏向量或多向量检索接口。

## 3. 硬件、环境与显存

服务器整机规格为 8 张 H20、2TB 系统内存、35TB 磁盘；本方案仅使用其中一张 GPU。不要将整机可见 GPU 等同于个人获授权资源。

本次截图记录：

- 基础环境 Python 3.11.11、Conda 位于 `/opt/conda`。
- 原基础 PyTorch 为 2.6.0+cu124；独立 LLM 环境安装后显示 PyTorch 2.13.0+cu130、vLLM 0.28.0。
- NVIDIA 驱动显示 590.48.01，`nvidia-smi` 的 CUDA 13.1 表示驱动支持能力，不等于当前 Python 包使用的 CUDA 版本。
- LLM 日志：模型加载占用 61.03GiB，KV Cache 约 15.59GiB。
- 后续 GPU 4 截图为 80881 / 97871MiB，剩余 16990MiB，约 16.6GiB。

上述数字是当时记录，不是每次启动固定值。vLLM 预分配 KV Cache，因此没有请求时显存仍然占用，属于正常现象。BGE-M3 使用 FP16、batch_size=4、单文本最多 1024 tokens，初期预计预留 3–5GiB，最终以两服务同时运行时峰值为准。

LLM 初始参数：BF16、8192 上下文、4 个并发序列、显存比例 0.84。8192 是单请求输入和输出总预算，不是任意长度的输入再额外生成 8192。4 个序列也不等于已验证能满足 4 个用户的延迟目标。

GPU 4 早期截图存在 Volatile Uncorr. ECC=1。管理员应确认硬件状态；不要自行重置共享 GPU。当前能推理不代表该错误记录已处理。

## 4. 目录规范

所有模型、环境、程序、缓存和日志放在个人 `/data` 目录。

| 路径（相对工作根目录） | 用途 |
|---|---|
| models/Qwen3-32B | LLM 模型 |
| models/bge-m3 | Embedding 模型 |
| envs/llm | vLLM、ModelScope、OpenAI SDK |
| envs/embedding | sentence-transformers、FastAPI、Uvicorn |
| envs/screen | screen 的 Conda 环境 |
| apps/embedding/server.py | Embedding HTTP 服务 |
| cache/pip、cache/tmp | 包下载及临时文件 |
| cache/modelscope、cache/huggingface、cache/vllm | 模型下载和编译缓存 |
| logs | 服务日志及版本清单 |

```bash
mkdir -p /data/pengshuang/desaymem/{models,envs,apps/embedding,logs,data}
mkdir -p /data/pengshuang/desaymem/cache/{pip,tmp,conda,modelscope,huggingface,vllm}
```

## 5. 网络与依赖安装

### 5.1 网络原则

公司文档列出的 PyPI 清华源、ModelScope 等域名已申请放行。先直接使用获准站点，访问失败后再按公司流程配置代理。不是必须先有代理才能下载。

本次已经实测清华源可以下载；ModelScope 权重下载访问过 `cdn-lfs-cn-1.modelscope.cn`，期间出现读超时并自动重试。主页可达不能保证所有文件 CDN 均可达。遇到持续失败，记录实际目标域名交管理员核查。

`curl: command not found`、`ping: command not found` 是命令缺失，不能据此判定网络不通。可用 pip 实测：

```bash
python -m pip download \
  -i https://pypi.tuna.tsinghua.edu.cn/simple \
  --no-deps -d /data/pengshuang/desaymem/cache/network-check \
  --timeout 15 --retries 0 packaging
```

### 5.2 新环境安装（已安装的服务器无需重复执行）

```bash
/opt/conda/bin/python -m venv /data/pengshuang/desaymem/envs/llm
source /data/pengshuang/desaymem/envs/llm/bin/activate
export PIP_CACHE_DIR=/data/pengshuang/desaymem/cache/pip
export TMPDIR=/data/pengshuang/desaymem/cache/tmp
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
  --only-binary=:all: vllm modelscope
python -m pip check
python -c "import torch, vllm; print(torch.__version__, vllm.__version__, torch.cuda.is_available())"
```

```bash
/opt/conda/bin/python -m venv /data/pengshuang/desaymem/envs/embedding
source /data/pengshuang/desaymem/envs/embedding/bin/activate
export PIP_CACHE_DIR=/data/pengshuang/desaymem/cache/pip
export TMPDIR=/data/pengshuang/desaymem/cache/tmp
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
  --only-binary=:all: sentence-transformers fastapi uvicorn
python -m pip check
python -c "from sentence_transformers import SentenceTransformer; import torch; print(torch.cuda.is_available())"
```

这组未固定版本的命令用于复述本次安装流程；未来安装可能获得不同版本。稳定运行后记录实际依赖，复建时优先使用版本清单和相同驱动条件，不要随意升级运行中的环境。

```bash
/data/pengshuang/desaymem/envs/llm/bin/python -m pip freeze \
  > /data/pengshuang/desaymem/logs/llm-requirements.txt
/data/pengshuang/desaymem/envs/embedding/bin/python -m pip freeze \
  > /data/pengshuang/desaymem/logs/embedding-requirements.txt
```

requirements 清单不是离线安装包；离线重建还需要匹配 Python、平台与 CUDA 依赖的 wheel 文件。

## 6. screen 安装和使用

新建 Conda 环境必须使用 `conda create`，不能对不存在的环境使用 `conda install`。

```bash
CONDA_PKGS_DIRS=/data/pengshuang/desaymem/cache/conda \
/opt/conda/bin/conda create -y \
  --prefix /data/pengshuang/desaymem/envs/screen \
  --override-channels \
  -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge screen
export PATH="/data/pengshuang/desaymem/envs/screen/bin:$PATH"
screen --version
```

| 操作 | 命令或按键 |
|---|---|
| 创建 LLM 会话 | `screen -S llm` |
| 创建 Embedding 会话 | `screen -S embedding` |
| 查看会话 | `screen -ls` |
| 返回 LLM | `screen -r llm` |
| 返回 Embedding | `screen -r embedding` |
| 分离并继续后台运行 | Ctrl+A，松开，再按 D |
| 停止当前前台程序 | Ctrl+C |
| 程序结束后退出会话 | `exit` |
| 恢复被 Ctrl+S 暂停的显示 | Ctrl+Q |

重新登录后找不到 screen，重新 export PATH，或使用完整二进制路径。会话处于 Attached 时先确认是否有人正在操作；必要时 `screen -d -r llm` 会分离旧连接并接入。多个同名会话用 `screen -ls` 中的完整 ID 指定。

screen 保护 SSH 断开时的进程，不负责进程崩溃自动重启，也不能跨容器或宿主机重启保活。不要用全局 kill 命令清理共享服务器进程。

## 7. 模型下载与完整性验证

在下载 screen 会话里执行。已有模型不必重复下载，重新调用通常复用已完成文件；不要两个进程写同一目录。

```bash
source /data/pengshuang/desaymem/envs/llm/bin/activate
export MODELSCOPE_CACHE=/data/pengshuang/desaymem/cache/modelscope
python - <<'PY'
from modelscope import snapshot_download
for model_id, local_dir in [
    ('Qwen/Qwen3-32B', '/data/pengshuang/desaymem/models/Qwen3-32B'),
    ('BAAI/bge-m3', '/data/pengshuang/desaymem/models/bge-m3'),
]:
    path = snapshot_download(model_id=model_id, local_dir=local_dir)
    print('模型下载完成：', path, flush=True)
PY
```

Qwen 权重约 65GB；本次 BGE 的 pytorch_model.bin 显示 2165.93MiB。下载仓库可能包含 ONNX 等额外格式，仓库下载体积不等于加载权重体积。配置文件显示 0.00MiB 通常只是两位小数舍入；`.bin` 存在时不要求同时存在 `.safetensors`。

进度条显示杂乱时，先 Ctrl+Q、Enter，确认是否已返回提示符，再分离并重连 screen。出现 `Read timed out, will retry` 且字节仍增长时可以继续等待。仅文件存在不能证明完整，应以离线加载及推理为验证。

```bash
source /data/pengshuang/desaymem/envs/embedding/bin/activate
CUDA_VISIBLE_DEVICES=4 HF_HUB_OFFLINE=1 python - <<'PY'
from sentence_transformers import SentenceTransformer
model = SentenceTransformer(
    '/data/pengshuang/desaymem/models/bge-m3',
    device='cuda:0', local_files_only=True,
    model_kwargs={'torch_dtype': 'float16'},
)
model.max_seq_length = 1024
vectors = model.encode(
    ['用户开车时喜欢把空调设为26度',
     '用户偏好的车内空调温度是26摄氏度', '用户喜欢吃川菜'],
    batch_size=3, normalize_embeddings=True, convert_to_numpy=True,
)
print('向量形状：', vectors.shape)
print('相近句子：', float(vectors[0] @ vectors[1]))
print('不同主题：', float(vectors[0] @ vectors[2]))
PY
```

本次结果为 `(3, 1024)`，两个相似度约 0.9131、0.5454。这是单次功能测试，不是检索精度基准，也不能据此把 0.5454 定义为通用阈值。

## 8. 启动 Qwen3-32B 服务

先创建 `screen -S llm`；如果已有服务，要回到其会话并 Ctrl+C 停止旧实例，再启动，避免重复占显存和端口。

在 screen 中执行：

```bash
source /data/pengshuang/desaymem/envs/llm/bin/activate
export HF_HOME=/data/pengshuang/desaymem/cache/huggingface
export VLLM_CACHE_ROOT=/data/pengshuang/desaymem/cache/vllm
export HF_HUB_OFFLINE=1
read -rsp '输入 LLM API Key：' VLLM_API_KEY
printf '\n'
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
  --max-model-len 8192 \
  --max-num-seqs 4
```

| 参数 | 含义 |
|---|---|
| CUDA_VISIBLE_DEVICES=4 | 当前进程仅使用物理 GPU 4；进程内编号为 cuda:0 |
| served-model-name | 客户端填写的别名 memory-llm |
| host 0.0.0.0 | 监听容器所有 IPv4 接口；0.0.0.0 不是客户端访问地址 |
| port 20140 | HTTP 服务监听端口 |
| api-key | 接口鉴权，不是第三方云模型付费凭据 |
| tensor-parallel-size 1 | 单卡推理 |
| gpu-memory-utilization 0.84 | vLLM 显存预算比例，并非硬件隔离 |
| max-model-len 8192 | 输入加输出上下文上限 |
| max-num-seqs 4 | 调度中的最大并发序列数 |

首次会加载 17 个权重分片并编译。看到 `Application startup complete` 才进入接口验证；加载完成日志本身不等于 API 就绪。不要按日志建议填满全部剩余显存，因为 BGE-M3 还要共享该卡。

输入的 Key 在当前进程环境中使用。重启或新会话要重新设置；本指南不提供密钥持久化文件。长期运维可由团队配置受控的凭据管理。

## 9. BGE-M3 HTTP 服务完整代码

本实现提供基础 OpenAI Embeddings 兼容接口，接受字符串或字符串列表；不是完整 OpenAI API 实现。限制为每请求 1–32 条、每条最多 20000 字符且不超过 1024 tokens（包含特殊 token），只支持 float 输出和 1024 维。超长输入返回 400，不静默截断。

在服务器执行下面完整代码块；若已有 server.py 且做过修改，请先备份后再替换。

```bash
mkdir -p /data/pengshuang/desaymem/apps/embedding
cat > /data/pengshuang/desaymem/apps/embedding/server.py <<'PY'
import os
import secrets
from threading import Lock
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

API_KEY = os.environ['EMBEDDING_API_KEY']
MODEL_NAME = 'bge-m3'
model = SentenceTransformer(
    '/data/pengshuang/desaymem/models/bge-m3',
    device='cuda:0', local_files_only=True,
    model_kwargs={'torch_dtype': 'float16'},
)
model.max_seq_length = 1024
app = FastAPI()
security = HTTPBearer(auto_error=False)
lock = Lock()

def authenticate(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    if (credentials is None
        or credentials.scheme.lower() != 'bearer'
        or not secrets.compare_digest(credentials.credentials, API_KEY)):
        raise HTTPException(status_code=401, detail='Invalid API key')

class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]
    encoding_format: str = 'float'
    dimensions: int | None = None

@app.get('/v1/models', dependencies=[Depends(authenticate)])
def models():
    return {'object': 'list', 'data': [
        {'id': MODEL_NAME, 'object': 'model', 'owned_by': 'local'}
    ]}

@app.post('/v1/embeddings', dependencies=[Depends(authenticate)])
def embeddings(request: EmbeddingRequest):
    if request.model != MODEL_NAME:
        raise HTTPException(400, 'Model must be bge-m3')
    if request.encoding_format != 'float':
        raise HTTPException(400, 'Only encoding_format=float is supported')
    if request.dimensions not in (None, 1024):
        raise HTTPException(400, 'BGE-M3 outputs 1024 dimensions')
    texts = [request.input] if isinstance(request.input, str) else request.input
    if not 1 <= len(texts) <= 32:
        raise HTTPException(400, 'Provide 1 to 32 texts per request')
    if any(not text.strip() or len(text) > 20000 for text in texts):
        raise HTTPException(400, 'Each text must contain 1 to 20000 characters')
    with lock:
        tokens = model.tokenizer(
            texts, truncation=False, add_special_tokens=True,
        )['input_ids']
        if any(len(ids) > 1024 for ids in tokens):
            raise HTTPException(400, 'Each text must be at most 1024 tokens')
        vectors = model.encode(
            texts, batch_size=4, normalize_embeddings=True,
            convert_to_numpy=True,
        )
        token_count = sum(len(ids) for ids in tokens)
    return {
        'object': 'list', 'model': MODEL_NAME,
        'data': [
            {'object': 'embedding', 'index': i,
             'embedding': vector.astype('float32').tolist()}
            for i, vector in enumerate(vectors)
        ],
        'usage': {'prompt_tokens': token_count, 'total_tokens': token_count},
    }
PY
```

使用 Lock 将 GPU 推理串行化，每次最多以 4 条文本成批执行，降低并发显存峰值。锁不是限流或有界队列；大量请求仍可能排队超时。生产并发能力需要另行压测与网关限流。该服务没有配置 CORS，浏览器直接跨域调用需要另外设计，建议由业务后端调用。

### 9.1 启动

执行 `screen -S embedding`，在新会话内运行：

```bash
source /data/pengshuang/desaymem/envs/embedding/bin/activate
export CUDA_VISIBLE_DEVICES=4
export HF_HUB_OFFLINE=1
read -rsp '输入 Embedding API Key：' EMBEDDING_API_KEY
printf '\n'
export EMBEDDING_API_KEY
cd /data/pengshuang/desaymem/apps/embedding
uvicorn server:app --host 0.0.0.0 --port 20141 --workers 1
```

Key 可以与 LLM 相同，也可以不同，客户端必须匹配。必须保持 workers=1，增加 worker 会重复加载模型。启动成功后分离 screen，不要 Ctrl+C。

## 10. 本地电脑调用

### 10.1 配置表

| 字段 | LLM | Embedding |
|---|---|---|
| Base URL | http://10.133.72.161:20140/v1 | http://10.133.72.161:20141/v1 |
| 模型名 | memory-llm | bge-m3 |
| API Key | 启动时设置的 LLM Key | 启动时设置的 Embedding Key |
| 接口 | POST /v1/chat/completions | POST /v1/embeddings |
| 上限 | 单请求总上下文 8192 | 单文本 1024 tokens，32条/请求 |

办公电脑需要能访问公司网络。相同模型服务对所有获授权客户端使用同一端口，不需要按电脑分配端口。本地 URL 不使用 127.0.0.1，除非客户端就在服务所在容器或使用了端口转发。

### 10.2 准备本地环境

```powershell
python -m pip install openai httpx
Test-NetConnection 10.133.72.161 -Port 20140
Test-NetConnection 10.133.72.161 -Port 20141
```

TCP True 只说明连接可建立，接着验证 HTTP 状态和实际推理。本次 `/v1/models` 已返回 memory-llm，说明 20140 路径连到了目标模型服务。

### 10.3 LLM 测试（保存为 test_llm.py）

```python
from getpass import getpass
import time
import httpx
from openai import OpenAI

key = getpass('LLM API Key: ')
with OpenAI(
    base_url='http://10.133.72.161:20140/v1', api_key=key,
    http_client=httpx.Client(trust_env=False, timeout=120),
    max_retries=0,
) as client:
    print('模型列表：', [m.id for m in client.models.list().data])
    start = time.perf_counter()
    result = client.chat.completions.create(
        model='memory-llm',
        messages=[
            {'role': 'system', 'content': '仅提取用户明确表达的事实，不增加推断。'},
            {'role': 'user', 'content': '我开车时喜欢把空调设置成26度。'},
        ],
        max_tokens=256,
        extra_body={'chat_template_kwargs': {'enable_thinking': False}},
    )
    print(result.choices[0].message.content)
    print('耗时秒：', round(time.perf_counter() - start, 2))
    print('Token：', result.usage)
```

`trust_env=False` 避免本机代理环境变量将内网调用发送到代理。关闭 thinking 是本次常规记忆处理的调用选择；不是保证事实正确性的机制。“26度”不能自动推断为“偏暖”，应由业务提示词和结果校验控制。

### 10.4 Embedding 测试（保存为 test_embedding.py）

```python
from getpass import getpass
import httpx
from openai import OpenAI

key = getpass('Embedding API Key: ')
with OpenAI(
    base_url='http://10.133.72.161:20141/v1', api_key=key,
    http_client=httpx.Client(trust_env=False, timeout=120),
    max_retries=0,
) as client:
    response = client.embeddings.create(
        model='bge-m3',
        input=['用户喜欢空调26度', '用户习惯将空调设为26摄氏度'],
        encoding_format='float',
    )
    vectors = [item.embedding for item in response.data]
    print('数量：', len(vectors))
    print('维度：', len(vectors[0]))
    print('相似度：', sum(a*b for a, b in zip(vectors[0], vectors[1])))
```

必须显式使用 encoding_format='float'。部分 OpenAI SDK 默认可能请求 base64，本服务不支持该格式。模型输出已归一化，两向量点积可用于余弦相似度比较。

### 10.5 原始 HTTP 排查

```python
from getpass import getpass
import httpx

key = getpass('API Key: ')
try:
    response = httpx.get(
        'http://10.133.72.161:20140/v1/models',
        headers={'Authorization': f'Bearer {key}'},
        timeout=15, trust_env=False,
    )
    print(response.status_code)
    print(response.text)
except Exception as exc:
    print(type(exc).__name__, repr(exc))
```

Embedding 将端口改为 20141。不要只截取 SDK 的 Connection error，原始 HTTP 状态更便于定位。

### 10.6 Cline 等客户端

选择 OpenAI Compatible，填 20140 的 Base URL、LLM Key、memory-llm 和 8192 上下文。不要复制其他服务的 Qwen3.8-27B 名称或 262144 上下文配置。

普通对话 API 成功不等于 Cline 的完整编码 Agent 功能已验证。工具调用、流式输出和 thinking 相关参数需按客户端及当前 vLLM 配置单独联调；不要把 Reasoning Effort=low 当作关闭 Qwen thinking 的等价设置。

## 11. 日常运维和重启

### 11.1 例行检查

```bash
export PATH="/data/pengshuang/desaymem/envs/screen/bin:$PATH"
screen -ls
nvidia-smi -i 4
df -h /data
```

GPU Util=0% 可能仅表示当前无计算，不能判断服务未运行。容器中 nvidia-smi 进程列表可能受可见性影响；结合模型接口、显存和 screen 日志判断。

### 11.2 日志

在 screen 内 Ctrl+A 后按冒号，输入以下命令设置日志文件，再用 Ctrl+A 后按 H 切换日志记录（大写 H）：

```text
logfile /data/pengshuang/desaymem/logs/llm-screen.log
```

Embedding 使用不同文件名 embedding-screen.log。新启动前可先启用日志。长期运行要配置轮转或定期归档，避免日志无限增长；分享日志前去除凭据和用户内容。

### 11.3 正常重启

1. `screen -r llm` 或 `screen -r embedding` 回到目标服务。
2. Ctrl+C 退出前台服务，等待关闭；不要影响另一个服务。
3. 确认原实例结束，再运行对应启动命令并重新输入 Key。
4. 等待 Application startup complete。
5. 本地验证 /v1/models 和实际请求。
6. Ctrl+A、D 分离。

容器重启后先检查 screen -ls 和 GPU 占用，确认没有原实例；然后分别启动 LLM 与 Embedding。模型无需重新下载、环境无需重新安装。原始 screen 与环境变量不能跨容器重启保留。

## 12. 故障排查表

| 现象 | 可能原因 | 处理 |
|---|---|---|
| curl/ss/ping not found | 精简容器无命令 | 使用 Python/httpx 和本地 PowerShell |
| screen not found | 未安装或 PATH 未设置 | 使用完整路径或 export PATH |
| EnvironmentLocationNotFound | 对未创建环境执行 conda install | 使用 conda create |
| embedding/bin/activate 不存在 | Embedding venv 未创建 | 先 python -m venv |
| No module named sentence_transformers | 环境错误或未装依赖 | 检查 which python，再在目标环境安装 |
| 下载 Read timed out | CDN 读取慢或连接异常 | 观察字节进度；退出后同目录重试，持续失败核查域名 |
| 多条进度残影 | screen 渲染/旧进度未清理 | Ctrl+Q、分离重连，确认提示符 |
| 本地 TCP False | 服务未监听、映射/防火墙不通 | 容器内测试，检查 0.0.0.0 与管理员映射 |
| TCP True 但 HTTP 不通 | 转发到其他服务或后端无响应 | 原始 HTTP 请求并查看服务日志 |
| HTTP 401 | Key 不一致 | 与当前启动环境匹配，重启后重新设置 |
| HTTP 404 | URL 路径或模型名错误 | 使用 /v1 和准确别名，查看响应正文 |
| Embedding HTTP 400 | 格式/长度/维度不支持 | float、1024维、1–32条、每条≤1024 tokens |
| HTTP 422 | JSON 字段类型不正确 | input 使用字符串或字符串列表 |
| Address already in use | 旧服务仍占端口 | 查已有会话，不重复启动 |
| CUDA out of memory | 重复实例、批量过大、其他占用 | 查 GPU；Embedding 保持1 worker并减小批量 |
| 模型回答包含额外推测 | 生成行为不受约束 | 明确只抽取事实，增加结构和业务验证 |
| 旧记忆检索效果异常 | 新旧向量混用 | 用新模型重建独立索引 |

容器内 HTTP 测试改用 `http://127.0.0.1:20140/v1/models` 或 20141。仅“服务器 IP+端口”不足以推断内外端口一定相同；迁移到其他容器必须重新确认。

## 13. 接入车载记忆后端

以下是建议配置名称，不代表当前仓库已经支持这些环境变量；实际需要检查代码的配置读取与 provider 适配。

```dotenv
LLM_BASE_URL=http://10.133.72.161:20140/v1
LLM_MODEL=memory-llm
LLM_API_KEY=REPLACE_WITH_LLM_KEY
EMBEDDING_BASE_URL=http://10.133.72.161:20141/v1
EMBEDDING_MODEL=bge-m3
EMBEDDING_API_KEY=REPLACE_WITH_EMBEDDING_KEY
EMBEDDING_DIMENSIONS=1024
```

后端与服务处于同一容器时可用 127.0.0.1；不同容器中 localhost 指向自己，需使用可达的服务地址。两个 OpenAI 客户端分别配置 Base URL，不能把 LLM 地址误用于 Embedding。

建议落地顺序：

1. 备份旧数据库、配置和历史数据，保留原业务 ID。
2. 修改模型调用配置；Embedding 强制 encoding_format=float。
3. 检查所有外部调用点：总结、冲突处理、重排、多模态等，不能只改两个变量就宣称完全离线。
4. 在独立新表/集合中建立 BGE-M3 的 1024 维索引。
5. 对现有记忆文本重新生成向量；保留 user_id、时间戳、有效状态、来源和关联关系。
6. 对超过 1024 tokens 的文本显式拆分，并保留原始记忆关联；不要静默丢失尾部信息。
7. 验证新增、查询、更新、删除、用户隔离和时间冲突处理。
8. 验证模糊指令和对话效果，通过后切换索引，保留旧索引用于回滚。

即使旧模型也是1024维，新旧模型的向量空间也不同，不能直接混用。更换 LLM 也可能改变抽取格式，需要检查 JSON 解析、字段约束和冲突规则。服务本身没有用户身份记忆隔离，隔离由记忆后端实现。

若业务前端仍在阿里云，需要另行解决阿里云到公司内网的网络路径。办公电脑能访问不等于阿里云能访问。

## 14. 验收与当前边界

| 验收项 | 判定 |
|---|---|
| LLM 功能 | /v1/models 返回 memory-llm，chat 正常返回 |
| Embedding 功能 | 返回与输入数量相同的向量，每条1024维 |
| 鉴权 | 正确 Key 成功，错误 Key 返回401 |
| 后台运行 | screen 分离、SSH断开后，客户端仍可调用 |
| 单卡共存 | 两个服务同时运行并请求，无OOM |
| 性能 | 测量实际文本长度、并发下延迟和显存，不仅看加载成功 |
| 记忆业务 | 新增、时间更新、用户隔离及检索回归通过 |

截至本文整理，已证明模型部署与基础调用可行，但没有证据证明生产压力测试、后端整体迁移、工具调用和浏览器跨域均已完成。

当前是公司内网联调方案。API Key 是访问凭据，HTTP 不加密；面向更广范围时应由受控网关提供 HTTPS、访问控制和限流。不要把凭据提交到 Git 或放在公开前端。硬件故障、容器重启自动恢复、高可用和监控告警需要运维方案单独覆盖。

## 15. 快速查阅

- LLM：`http://10.133.72.161:20140/v1`，模型 `memory-llm`。
- Embedding：`http://10.133.72.161:20141/v1`，模型 `bge-m3`，float 输出，1024维。
- SSH：2214；应用端口：20140–20149。
- screen 会话：llm、embedding；后台分离：Ctrl+A、D。
- 工作目录：`/data/pengshuang/desaymem`。
- GPU：4；LLM 显存预算0.84，Embedding FP16、batch=4、workers=1。
- 旧环境已正常运行时不重复安装、不重复下载、不重复启动。
