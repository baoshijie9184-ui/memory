"""DesayMem Light 前端服务 — 静态页面 + JSON 镜像 + 后端/LLM 代理。

浏览器无法直接读本地文件，本服务把 JSON 镜像目录暴露为只读 API：

    GET  /api/tables                      → 所有表 {table: {rows, table_version, mtime}}
    GET  /api/tables/{table}              → 单表完整内容 {"table_version": n, "rows": {...}}
    GET  /api/health                      → {ok, mirror_root, backend}
    POST /api/backend/{path}              → 反向代理到 DesayMem Light API（规避 CORS）
    POST /api/llm/chat                    → 车机 LLM 对话代理（隐藏 vLLM API Key）

静态托管同目录 index.html。JSON 镜像由 DesayMem_light MirrorWorker 持续投影，
本服务只读镜像；后端 API 仅转发；LLM 调用由本服务携带 Key 转发到 vLLM。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent

# 可用环境变量覆盖：MIRROR_ROOT / FRONTEND_PORT / BACKEND_URL / LLM_*
MIRROR_ROOT = Path(
    os.environ.get(
        "MIRROR_ROOT",
        "/data/pengshuang/memory-benchmark/data/desaymem_light/json_mirror/postgres",
    )
)
PORT = int(os.environ.get("FRONTEND_PORT", "20147"))
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:20148").rstrip("/")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:20140/v1").rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "boluoboluomi")
LLM_MODEL = os.environ.get("LLM_MODEL", "memory-llm")
# 512：决策 JSON（decision 全字段 + reply）在 256 时偶发被截断 → invalid_json
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "512"))
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "30"))

_TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_BACKEND_PATH_RE = re.compile(r"^/api/backend/(.*)$")

# ── 车机决策提示词（参考 cockpit-frontend/llm_proxy.py） ──
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
- explanation只写一句简洁、可验证的决策原因，不写内部推理
- confidence范围为0到1
- reply控制在1至2句话，一般不超过50个汉字
- 普通问候不需要复杂的工具规划
- 如果缺少必要信息，reply应向用户进行一次清晰追问
- 不得输出JSON之外的任何文字
"""

_THINK_FULL = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think>", re.IGNORECASE)
_CODEBLOCK_START = re.compile(r"^```(?:json)?\s*\n?", re.IGNORECASE)
_CODEBLOCK_END = re.compile(r"\n?```\s*$")


def _strip_model_noise(raw: str) -> str:
    """去掉 Qwen3 thinking 泄漏与 Markdown 代码块包装。"""
    raw = _THINK_FULL.sub("", raw).strip()
    if _THINK_OPEN.search(raw):
        raw = raw[: _THINK_OPEN.search(raw).start()].strip()
    raw = _CODEBLOCK_START.sub("", raw)
    raw = _CODEBLOCK_END.sub("", raw)
    return raw.strip()


def _table_files() -> dict[str, Path]:
    if not MIRROR_ROOT.is_dir():
        return {}
    return {
        p.stem: p for p in MIRROR_ROOT.glob("*.json") if _TABLE_NAME_RE.match(p.stem)
    }


def _load_table(name: str) -> dict | None:
    path = _table_files().get(name)
    if path is None:
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        # 投影采用 tmpfile + os.replace 原子写，读到的永远是完整文件；
        # 万一损坏返回 None，前端按"读取失败"处理
        return None


