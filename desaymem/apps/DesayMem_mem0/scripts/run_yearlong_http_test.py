"""HTTP year-long fuzzy-memory eval against a live DesayMem API.

Designed so the same command can record a cloud baseline (current server)
and later a post-layers run after the new code is deployed.

This script talks ONLY to HTTP. It does not import DesayMemory, so local
unreleased L2/L3 code cannot leak into the cloud measurement.

Examples:
    python scripts/run_yearlong_http_test.py --mode dry-run
    python scripts/run_yearlong_http_test.py --mode all --profile core --reset
    python scripts/run_yearlong_http_test.py --mode search --label baseline_pre_layers

Default target: http://47.115.228.135/memory
Default dataset: D:\\workspace\\Desay_mem0_data\\yearlong_cockpit_memory_dataset
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_DATASET = Path(r"D:\workspace\Desay_mem0_data\yearlong_cockpit_memory_dataset\yearlong_cockpit_sessions.jsonl")
DEFAULT_GOLD = Path(r"D:\workspace\Desay_mem0_data\yearlong_cockpit_memory_dataset\yearlong_fuzzy_memory_gold.json")
DEFAULT_REPORT_DIR = ROOT / "data_cesi" / "reports"
DEFAULT_BASE_URL = "http://47.115.228.135/memory"

TENANT_ID = "oem_desay_demo"
USER_ID = "family_driver_001"

CORE_TAGS = {
    "亲子餐厅",
    "饮食偏好",
    "空气差老规矩",
    "西餐习惯",
    "相似亲子餐厅",
}

# Gold-aware diagnostics. Keyword recall is the comparable numeric score.
# The extra flags exist to catch the failure modes gold actually cares about;
# they are recorded, not hidden behind a single pass/fail bit.
CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "fuzzy_01",
        "query": "我们经常去的亲子餐厅",
        "keywords": ("童梦森林", "亲子", "孩子"),
        "habit_names": ("童梦森林",),
        "also_frequent": ("麦田亲子", "小象花园"),
        "one_shot_trap": ("童趣岛",),
        "gold_expect": "高频到访应指向童梦森林；童趣岛只收藏过一次，不能当成经常去。",
    },
    {
        "id": "fuzzy_02",
        "query": "根据我爱吃的菜，找个最近的地方",
        "keywords": ("粤菜", "少油", "香菜"),
        "preference_cues": ("少油", "香菜", "清淡", "鲈鱼", "白灼虾", "酿豆腐", "粤菜"),
        "gold_expect": "记忆只应给出饮食偏好；不能编造最近的具体餐厅。",
    },
    {
        "id": "fuzzy_03",
        "query": "外面的空气质量不好，按老规矩给我调整一下",
        "keywords": ("车窗", "内循环", "空气净化", "3档", "23"),
        "operation_cues": ("车窗", "内循环", "空气净化", "3档", "3挡", "23"),
        "gold_expect": "召回关窗+内循环+净化3档+23度这一组；记忆层不负责是否自动执行。",
    },
    {
        "id": "fuzzy_04",
        "query": "帮我找一下西餐厅，按照习惯进行点餐",
        "keywords": ("西冷牛排", "七分熟", "蘑菇汤", "柠檬水"),
        "order_cues": ("西冷", "七分熟", "蘑菇汤", "柠檬水"),
        "gold_expect": "召回固定套餐即可；未选店就声称点餐成功算硬失败（本脚本只测记忆召回）。",
    },
    {
        "id": "fuzzy_05",
        "query": "根据上周的亲子餐厅，帮我预定一个差不多的餐厅",
        "keywords": ("2026-08-29", "童梦森林", "儿童活动区", "宝宝椅"),
        "last_week_cues": ("2026-08-29", "08-29", "童梦森林"),
        "similar_candidate": ("童趣岛",),
        "outside_window_leak": ("麦田亲子", "小象花园"),
        "gold_window": ("2026-08-24", "2026-08-30"),
        "gold_expect": "上周锚点是 2026-08-29 童梦森林；8-22 麦田不在该自然周；童趣岛是相似候选不是上周到访。",
    },
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"数据集不存在：{path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"第 {line_number} 行不是有效 JSON：{exc}") from exc
            required = {"tenant_id", "user_id", "vehicle_id", "session_id", "messages", "api_payload"}
            missing = required.difference(row)
            if missing:
                raise ValueError(f"第 {line_number} 行缺少字段：{sorted(missing)}")
            rows.append(row)
    if not rows:
        raise ValueError("数据集为空")
    return rows


def select_rows(rows: Iterable[dict[str, Any]], profile: str, limit: int | None) -> list[dict[str, Any]]:
    selected = list(rows)
    if profile == "core":
        selected = [row for row in selected if CORE_TAGS.intersection(row.get("tags") or [])]
    if limit is not None:
        selected = selected[:limit]
    return selected


def validate_dataset(rows: list[dict[str, Any]], gold_path: Path) -> dict[str, Any]:
    if not gold_path.exists():
        raise FileNotFoundError(f"标准答案不存在：{gold_path}")
    gold = json.loads(gold_path.read_text(encoding="utf-8-sig"))
    joined = "\n".join(
        message.get("content", "")
        for row in rows
        for message in row.get("messages", [])
    )
    required_evidence = ["童梦森林亲子餐厅", "少油、不要香菜", "空气净化调到3档", "西冷牛排七分熟", "童趣岛家庭餐厅"]
    missing = [text for text in required_evidence if text not in joined]
    return {
        "valid": not missing,
        "session_count": len(rows),
        "first_started_at": rows[0].get("started_at"),
        "last_started_at": rows[-1].get("started_at"),
        "gold_case_count": len(gold.get("test_cases") or []),
        "missing_required_evidence": missing,
        "gold_time_range": gold.get("time_range"),
    }


def _blob(hits: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for hit in hits:
        parts.append(str(hit.get("content") or ""))
        meta = hit.get("metadata") or {}
        if isinstance(meta, dict):
            parts.append(json.dumps(meta, ensure_ascii=False))
        parts.append(str(hit.get("memory_type") or ""))
    return "\n".join(parts)


def _contains_any(text: str, needles: tuple[str, ...] | list[str]) -> list[str]:
    return [item for item in needles if item and item in text]


def evaluate_case(case: dict[str, Any], hits: list[dict[str, Any]], search_body: dict[str, Any]) -> dict[str, Any]:
    combined = _blob(hits)
    keywords = tuple(case["keywords"])
    matched = _contains_any(combined, keywords)
    missing = [item for item in keywords if item not in matched]
    recall = len(matched) / len(keywords) if keywords else 0.0
    top_content = str(hits[0].get("content") or "") if hits else ""

    diagnostics: dict[str, Any] = {
        "habit_hit": _contains_any(combined, case.get("habit_names") or ()),
        "also_frequent_hit": _contains_any(combined, case.get("also_frequent") or ()),
        "one_shot_trap_hit": _contains_any(combined, case.get("one_shot_trap") or ()),
        "one_shot_is_top1": bool(case.get("one_shot_trap") and any(name in top_content for name in case.get("one_shot_trap") or ())),
        "preference_cues_hit": _contains_any(combined, case.get("preference_cues") or ()),
        "operation_cues_hit": _contains_any(combined, case.get("operation_cues") or ()),
        "order_cues_hit": _contains_any(combined, case.get("order_cues") or ()),
        "last_week_cues_hit": _contains_any(combined, case.get("last_week_cues") or ()),
        "similar_candidate_hit": _contains_any(combined, case.get("similar_candidate") or ()),
        "outside_window_leak": _contains_any(combined, case.get("outside_window_leak") or ()),
        "profile_attached": isinstance(search_body.get("profile"), dict),
        "episode_hits": sum(1 for hit in hits if hit.get("memory_type") == "episodic_memory"),
    }
    # Gold-oriented red flags (not the same as keyword pass).
    flags: list[str] = []
    if case["id"] == "fuzzy_01" and diagnostics["one_shot_is_top1"] and not diagnostics["habit_hit"]:
        flags.append("one_shot_trap_promoted")
    if case["id"] == "fuzzy_05" and not diagnostics["last_week_cues_hit"]:
        flags.append("last_week_anchor_missing")
    if case["id"] == "fuzzy_05" and diagnostics["outside_window_leak"] and not diagnostics["last_week_cues_hit"]:
        flags.append("outside_week_without_anchor")
    return {
        "case_id": case["id"],
        "query": case["query"],
        "gold_expect": case["gold_expect"],
        "passed_keyword": recall >= 0.8,
        "keyword_recall": round(recall, 4),
        "matched": matched,
        "missing": missing,
        "diagnostics": diagnostics,
        "flags": flags,
        "hits": [
            {
                "rank": index,
                "id": hit.get("id"),
                "memory_type": hit.get("memory_type"),
                "content": hit.get("content"),
                "score": hit.get("score"),
                "scene": hit.get("scene"),
                "created_at": hit.get("created_at"),
                "occurred_at": (hit.get("metadata") or {}).get("occurred_at") if isinstance(hit.get("metadata"), dict) else None,
            }
            for index, hit in enumerate(hits, 1)
        ],
        "profile": search_body.get("profile"),
    }


def add_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row["api_payload"])
    payload.setdefault("occupant_id", "primary")
    payload.setdefault("source", payload.get("source") or "synthetic_cockpit")
    payload.setdefault("infer", True)
    return payload


class CloudClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        import httpx

        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, Any]:
        resp = await self._client.get("/health")
        resp.raise_for_status()
        return resp.json()

    async def add(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = await self._client.post("/v1/memories", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def search(self, query: str, *, top_k: int) -> dict[str, Any]:
        resp = await self._client.post(
            "/v1/memories/search",
            json={"tenant_id": TENANT_ID, "user_id": USER_ID, "query": query, "top_k": top_k},
        )
        resp.raise_for_status()
        return resp.json()

    async def list_memories(self, *, limit: int = 500) -> dict[str, Any]:
        resp = await self._client.get(
            f"/v1/users/{USER_ID}/memories",
            params={"tenant_id": TENANT_ID, "limit": limit},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_profile(self) -> dict[str, Any] | None:
        resp = await self._client.get(
            f"/v1/users/{USER_ID}/profile",
            params={"tenant_id": TENANT_ID},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def delete_all(self) -> dict[str, Any]:
        resp = await self._client.delete(
            f"/v1/users/{USER_ID}/memories",
            params={"tenant_id": TENANT_ID, "confirm": "true"},
        )
        resp.raise_for_status()
        return resp.json()


async def import_rows(client: CloudClient, rows: list[dict[str, Any]], *, retries: int) -> dict[str, Any]:
    started = time.monotonic()
    succeeded = failed = new_memories = 0
    failures: list[dict[str, Any]] = []
    total = len(rows)
    for index, row in enumerate(rows, 1):
        payload = add_payload(row)
        last_error = ""
        ok = False
        body: dict[str, Any] = {}
        for attempt in range(1, retries + 1):
            try:
                body = await client.add(payload)
                added = body.get("memories") or []
                ok = True
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < retries:
                    await asyncio.sleep(min(2 * attempt, 6))
        if ok:
            succeeded += 1
            new_memories += len(added)
            extra = ""
            if isinstance(body.get("episode"), dict):
                extra = " episode=yes"
            print(
                f"[{index:03d}/{total:03d}] ✓ {row.get('started_at')} {row.get('scene')} "
                f"新增={len(added)} beliefs={body.get('beliefs_applied', '-')}{extra}"
            )
        else:
            failed += 1
            failure = {"session_id": row.get("session_id"), "error": last_error}
            failures.append(failure)
            print(f"[{index:03d}/{total:03d}] ✗ {failure['session_id']} {last_error}")
    return {
        "selected_sessions": total,
        "succeeded": succeeded,
        "failed": failed,
        "new_memories": new_memories,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "failures": failures[:20],
    }


async def run_search(client: CloudClient, *, top_k: int) -> dict[str, Any]:
    evaluations: list[dict[str, Any]] = []
    profile = None
    try:
        profile = await client.get_profile()
    except Exception as exc:
        profile = {"error": f"{type(exc).__name__}: {exc}"}
    for case in CASES:
        body = await client.search(case["query"], top_k=top_k)
        hits = body.get("memories") or []
        evaluation = evaluate_case(case, hits, body)
        evaluations.append(evaluation)
        mark = "通过" if evaluation["passed_keyword"] else "未通过"
        print(f"\n[{case['id']}] {mark}｜关键词召回率={evaluation['keyword_recall']:.0%}")
        print(f"用户：{case['query']}")
        print(f"Gold：{case['gold_expect']}")
        for hit in evaluation["hits"][:5]:
            score = hit["score"]
            score_text = "None" if score is None else f"{score:.4f}"
            mtype = hit.get("memory_type") or ""
            print(f"  Top{hit['rank']} {mtype} score={score_text}｜{hit['content']}")
        if evaluation["missing"]:
            print(f"  缺少关键词：{'、'.join(evaluation['missing'])}")
        if evaluation["flags"]:
            print(f"  红旗：{'、'.join(evaluation['flags'])}")
        diag = evaluation["diagnostics"]
        interesting = {key: value for key, value in diag.items() if value}
        if interesting:
            print(f"  诊断：{json.dumps(interesting, ensure_ascii=False)}")
    passed = sum(1 for item in evaluations if item["passed_keyword"])
    return {
        "passed_keyword": passed,
        "total": len(evaluations),
        "pass_rate": round(passed / len(evaluations), 4) if evaluations else 0.0,
        "profile_endpoint": profile,
        "evaluations": evaluations,
    }


def render_markdown(report: dict[str, Any]) -> str:
    search = report.get("search") or {}
    lines = [
        f"# 云端模糊记忆评测 — {report.get('label')}",
        "",
        f"- 时间：{report.get('recorded_at')}",
        f"- 目标：`{report.get('base_url')}`",
        f"- 健康检查：`{json.dumps(report.get('health'), ensure_ascii=False)}`",
        f"- 数据：`{report.get('dataset')}`",
        f"- profile：{report.get('profile')}，导入会话：{(report.get('import') or {}).get('selected_sessions', 0)}",
        f"- 关键词通过：{search.get('passed_keyword', 0)}/{search.get('total', 0)}",
        "",
        "## 评测逻辑（后续同一套复测）",
        "",
        "1. 只走 HTTP，不加载本地 `DesayMemory`，避免未上线代码污染结果。",
        "2. 合成用户固定 `oem_desay_demo` / `family_driver_001`。",
        "3. `--profile core` 只导入带习惯证据的会话（亲子/饮食/空气/西餐/相似餐厅）。",
        "4. 每条 gold query 做一次 `POST /v1/memories/search`。",
        "5. **关键词召回率 ≥ 80%** 记为数值通过，便于新旧版对比。",
        "6. 额外诊断不计入通过率，但必须记录：一次收藏是否被抬成习惯、上周锚点是否出现、窗外到访是否泄漏、是否返回 `profile`/`episodic_memory`。",
        "",
        "## 导入",
        "",
        f"```json\n{json.dumps(report.get('import') or report.get('validation'), ensure_ascii=False, indent=2)}\n```",
        "",
        "## 检索",
        "",
    ]
    for item in search.get("evaluations") or []:
        mark = "PASS" if item["passed_keyword"] else "FAIL"
        lines.append(f"### {item['case_id']} [{mark}] {item['query']}")
        lines.append("")
        lines.append(item["gold_expect"])
        lines.append("")
        lines.append(f"- 关键词召回率：{item['keyword_recall']:.0%}  命中：{item['matched']}  缺少：{item['missing']}")
        lines.append(f"- 红旗：{item['flags'] or '无'}")
        lines.append(f"- 诊断：`{json.dumps(item['diagnostics'], ensure_ascii=False)}`")
        lines.append("")
        for hit in item["hits"][:8]:
            content = (hit.get("content") or "").replace("\n", " ")
            lines.append(f"  {hit['rank']}. ({hit.get('memory_type')}, {hit.get('score')}) {content}")
        lines.append("")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloud HTTP year-long fuzzy memory eval")
    parser.add_argument("--mode", choices=("dry-run", "import", "search", "all"), default="dry-run")
    parser.add_argument("--profile", choices=("core", "full"), default="core")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--label", default="baseline_pre_layers")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--reset", action="store_true", help="导入前清空合成测试用户")
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    rows = load_jsonl(args.dataset)
    validation = validate_dataset(rows, args.gold)
    selected = select_rows(rows, args.profile, args.limit)
    print(json.dumps({**validation, "profile": args.profile, "selected_sessions": len(selected)}, ensure_ascii=False, indent=2))
    if not validation["valid"]:
        print("数据集校验失败，停止测试。", file=sys.stderr)
        return 2
    if args.mode == "dry-run":
        print("Dry-run 通过：未请求云端。")
        return 0

    client = CloudClient(args.base_url, timeout=args.timeout)
    report: dict[str, Any] = {
        "label": args.label,
        "recorded_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "base_url": args.base_url.rstrip("/"),
        "dataset": str(args.dataset),
        "gold": str(args.gold),
        "profile": args.profile,
        "validation": validation,
    }
    try:
        report["health"] = await client.health()
        print("健康检查：", json.dumps(report["health"], ensure_ascii=False))
        if args.mode in {"import", "all"}:
            if args.reset:
                deleted = await client.delete_all()
                print("已清空合成用户：", json.dumps(deleted, ensure_ascii=False))
                report["reset"] = deleted
            report["import"] = await import_rows(client, selected, retries=args.retries)
            listed = await client.list_memories()
            report["store_count"] = listed.get("count")
            print(f"当前该用户记忆条数：{report['store_count']}")
        if args.mode in {"search", "all"}:
            report["search"] = await run_search(client, top_k=args.top_k)
    finally:
        await client.close()

    args.report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = args.report_dir / f"{args.label}_{stamp}.json"
    md_path = args.report_dir / f"{args.label}_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    latest_json = args.report_dir / f"{args.label}_latest.json"
    latest_md = args.report_dir / f"{args.label}_latest.md"
    latest_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"\n报告：{md_path}")
    print(f"JSON：{json_path}")
    if report.get("import", {}).get("failed", 0):
        return 1
    search = report.get("search")
    if search and search["passed_keyword"] < search["total"]:
        return 1
    return 0


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
