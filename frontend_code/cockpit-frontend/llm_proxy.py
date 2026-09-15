"""Cockpit LLM Proxy — local sidecar for the cockpit frontend.

Borrows Mem0 OSS proxy pattern (mem0.proxy.main.Completions.create):
  search memories → format into prompt → LLM completion → return reply

Config comes from .env in the same directory — no manual env vars needed.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

# Load .env from the same directory as this script
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
import httpx

# ── Qwen3 thinking tag constants ──
_THINK_OPEN = "<" + "think" + ">"
_THINK_CLOSE = "</" + "think" + ">"
_THINK_OPEN_PATTERN = re.compile(_THINK_OPEN, re.IGNORECASE)
_THINK_FULL_PATTERN = re.compile(_THINK_OPEN + r".*?" + _THINK_CLOSE, re.DOTALL | re.IGNORECASE)

# ── Markdown JSON code block pattern ──
_JSON_CODEBLOCK_PATTERN = re.compile(r"^```(?:json)?\s*\n?", re.IGNORECASE)
_JSON_CODEBLOCK_END_PATTERN = re.compile(r"\n?```\s*$")


# ── Cockpit structured decision prompt ────────────────────────────────────

COCKPIT_DECISION_PROMPT = """\
你是一个车载智能Agent的决策模块。
你的任务不是输出详细思考过程，而是根据用户输入、历史记忆和可用车机能力，生成：
1. 可供开发者查看的结构化决策摘要
2. 可直接向用户展示和语音播报的简短回答

禁止输出原始思考链、分析草稿、回答计划和系统规则。
必须只返回合法JSON，不要输出Markdown代码块，不要输出JSON之外的任何文字。

JSON格式如下：
{
  "decision": {
    "intent": "chat|navigation|music|climate|seat|vehicle_control|information_query|memory_query|other",
    "entities": {
      "destination": null,
      "current_location": null,
      "temperature": null,
      "music": null,
      "contact": null,
      "time": null
    },
    "retrieved_preferences": [],
    "missing_information": [],
    "action": "",
    "next_tools": [],
    "tool_mode": "simulation",
    "explanation": "",
    "confidence": 0.0
  },
  "reply": ""
}