def _llm_chat(handler: "FrontendHandler", req: dict) -> None:
    """POST /api/llm/chat — 组装车机决策 prompt 并调 vLLM，Key 不出本服务。"""
    t0 = time.monotonic()

    def err(reply: str, error: str, detail: str = "") -> None:
        payload = {"reply": reply, "decision": None, "error": error, "detail": detail,
                   "latency_ms": int((time.monotonic() - t0) * 1000)}
        handler._send_json(payload)

    messages = req.get("messages") or []
    memory_context = req.get("memory_context") or []
    vehicle_context = req.get("vehicle_context") or {}

    prepared = [{"role": "system", "content": COCKPIT_DECISION_PROMPT}]
    for m in messages:
        if m.get("role") != "system":
            prepared.append(m)

    context_parts = []
    if memory_context:
        memories_text = "\n".join(f"- {m}" for m in memory_context)
        context_parts.append(f"【历史记忆】\n{memories_text}")
    context_parts.append("【当前工具模式】\nsimulation")
    context_parts.append(
        "【已知车机上下文】\n" + json.dumps(vehicle_context, ensure_ascii=False, indent=2)
    )
    context_parts.append("【实际工具结果】\n无")

    if prepared and prepared[-1]["role"] == "user":
        user_question = prepared[-1]["content"]
        prepared[-1]["content"] = (
            "\n".join(context_parts) + f"\n\n【用户当前请求】\n{user_question}"
        )
    else:
        prepared.append({"role": "user", "content": "\n".join(context_parts)})

    body = json.dumps({
        "model": LLM_MODEL,
        "messages": prepared,
        "temperature": LLM_TEMPERATURE,
        "max_tokens": LLM_MAX_TOKENS,
        "top_p": 0.8,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode("utf-8")

    url = f"{LLM_BASE_URL}/chat/completions"
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Authorization", f"Bearer {LLM_API_KEY}")
    request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=LLM_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        hint = "API Key 可能不正确" if e.code in (401, 403) else ""
        err("车机助手暂时无法回答，请稍后重试。", f"http_{e.code}", hint)
        return
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        err("车机助手暂时无法回答，请稍后重试。", "llm_unreachable", str(e))
        return

    try:
        raw = data["choices"][0]["message"]["content"] or ""
    except (KeyError, TypeError, IndexError):
        err("车机助手暂时无法回答，请稍后重试。", "malformed_response")
        return

    def _parse(raw_text: str):
        cleaned = _strip_model_noise(raw_text)
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
        return json.loads(cleaned)

    try:
        parsed = _parse(raw)
    except json.JSONDecodeError:
        # vLLM/Qwen 偶发输出截断（max_tokens）或 thinking 泄漏导致非法 JSON：
        # 记录原始输出并自动重试一次（重试成功则对用户透明）。
        print(f"[llm] invalid json, retrying. raw({len(raw)}): {raw[:200]!r}", file=sys.stderr)
        try:
            with urllib.request.urlopen(urllib.request.Request(
                url, data=body, method="POST",
                headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
            ), timeout=LLM_TIMEOUT) as resp:
                data = json.loads(resp.read())
            raw = data["choices"][0]["message"]["content"] or ""
            parsed = _parse(raw)
        except (json.JSONDecodeError, KeyError, TypeError, IndexError,
                urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError) as e:
            err("车机助手暂时无法回答，请稍后重试。", "invalid_json", f"模型返回非合法JSON（已重试）: {e}")
            return
    decision = parsed.get("decision") or {}
    reply = (parsed.get("reply") or "").strip()

    if not reply:
        err("模型未返回有效回答", "empty_reply")
        return

    usage = data.get("usage", {})
    payload = {
        "reply": reply,
        "decision": decision,
        "model": LLM_MODEL,
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
        "latency_ms": int((time.monotonic() - t0) * 1000),
    }
    handler._send_json(payload)


class FrontendHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    # ── helpers ──
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def log_message(self, fmt: str, *args) -> None:  # 静默常规访问日志
        pass

    # ── 后端 API 反向代理（转发 body/status/Content-Type，规避 CORS） ──
    def _proxy_backend(self, subpath: str) -> None:
        url = f"{BACKEND_URL}/{subpath}"
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=body, method=self.command)
        if ctype := self.headers.get("Content-Type"):
            req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read()
                self.send_response(resp.status)
                self.send_header(
                    "Content-Type",
                    resp.headers.get("Content-Type", "application/json"),
                )
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as e:
            payload = e.read()
            self.send_response(e.code)
            self.send_header(
                "Content-Type", e.headers.get("Content-Type", "application/json")
            )
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
        except (urllib.error.URLError, OSError) as e:
            self._send_error_json(502, f"后端不可达 {BACKEND_URL}: {e}")

    # ── API routes ──
    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/api/health":
            self._send_json(
                {
                    "ok": True,
                    "mirror_root": str(MIRROR_ROOT),
                    "backend": BACKEND_URL,
                    "port": PORT,
                }
            )
            return
        if path == "/api/tables":
            result = {}
            for name, p in _table_files().items():
                data = _load_table(name)
                if data is None:
                    continue
                rows = data.get("rows", {})
                entry = {
                    "rows": len(rows),
                    "table_version": data.get("table_version", 0),
                    "mtime": p.stat().st_mtime,
                }
                # memory_items 同时按 memory_type 分桶，前端"记忆视图"
                # 直接用概览数据渲染 fact/event/cross_event 计数，
                # 避免为分型再拉整个明细文件（embedding 占位后仍省一次请求）
                if name == "memory_items":
                    by_type = {}
                    for cell in rows.values():
                        memory_type = (cell.get("data") or {}).get("memory_type")
                        if memory_type:
                            by_type[memory_type] = by_type.get(memory_type, 0) + 1
                    entry["by_type"] = by_type
                # memory_jobs 按 status 分桶：前端在 jobs 行上标红失败数
                # （排查"为什么没沉淀"时一眼看到管线断在哪）
                if name == "memory_jobs":
                    by_status = {}
                    for cell in rows.values():
                        status = (cell.get("data") or {}).get("status")
                        if status:
                            by_status[status] = by_status.get(status, 0) + 1
                    entry["by_status"] = by_status
                result[name] = entry
            self._send_json(result)
            return
        match = re.fullmatch(r"/api/tables/([a-z][a-z0-9_]*)", path)
        if match:
            name = match.group(1)
            data = _load_table(name)
            if data is None:
                known = ", ".join(sorted(_table_files())) or "(无)"
                self._send_error_json(
                    404, f"表 {name} 不存在或不可读。已知表: {known}"
                )
                return
            self._send_json(data)
            return
        match = _BACKEND_PATH_RE.match(path)
        if match:
            self._proxy_backend(match.group(1))
            return
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/llm/chat":
            _llm_chat(self, self._read_json_body())
            return
        match = _BACKEND_PATH_RE.match(path)
        if match:
            self._proxy_backend(match.group(1))
            return
        self._send_error_json(404, "未知 POST 路径: " + path)


def main() -> None:
    if not MIRROR_ROOT.is_dir():
        print(f"[WARN] 镜像目录不存在: {MIRROR_ROOT}（左侧面板将为空）")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), FrontendHandler)
    print(f"DesayMem Light 前端:  http://127.0.0.1:{PORT}")
    print(f"JSON 镜像目录:        {MIRROR_ROOT}")
    print(f"后端 API 代理:        {BACKEND_URL}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
