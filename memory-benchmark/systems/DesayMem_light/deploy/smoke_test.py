"""Post-deployment API smoke test using only the Python standard library."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from uuid import uuid4


def call(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    request = urllib.request.Request(
        url, data=body, method=method,
        headers={"Content-Type": "application/json", "X-Request-Id": str(uuid4())},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--wait-seconds", type=int, default=120)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    status, ready = call("GET", base + "/health/ready")
    if status != 200:
        raise SystemExit(f"readiness failed: {status} {ready}")

    marker = f"SMOKE-{uuid4().hex[:12]}"
    scope = {
        "tenant_id": "desaymem_smoke", "user_id": "smoke_user",
        "vehicle_id": "smoke_vehicle", "occupant_id": "driver", "session_id": marker,
    }
    content = f"{marker}：我在测试车辆中偏好较低的音乐音量。" * 180
    status, accepted = call("POST", base + "/v1/messages", {
        "request_id": marker, "scope": scope, "sequence_no": 1,
        "role": "user", "content": content, "metadata": {"tags": ["smoke-test"]},
    })
    if status != 202:
        raise SystemExit(f"message ingest failed: {status} {accepted}")

    search_scope = {key: value for key, value in scope.items() if key != "session_id"}
    deadline = time.monotonic() + args.wait_seconds
    while time.monotonic() < deadline:
        status, result = call("POST", base + "/v1/memories/search", {
            "request_id": str(uuid4()), "query": marker, "scope": search_scope,
            "session_id": marker, "top_k": 5, "vehicle_only": True,
        })
        if status == 200 and (result.get("facts") or result.get("events")):
            print(json.dumps({
                "status": "ok", "marker": marker,
                "facts": len(result.get("facts", [])), "events": len(result.get("events", [])),
                "llm_calls_on_search": result.get("usage", {}).get("llm_calls"),
            }, ensure_ascii=False))
            return
        time.sleep(3)
    raise SystemExit(f"memory pipeline did not finish within {args.wait_seconds}s; marker={marker}")


if __name__ == "__main__":
    main()