要求：
- intent允许在必要时返回新的开放类别，不要强制错误归类
- entities允许按实际输入增加字段，不局限于示例字段
- retrieved_preferences只能来自传入的历史记忆，不能编造
- missing_information只列出完成任务真正缺少的信息
- next_tools只能表示下一步计划，不能假装已经执行
- 未接入真实工具时，tool_mode必须为simulation
- simulation模式下不能回答"导航已开始""空调已调整"等已执行结果
- production模式下，只有收到真实工具成功结果后才能声称操作完成
- explanation只写一句简洁、可验证的决策原因，不写内部推理
- confidence范围为0到1
- reply控制在1至2句话，一般不超过50个汉字
- 普通问候不需要复杂的工具规划
- 如果缺少必要信息，reply应向用户进行一次清晰追问
- 不得输出JSON之外的任何文字
"""


# ── Defaults (point to company server vLLM) ──
DEFAULT_LLM_MODEL = "memory-llm"
DEFAULT_LLM_BASE_URL = "http://10.133.72.161:20140/v1"
DEFAULT_PROXY_PORT = "8767"
DEFAULT_ENABLE_THINKING = False
DEFAULT_MAX_TOKENS = 256
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TOP_P = 0.8
DEFAULT_TOOL_MODE = "simulation"


def _get_bool(key: str, default: bool) -> bool:
    val = os.getenv(key, "").strip().lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    return default


def _get_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _get_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _get_tool_mode(req_mode: str | None) -> str:
    """Resolve tool_mode: request param > env > default. Fallback on invalid."""
    mode = (req_mode or os.getenv("COCKPIT_TOOL_MODE", DEFAULT_TOOL_MODE)).strip().lower()
    if mode in ("simulation", "production"):
        return mode
    return DEFAULT_TOOL_MODE


# ── Pydantic models for structured output validation ──

class DecisionSummary(BaseModel):
    intent: str = "other"
    entities: dict = {}
    retrieved_preferences: list[str] = []
    missing_information: list[str] = []
    action: str = ""
    next_tools: list[str] = []
    tool_mode: str = "simulation"
    explanation: str = ""
    confidence: float = 0.0

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0.0
        return max(0.0, min(1.0, v))


class ModelOutput(BaseModel):
    decision: DecisionSummary
    reply: str


class ChatProxyRequest(BaseModel):
    messages: list[dict]
    memory_context: list[str] | None = None
    tool_mode: str | None = None
    vehicle_context: dict | None = None
    tool_results: list[dict] | None = None


app = FastAPI(title="Cockpit LLM Proxy")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _mask_key(key: str) -> str:
    """Mask API key for safe logging — show only first/last 4 chars."""
    if not key or len(key) <= 12:
        return "***" if key else "(empty)"
    return key[:4] + "..." + key[-4:]


def _strip_thinking(reply: str) -> str:
    """Remove Qwen3 thinking blocks if they leak through.

    Handles both closed and unclosed thinking tags.
    """
    reply = _THINK_FULL_PATTERN.sub("", reply).strip()
    if _THINK_OPEN_PATTERN.search(reply):
        idx = _THINK_OPEN_PATTERN.search(reply).start()
        reply = reply[:idx].strip()
    return reply


def _strip_codeblock(text: str) -> str:
    """Remove Markdown JSON code block wrappers."""
    text = _JSON_CODEBLOCK_PATTERN.sub("", text)
    text = _JSON_CODEBLOCK_END_PATTERN.sub("", text)
    return text.strip()


def _parse_model_output(raw: str, tool_mode: str) -> dict:
    """Parse and validate model JSON output.

    Returns a dict with reply, decision, error fields.
    """
    # 1. Strip thinking tags
    raw = _strip_thinking(raw)
    # 2. Strip Markdown code block
    raw = _strip_codeblock(raw)

    # 3. Parse JSON
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "error": "invalid_json",
            "reply": None,
            "decision": None,
        }

    # 4. Validate with Pydantic
    try:
        model_output = ModelOutput(
            decision=DecisionSummary(**parsed.get("decision", {})),
            reply=parsed.get("reply", ""),
        )
    except Exception:
        return {
            "error": "malformed_output",
            "reply": None,
            "decision": None,
        }

    # 5. Override tool_mode with resolved value (model must not fake it)
    model_output.decision.tool_mode = tool_mode

    # 6. Check reply not empty
    reply = (model_output.reply or "").strip()
    if not reply:
        return {
            "error": "empty_reply",
            "reply": None,
            "decision": model_output.decision.model_dump(),
        }

    return {
        "error": None,
        "reply": reply,
        "decision": model_output.decision.model_dump(),
    }


@app.post("/chat")
async def chat(req: ChatProxyRequest) -> dict:
    t_start = time.monotonic()
    llm_api_key = os.getenv("LLM_API_KEY", "")
    llm_model = os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL)
    llm_base_url = os.getenv("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)
    enable_thinking = _get_bool("LLM_ENABLE_THINKING", DEFAULT_ENABLE_THINKING)
    max_tokens = _get_int("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS)
    temperature = _get_float("LLM_TEMPERATURE", DEFAULT_TEMPERATURE)
    top_p = _get_float("LLM_TOP_P", DEFAULT_TOP_P)
    tool_mode = _get_tool_mode(req.tool_mode)

    def _err(reply: str, error: str, detail: str = "") -> dict:
        return {
            "reply": reply,
            "decision": None,
            "model": llm_model,
            "error": error,
            "detail": detail,
            "latency_ms": int((time.monotonic() - t_start) * 1000),
        }

    # ── Error: API Key not configured ──
    if not llm_api_key:
        return _err("车机助手暂时无法回答，请稍后重试。", "api_key_missing")

    # Step 1: prepend system prompt
    prepared = [{"role": "system", "content": COCKPIT_DECISION_PROMPT}]
    for msg in req.messages:
        if msg.get("role") != "system":
            prepared.append(msg)

    # Step 2: format memories + tool_mode + vehicle_context + tool_results
    context_parts = []
    if req.memory_context:
        memories_text = "\n".join(f"- {m}" for m in req.memory_context)
        context_parts.append(f"【历史记忆】\n{memories_text}")
    context_parts.append(f"【当前工具模式】\n{tool_mode}")
    if req.vehicle_context:
        context_parts.append(f"【已知车机上下文】\n{json.dumps(req.vehicle_context, ensure_ascii=False, indent=2)}")
    else:
        context_parts.append("【已知车机上下文】\n未提供")
    if req.tool_results:
        context_parts.append(f"【实际工具结果】\n{json.dumps(req.tool_results, ensure_ascii=False, indent=2)}")
    else:
        context_parts.append("【实际工具结果】\n无")

    if prepared and prepared[-1]["role"] == "user":
        user_question = prepared[-1]["content"]
        prepared[-1]["content"] = (
            "\n".join(context_parts) + f"\n\n【用户当前请求】\n{user_question}"
        )
    else:
        prepared.append({"role": "user", "content": "\n".join(context_parts)})

    # Step 3: call LLM
    # NOTE: chat_template_kwargs.enable_thinking is for Qwen3-32B.
    # If switching to Qwen3.8 or other models, verify thinking control compatibility.
    url = f"{llm_base_url}/chat/completions"
    request_body = {
        "model": llm_model,
        "messages": prepared,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "top_p": top_p,
        "chat_template_kwargs": {
            "enable_thinking": enable_thinking,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {llm_api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
            )
    except httpx.ConnectTimeout:
        return _err("车机助手暂时无法回答，请稍后重试。", "connect_timeout")
    except httpx.ConnectError:
        return _err("车机助手暂时无法回答，请稍后重试。", "connect_refused")
    except httpx.RequestError as e:
        return _err("车机助手暂时无法回答，请稍后重试。", "network_error")

    # ── Error: HTTP non-2xx ──
    if not resp.is_success:
        hint = ""
        if resp.status_code in (401, 403):
            hint = "API Key 可能不正确"
        elif resp.status_code == 400:
            hint = "请求参数被拒绝，可能 chat_template_kwargs 未被支持"
        elif resp.status_code >= 500:
            hint = "服务器模型服务内部错误"
        return _err("车机助手暂时无法回答，请稍后重试。", f"http_{resp.status_code}", hint)

    latency_ms = int((time.monotonic() - t_start) * 1000)

    # ── Error: invalid JSON ──
    try:
        data = resp.json()
    except Exception:
        return _err("车机助手暂时无法回答，请稍后重试。", "invalid_json")

    # ── Error: missing choices ──
    choices = data.get("choices")
    if not choices or not isinstance(choices, list) or len(choices) == 0:
        return _err("车机助手暂时无法回答，请稍后重试。", "missing_choices")

    try:
        raw_content = choices[0]["message"].get("content") or ""
    except (KeyError, TypeError, IndexError):
        return _err("车机助手暂时无法回答，请稍后重试。", "malformed_response")

    # ── Parse and validate structured output ──
    parsed = _parse_model_output(raw_content, tool_mode)

    if parsed["error"]:
        if parsed["error"] == "empty_reply":
            return _err("模型未返回有效回答", "empty_reply")
        elif parsed["error"] == "invalid_json":
            return _err("车机助手暂时无法回答，请稍后重试。", "invalid_json", "模型返回非合法JSON")
        elif parsed["error"] == "malformed_output":
            return _err("车机助手暂时无法回答，请稍后重试。", "malformed_output", "模型输出结构不符合要求")

    # ── Extract usage stats ──
    usage = data.get("usage", {})
    usage_info = {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }

    return {
        "reply": parsed["reply"],
        "decision": parsed["decision"],
        "model": llm_model,
        "usage": usage_info,
        "latency_ms": latency_ms,
    }


@app.get("/health")
async def health() -> dict:
    api_key = os.getenv("LLM_API_KEY", "")
    return {
        "status": "ok" if api_key else "no_api_key",
        "model": os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL),
        "base_url": os.getenv("LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
        "api_key": _mask_key(api_key),
        "enable_thinking": _get_bool("LLM_ENABLE_THINKING", DEFAULT_ENABLE_THINKING),
        "max_tokens": _get_int("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS),
        "tool_mode": os.getenv("COCKPIT_TOOL_MODE", DEFAULT_TOOL_MODE),
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Cockpit LLM Proxy")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("PROXY_PORT", DEFAULT_PROXY_PORT)))
    args = parser.parse_args()

    import uvicorn

    api_key = os.getenv("LLM_API_KEY", "")
    print(f"LLM Proxy starting on http://{args.host}:{args.port}")
    print(f"  Model:           {os.getenv('LLM_MODEL', DEFAULT_LLM_MODEL)}")
    print(f"  Base URL:        {os.getenv('LLM_BASE_URL', DEFAULT_LLM_BASE_URL)}")
    print(f"  Enable thinking: {_get_bool('LLM_ENABLE_THINKING', DEFAULT_ENABLE_THINKING)}")
    print(f"  Max tokens:      {_get_int('LLM_MAX_TOKENS', DEFAULT_MAX_TOKENS)}")
    print(f"  Temperature:     {_get_float('LLM_TEMPERATURE', DEFAULT_TEMPERATURE)}")
    print(f"  Tool mode:       {os.getenv('COCKPIT_TOOL_MODE', DEFAULT_TOOL_MODE)}")
    print(f"  API Key:         {'configured (' + _mask_key(api_key) + ')' if api_key else 'NOT SET — edit .env'}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
