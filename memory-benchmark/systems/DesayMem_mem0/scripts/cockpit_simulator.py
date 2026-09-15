"""Cockpit multi-turn dialog simulator for DesayMem.

Simulates a driver's complete in-car session: climate preference →
navigation → music → seat heating → cross-turn recall, validating
real LLM extraction and vector retrieval at each turn.

Requires live DashScope LLM/Embedding credentials in `.env`.
Persists to PostgreSQL (memories + entities) and SQLite `history.db`.

Usage:
    python scripts/cockpit_simulator.py                     # direct mode
    python scripts/cockpit_simulator.py --http               # HTTP client mode
    python scripts/cockpit_simulator.py --http --base-url http://192.168.0.166:8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from desaymem.core.config import Settings
from desaymem.core.enums import MemoryType
from desaymem.core.memory import DesayMemory

# ── Defaults ───────────────────────────────────────────────────────────────

TENANT = "oem_chery"
DRIVER = "user_driver"
VEHICLE = "vehicle_001"
SESSION = "session_drive_001"


# ── Dialog scenario data ────────────────────────────────────────────────────

DIALOG_TURNS: list[dict[str, Any]] = [
    {
        "label": "空调偏好",
        "messages": [
            {"role": "user", "content": "我开车的时候喜欢把空调调到22度"},
            {"role": "assistant", "content": "好的，已为您设置空调温度22度"},
        ],
        "search_query": "用户习惯的空调温度是多少",
        "expect_keyword": "22",
        "expect_label": "空调温度22度",
    },
    {
        "label": "导航目的地",
        "messages": [
            {"role": "user", "content": "导航去上海迪士尼"},
            {"role": "assistant", "content": "正在为您规划前往上海迪士尼的路线，预计30分钟到达"},
        ],
        "search_query": "上海迪士尼",
        "expect_keyword": "上海迪士尼",
        "expect_label": "导航目的地上海迪士尼",
        "infer": False,
    },
    {
        "label": "音乐偏好",
        "messages": [
            {"role": "user", "content": "开车时帮我放周杰伦的歌"},
            {"role": "assistant", "content": "已为您播放周杰伦的歌单"},
        ],
        "search_query": "司机喜欢听什么音乐",
        "expect_keyword": "周杰伦",
        "expect_label": "周杰伦音乐偏好",
    },
    {
        "label": "座椅加热（过程性记忆）",
        "messages": [
            {"role": "user", "content": "帮我把座椅加热打开到3挡"},
            {"role": "assistant", "content": "座椅加热已调至3挡，请注意温度"},
        ],
        "search_query": "座椅加热",
        "expect_keyword": "座椅",
        "expect_label": "座椅加热操作步骤",
        "memory_type": MemoryType.PROCEDURAL.value,
    },
    {
        "label": "跨轮上下文召回",
        "messages": [
            {"role": "user", "content": "我之前说的空调温度是多少度？"},
            {"role": "assistant", "content": "您之前说喜欢把空调调到22度"},
        ],
        "search_query": "用户习惯的空调温度是多少",
        "expect_keyword": "22",
        "expect_label": "从已有记忆中召回22度偏好",
        "skip_add": True,
    },
]


# ── Backend abstraction ─────────────────────────────────────────────────────

class CockpitBackend(ABC):
    """Abstract backend for calling DesayMem memory operations."""

    @abstractmethod
    async def add(
        self,
        messages: list[dict],
        user_id: str,
        tenant_id: str,
        vehicle_id: str = "",
        session_id: str = "",
        scene: str = "",
        infer: bool = True,
        memory_type: str | None = None,
    ) -> list[dict]:
        ...

    @abstractmethod
    async def search(
        self, query: str, user_id: str, tenant_id: str, top_k: int = 5
    ) -> list[dict]:
        ...

    @abstractmethod
    async def delete_all(self, user_id: str, tenant_id: str) -> int:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...


class DirectBackend(CockpitBackend):
    """Direct in-process DesayMemory with live LLM and persistent stores."""

    def __init__(self, settings: Settings) -> None:
        settings.search_threshold = 0.0
        self.memory = DesayMemory.from_settings(settings)

    async def prepare(self) -> None:
        await self.memory.prepare()

    async def add(
        self,
        messages: list[dict],
        user_id: str,
        tenant_id: str,
        vehicle_id: str = "",
        session_id: str = "",
        scene: str = "",
        infer: bool = True,
        memory_type: str | None = None,
    ) -> list[dict]:
        return await self.memory.add(
            messages,
            user_id=user_id,
            tenant_id=tenant_id,
            vehicle_id=vehicle_id,
            session_id=session_id,
            scene=scene,
            infer=infer,
            memory_type=memory_type,
        )

    async def search(
        self, query: str, user_id: str, tenant_id: str, top_k: int = 5
    ) -> list[dict]:
        return await self.memory.search(query, user_id=user_id, tenant_id=tenant_id, top_k=top_k)

    async def delete_all(self, user_id: str, tenant_id: str) -> int:
        return await self.memory.delete_all(user_id, tenant_id=tenant_id)

    async def close(self) -> None:
        await self.memory.close()


class HttpBackend(CockpitBackend):
    """HTTP client mode calling DesayMem API server via httpx."""

    def __init__(self, base_url: str) -> None:
        import httpx

        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30.0)

    async def add(
        self,
        messages: list[dict],
        user_id: str,
        tenant_id: str,
        vehicle_id: str = "",
        session_id: str = "",
        scene: str = "",
        infer: bool = True,
        memory_type: str | None = None,
    ) -> list[dict]:
        payload: dict[str, Any] = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "vehicle_id": vehicle_id,
            "session_id": session_id,
            "scene": scene,
            "messages": messages,
            "infer": infer,
        }
        if memory_type:
            payload["memory_type"] = memory_type
        resp = await self._client.post("/v1/memories", json=payload)
        resp.raise_for_status()
        body = resp.json()
        return body.get("memories", [])

    async def search(
        self, query: str, user_id: str, tenant_id: str, top_k: int = 5
    ) -> list[dict]:
        resp = await self._client.post(
            "/v1/memories/search",
            json={
                "tenant_id": tenant_id,
                "user_id": user_id,
                "query": query,
                "top_k": top_k,
            },
        )
        resp.raise_for_status()
        return resp.json().get("memories", [])

    async def delete_all(self, user_id: str, tenant_id: str) -> int:
        resp = await self._client.delete(
            f"/v1/users/{user_id}/memories",
            params={"tenant_id": tenant_id, "confirm": "true"},
        )
        resp.raise_for_status()
        return resp.json().get("deleted_count", 0)

    async def close(self) -> None:
        await self._client.aclose()


# ── Logging helpers ──────────────────────────────────────────────────────────

def _print_header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _print_turn(turn_idx: int, total: int, label: str) -> None:
    print(f"\n--- [轮次 {turn_idx}/{total}] {label} ---")


def _print_speaker(speaker: str, text: str) -> None:
    truncated = text[:120] + ("..." if len(text) > 120 else "")
    print(f"  {speaker}: {truncated}")


def _print_stored(memories: list[dict]) -> None:
    if not memories:
        print("  记忆存储: (无新记忆)")
        return
    for m in memories:
        content = m.get("content", "")
        mtype = m.get("memory_type", "semantic_memory")
        truncated = content[:100] + ("..." if len(content) > 100 else "")
        type_tag = f"[{mtype}]" if mtype != "semantic_memory" else ""
        print(f"  记忆存储 {type_tag}: {truncated}")


def _print_search(hits: list[dict], expect_keyword: str, expect_label: str) -> None:
    if not hits:
        print(f"  召回验证: ✗ 未召回任何记忆 (期望: {expect_label})")
        return
    for h in hits[:3]:
        content = h.get("content", "")
        truncated = content[:100] + ("..." if len(content) > 100 else "")
        print(f"  召回命中: {truncated}")
    found = any(expect_keyword in h.get("content", "") for h in hits)
    mark = "✓" if found else "✗"
    print(f"  召回验证: {mark} 期望「{expect_label}」{'已确认' if found else '未命中'}")


# ── Main simulation ──────────────────────────────────────────────────────────

async def run_simulator(
    backend: CockpitBackend,
    tenant: str,
    driver: str,
    vehicle: str,
    session: str,
) -> int:
    total = len(DIALOG_TURNS)
    failures: list[str] = []

    _print_header("DesayMem 车机多轮对话模拟器")

    prepare = getattr(backend, "prepare", None)
    if callable(prepare):
        await prepare()

    # Clean slate
    await backend.delete_all(driver, tenant_id=tenant)
    print(f"  已清空用户 {driver} 的历史记忆")

    for idx, turn in enumerate(DIALOG_TURNS, 1):
        _print_turn(idx, total, turn["label"])

        for msg in turn["messages"]:
            speaker = "司机" if msg["role"] == "user" else "车机"
            _print_speaker(speaker, msg["content"])

        if turn.get("skip_add"):
            # Cross-turn recall: only search, no add
            print("  (跨轮召回模式: 不写入新记忆，仅检索已有记忆)")
        else:
            memories = await backend.add(
                messages=turn["messages"],
                user_id=driver,
                tenant_id=tenant,
                vehicle_id=vehicle,
                session_id=session,
                scene="driving",
                infer=turn.get("infer", True),
                memory_type=turn.get("memory_type"),
            )
            _print_stored(memories)

            # Check if procedural memory type is preserved
            if turn.get("memory_type"):
                expected_type = turn["memory_type"]
                if memories and memories[0].get("memory_type") == expected_type:
                    print(f"  类型验证: ✓ 过程性记忆类型保留 ({expected_type})")
                else:
                    msg = f"轮次{idx}: 过程性记忆类型未保留"
                    failures.append(msg)
                    print(f"  类型验证: ✗ {msg}")

        # Search validation
        hits = await backend.search(
            query=turn["search_query"],
            user_id=driver,
            tenant_id=tenant,
            top_k=5,
        )
        _print_search(hits, turn["expect_keyword"], turn["expect_label"])

        found = any(turn["expect_keyword"] in h.get("content", "") for h in hits)
        if not found:
            failures.append(f"轮次{idx}「{turn['label']}」未召回「{turn['expect_label']}」")

    # Summary
    _print_header("模拟结果汇总")
    total_checks = total
    passed = total_checks - len(failures)
    print(f"  通过: {passed}/{total_checks}")
    if failures:
        print(f"  失败项:")
        for f in failures:
            print(f"    ✗ {f}")
    else:
        print("  全部轮次验证通过 ✓")

    await backend.close()
    return 1 if failures else 0


# ── CLI entry ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DesayMem 车机多轮对话模拟器（Live LLM，无需 Postgres）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
场景流程（5 轮）:
  1. 空调偏好 → 记忆抽取 + 检索验证
  2. 导航目的地 → 原文存储 (infer=false) + 检索验证
  3. 音乐偏好 → 记忆抽取 + 检索验证
  4. 座椅加热 → 过程性记忆 (procedural_memory) + 类型验证
  5. 跨轮召回 → 从已有记忆中召回空调偏好

示例:
  python scripts/cockpit_simulator.py
  python scripts/cockpit_simulator.py --http
  python scripts/cockpit_simulator.py --http --base-url http://192.168.0.166:8000
  python scripts/cockpit_simulator.py --tenant oem_chery --driver user_driver
""",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="使用 HTTP 客户端模式调 API server（默认直连 DesayMemory）",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="API server 地址（仅 --http 模式有效，默认 http://127.0.0.1:8000）",
    )
    parser.add_argument("--tenant", default=TENANT, help=f"租户 ID（默认 {TENANT}）")
    parser.add_argument("--driver", default=DRIVER, help=f"司机用户 ID（默认 {DRIVER}）")
    parser.add_argument("--vehicle", default=VEHICLE, help=f"车辆 ID（默认 {VEHICLE}）")
    parser.add_argument("--session", default=SESSION, help=f"会话 ID（默认 {SESSION}）")
    args = parser.parse_args()

    if args.http:
        backend: CockpitBackend = HttpBackend(args.base_url)
        print(f"模式: HTTP 客户端 → {args.base_url}")
    else:
        settings = Settings()
        settings.require_runtime_secrets()
        print(f"模式: 直连 DesayMemory | LLM={settings.llm_model} EMB={settings.embedding_model}")
        backend = DirectBackend(settings)

    try:
        ret = asyncio.run(
            run_simulator(
                backend=backend,
                tenant=args.tenant,
                driver=args.driver,
                vehicle=args.vehicle,
                session=args.session,
            )
        )
    except KeyboardInterrupt:
        print("\n\n模拟已中断")
        ret = 130
    except Exception as exc:
        exc_name = type(exc).__name__
        if "ConnectError" in exc_name or "Connection" in exc_name:
            print(f"\n连接失败: {exc}", file=sys.stderr)
            if args.http:
                print(
                    f"请确认 API server 已启动: uvicorn desaymem.api.main:app "
                    f"--host 0.0.0.0 --port 8000",
                    file=sys.stderr,
                )
            ret = 1
        else:
            print(f"\n模拟异常: {exc}", file=sys.stderr)
            raise

    raise SystemExit(ret)


if __name__ == "__main__":
    main()
