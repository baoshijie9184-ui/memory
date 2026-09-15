"""Minimal LLM proxy for cockpit frontend — hides DashScope API key.

Borrows Mem0 OSS proxy pattern (mem0.proxy.main.Completions.create):
  search memories → format into prompt → LLM completion → return reply

Run locally:  python scripts/llm_proxy.py
Deploy on server: python scripts/llm_proxy.py --host 0.0.0.0 --port 8767
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import httpx


# ── Mem0 OSS MEMORY_ANSWER_PROMPT (adapted for cockpit) ────────────────────

COCKPIT_ANSWER_PROMPT = """\
你是一个车载智能语音助手。根据用户提供的历史记忆和当前对话，生成自然、简洁的回复。

规则：
- 从历史记忆中提取与当前问题相关的信息
- 如果记忆中有相关偏好，自然地在回复中体现（如"您之前说喜欢..."）
- 如果没有相关记忆，正常回答用户问题，不要说"未找到记忆"
- 回复控制在 1-2 句话，口语化，适合语音播报
"""


class ChatProxyRequest(BaseModel):
    messages: list[dict]
    memory_context: list[str] | None = None  # retrieved memories to inject


app = FastAPI(title="Cockpit LLM Proxy")

allowed_origins = [
    item.strip()
    for item in os.getenv(
        "LLM_PROXY_ALLOWED_ORIGINS",
        "http://127.0.0.1,http://localhost",
    ).split(",")
    if item.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/chat")
async def chat(req: ChatProxyRequest, request: Request) -> dict:
    """Generate a car assistant reply, informed by retrieved memories.

    Follows Mem0 OSS proxy pattern:
      1. Prepend system prompt (COCKPIT_ANSWER_PROMPT)
      2. Format memories + user question into the last user message
      3. Call LLM
    """
    proxy_api_key = os.getenv("LLM_PROXY_API_KEY", "")
    supplied_key = request.headers.get("X-Proxy-API-Key", "")
    if proxy_api_key and not secrets.compare_digest(proxy_api_key, supplied_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    llm_api_key = os.getenv("LLM_API_KEY", "")
    llm_model = os.getenv("LLM_MODEL", "qwen-plus")
    llm_base_url = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    if not llm_api_key:
        return {"reply": "(LLM_API_KEY 未配置，请设置环境变量)", "model": llm_model}

    # Step 1: prepend system prompt (Mem0 pattern: _prepare_messages)
    prepared = [{"role": "system", "content": COCKPIT_ANSWER_PROMPT}]
    for msg in req.messages:
        if msg.get("role") != "system":
            prepared.append(msg)

    # Step 2: format memories into the last user message (Mem0 pattern: _format_query_with_memories)
    if req.memory_context and prepared and prepared[-1]["role"] == "user":
        memories_text = "\n".join(f"- {m}" for m in req.memory_context)
        user_question = prepared[-1]["content"]
        prepared[-1]["content"] = (
            f"- 相关历史记忆:\n{memories_text}\n\n"
            f"- 用户当前问题: {user_question}"
        )
    elif req.memory_context:
        memories_text = "\n".join(f"- {m}" for m in req.memory_context)
        prepared.append({"role": "user", "content": f"- 相关历史记忆:\n{memories_text}"})

    # Step 3: call LLM (Mem0 pattern: litellm.completion → DashScope OpenAI-compatible)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{llm_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {llm_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": llm_model,
                    "messages": prepared,
                    "temperature": 0.7,
                    "max_tokens": 500,
                    "top_p": 0.8,
                },
            )
        resp.raise_for_status()
        data = resp.json()
        reply = data["choices"][0]["message"]["content"]
        return {"reply": reply, "model": llm_model}
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream LLM request failed",
        )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "model": os.getenv("LLM_MODEL", "qwen-plus")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Cockpit LLM Proxy")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()

    import uvicorn

    print(f"LLM Proxy starting on http://{args.host}:{args.port}")
    print(f"Model: {os.getenv('LLM_MODEL', 'qwen-plus')}")
    print(f"API Key: {'configured' if os.getenv('LLM_API_KEY') else 'NOT SET'}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
