import os
import secrets
from threading import Lock

from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

API_KEY = os.environ["EMBEDDING_API_KEY"]
MODEL_NAME = "bge-m3"

model = SentenceTransformer(
    "/data/pengshuang/desaymem/models/bge-m3",
    device="cuda:0",
    local_files_only=True,
    model_kwargs={"torch_dtype": "float16"},
)
model.max_seq_length = 1024

app = FastAPI()
security = HTTPBearer(auto_error=False)
lock = Lock()


def authenticate(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not secrets.compare_digest(credentials.credentials, API_KEY)
    ):
        raise HTTPException(status_code=401, detail="Invalid API key")


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]
    encoding_format: str = "float"
    dimensions: int | None = None


@app.get("/v1/models", dependencies=[Depends(authenticate)])
def models():
    return {
        "object": "list",
        "data": [{
            "id": MODEL_NAME,
            "object": "model",
            "owned_by": "local",
        }],
    }


@app.post("/v1/embeddings", dependencies=[Depends(authenticate)])
def embeddings(request: EmbeddingRequest):
    if request.model != MODEL_NAME:
        raise HTTPException(400, "Model must be bge-m3")
    if request.encoding_format != "float":
        raise HTTPException(400, "Only encoding_format=float is supported")
    if request.dimensions not in (None, 1024):
        raise HTTPException(400, "BGE-M3 outputs 1024 dimensions")

    texts = [request.input] if isinstance(request.input, str) else request.input

    if not 1 <= len(texts) <= 32:
        raise HTTPException(400, "Provide 1 to 32 texts per request")
    if any(not text.strip() or len(text) > 20000 for text in texts):
        raise HTTPException(400, "Each text must contain 1 to 20000 characters")

    # 串行执行 GPU 推理，防止多个请求同时增加显存峰值
    with lock:
        tokens = model.tokenizer(
            texts,
            truncation=False,
            add_special_tokens=True,
        )["input_ids"]

        # 超长文本明确报错，避免静默截断记忆内容
        if any(len(ids) > 1024 for ids in tokens):
            raise HTTPException(400, "Each text must be at most 1024 tokens")

        vectors = model.encode(
            texts,
            batch_size=4,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        token_count = sum(len(ids) for ids in tokens)

    return {
        "object": "list",
        "model": MODEL_NAME,
        "data": [
            {
                "object": "embedding",
                "index": i,
                "embedding": vector.astype("float32").tolist(),
            }
            for i, vector in enumerate(vectors)
        ],
        "usage": {
            "prompt_tokens": token_count,
            "total_tokens": token_count,
        },
    }
