"""Import the year-long cockpit dataset and evaluate fuzzy memory retrieval.

Examples:
    python scripts/run_yearlong_memory_test.py --mode dry-run
    python scripts/run_yearlong_memory_test.py --mode import --profile core
    python scripts/run_yearlong_memory_test.py --mode search
    python scripts/run_yearlong_memory_test.py --mode all --profile core --top-k 10
    python scripts/run_yearlong_memory_test.py --mode import --profile full

Run from the DesayMem_mem0 project root. Live import/search modes use the
configured LLM, embedding provider, PostgreSQL/pgvector and SQLite history.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_DATASET = ROOT / "data" / "yearlong_cockpit_sessions.jsonl"
DEFAULT_GOLD = ROOT / "data" / "yearlong_fuzzy_memory_gold.json"
DEFAULT_REPORT = ROOT / "data" / "yearlong_memory_test_report.json"

CORE_TAGS = {
    "亲子餐厅",
    "饮食偏好",
    "空气差老规矩",
    "西餐习惯",
    "相似亲子餐厅",
}


@dataclass(frozen=True)
class TestCase:
    case_id: str
    query: str
    keywords: tuple[str, ...]


TEST_CASES = (
    TestCase("fuzzy_01", "我们经常去的亲子餐厅", ("童梦森林", "亲子", "孩子")),
    TestCase("fuzzy_02", "根据我爱吃的菜，找个最近的地方", ("粤菜", "少油", "香菜")),
    TestCase("fuzzy_03", "外面的空气质量不好，按老规矩给我调整一下", ("车窗", "内循环", "空气净化", "3档", "23")),
    TestCase("fuzzy_04", "帮我找一下西餐厅，按照习惯进行点餐", ("西冷牛排", "七分熟", "蘑菇汤", "柠檬水")),
    TestCase("fuzzy_05", "根据上周的亲子餐厅，帮我预定一个差不多的餐厅", ("2026-08-29", "童梦森林", "儿童活动区", "宝宝椅")),
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
    }


async def import_rows(
    memory: DesayMemory,
    rows: list[dict[str, Any]],
    *,
    infer: bool,
    continue_on_error: bool,
) -> dict[str, Any]:
    started = time.monotonic()
    succeeded = failed = new_memories = 0
    failures: list[dict[str, Any]] = []
    total = len(rows)
    for index, row in enumerate(rows, 1):
        payload = row["api_payload"]
        try:
            added = await memory.add(
                payload["messages"],
                user_id=payload["user_id"],
                tenant_id=payload["tenant_id"],
                vehicle_id=payload.get("vehicle_id", ""),
                occupant_id=payload.get("occupant_id", "primary"),
                session_id=payload.get("session_id", ""),
                scene=payload.get("scene", ""),
                source=payload.get("source", "synthetic_cockpit"),
                metadata=payload.get("metadata") or {},
                infer=infer,
            )
            succeeded += 1
            new_memories += len(added)
            print(f"[{index:03d}/{total:03d}] ✓ {row['started_at']} {row['scene']} 新增记忆={len(added)}")
        except Exception as exc:
            failed += 1
            failure = {"session_id": row.get("session_id"), "error": f"{type(exc).__name__}: {exc}"}
            failures.append(failure)
            print(f"[{index:03d}/{total:03d}] ✗ {failure['session_id']} {failure['error']}")
            if not continue_on_error:
                raise
    return {
        "selected_sessions": total,
        "succeeded": succeeded,
        "failed": failed,
        "new_memories": new_memories,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "failures": failures,
    }


def evaluate_hits(case: TestCase, hits: list[dict[str, Any]]) -> dict[str, Any]:
    combined = "\n".join(str(hit.get("content") or "") for hit in hits)
    matched = [keyword for keyword in case.keywords if keyword in combined]
    missing = [keyword for keyword in case.keywords if keyword not in combined]
    recall = len(matched) / len(case.keywords)
    return {
        "case_id": case.case_id,
        "query": case.query,
        "passed": recall >= 0.8,
        "keyword_recall": round(recall, 4),
        "matched": matched,
        "missing": missing,
        "hits": [
            {
                "rank": index,
                "content": hit.get("content"),
                "score": hit.get("score"),
                "scene": hit.get("scene"),
                "created_at": hit.get("created_at"),
                "occurred_at": (hit.get("metadata") or {}).get("occurred_at"),
            }
            for index, hit in enumerate(hits, 1)
        ],
    }


async def run_search(memory: DesayMemory, *, top_k: int, threshold: float | None) -> dict[str, Any]:
    evaluations: list[dict[str, Any]] = []
    for case in TEST_CASES:
        hits = await memory.search(
            case.query,
            user_id="family_driver_001",
            tenant_id="oem_desay_demo",
            top_k=top_k,
            threshold=threshold,
        )
        evaluation = evaluate_hits(case, hits)
        evaluations.append(evaluation)
        mark = "通过" if evaluation["passed"] else "未通过"
        print(f"\n[{case.case_id}] {mark}｜关键词召回率={evaluation['keyword_recall']:.0%}")
        print(f"用户：{case.query}")
        for hit in evaluation["hits"]:
            score = hit["score"]
            score_text = "None" if score is None else f"{score:.4f}"
            print(f"  Top{hit['rank']} score={score_text}｜{hit['content']}")
        if evaluation["missing"]:
            print(f"  缺少：{'、'.join(evaluation['missing'])}")
    passed = sum(1 for item in evaluations if item["passed"])
    return {
        "passed": passed,
        "total": len(evaluations),
        "pass_rate": round(passed / len(evaluations), 4),
        "evaluations": evaluations,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DesayMem 近一年模糊记忆导入与检索测试")
    parser.add_argument("--mode", choices=("dry-run", "import", "search", "all"), default="dry-run")
    parser.add_argument("--profile", choices=("core", "full"), default="core",
                        help="core只导入关键模糊场景；full导入全部495次会话")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--threshold", type=float, default=0.0)
    parser.add_argument("--raw", action="store_true",
                        help="使用infer=False做原文入库冒烟测试；正式模糊记忆测试不要开启")
    parser.add_argument("--reset", action="store_true",
                        help="导入前清空该合成用户的全部记忆（危险操作，默认关闭）")
    parser.add_argument("--stop-on-error", action="store_true")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit 必须大于0")
    if not 1 <= args.top_k <= 50:
        parser.error("--top-k 必须在1到50之间")
    return args


async def async_main(args: argparse.Namespace) -> int:
    rows = load_jsonl(args.dataset)
    validation = validate_dataset(rows, args.gold)
    selected = select_rows(rows, args.profile, args.limit)
    print(json.dumps({**validation, "profile": args.profile, "selected_sessions": len(selected)}, ensure_ascii=False, indent=2))
    if not validation["valid"]:
        print("数据集校验失败，停止测试。", file=sys.stderr)
        return 2
    if args.mode == "dry-run":
        print("Dry-run通过：未连接模型和数据库，也未写入任何记忆。")
        return 0

    # Lazy import keeps --mode dry-run usable before project dependencies are
    # installed, while live modes still fail clearly on missing dependencies.
    from desaymem.core.config import Settings
    from desaymem.core.memory import DesayMemory

    settings = Settings()
    settings.search_threshold = args.threshold
    memory = DesayMemory.from_settings(settings)
    report: dict[str, Any] = {"validation": validation, "profile": args.profile}
    try:
        await memory.prepare()
        print("后端：", json.dumps(memory.backend_info(), ensure_ascii=False))
        if args.mode in {"import", "all"}:
            if args.reset:
                deleted = await memory.delete_all("family_driver_001", tenant_id="oem_desay_demo")
                print(f"已清空合成测试用户记忆：{deleted} 条")
            report["import"] = await import_rows(
                memory,
                selected,
                infer=not args.raw,
                continue_on_error=not args.stop_on_error,
            )
        if args.mode in {"search", "all"}:
            report["search"] = await run_search(memory, top_k=args.top_k, threshold=args.threshold)
    finally:
        await memory.close()

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n测试报告：{args.report}")
    if report.get("import", {}).get("failed", 0):
        return 1
    if report.get("search") and report["search"]["passed"] < report["search"]["total"]:
        return 1
    return 0


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
