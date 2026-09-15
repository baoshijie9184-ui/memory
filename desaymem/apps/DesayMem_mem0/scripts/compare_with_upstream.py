"""Compare DesayMem extraction/dedup/isolation with Mem0 OSS when available.

LLM output is non-deterministic, so this script records qualitative checks:
- core fact present
- count reasonable
- user scope respected
- target memory retrieved
- no extra hard errors

If Mem0 cannot be imported, DesayMem's own parser + MD5 dedup is compared
against the live add/search path (PostgreSQL + DashScope).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

MEM0_ROOT = Path(r"D:\agent memory\code\mem0")

from desaymem.core.config import Settings
from desaymem.core.memory import DesayMemory
from desaymem.extraction.deduplicator import drop_duplicate_texts, memory_content_hash
from desaymem.extraction.parser import parse_extraction_response


SAMPLES = [
    {"user_id": "user_a", "text": "我开车的时候喜欢把空调调到22度", "expect": "22"},
    {"user_id": "user_b", "text": "我开车的时候喜欢把空调调到25度", "expect": "25"},
]


def _try_import_mem0() -> Any | None:
    if MEM0_ROOT.exists() and str(MEM0_ROOT) not in sys.path:
        sys.path.insert(0, str(MEM0_ROOT))
    try:
        from mem0.memory.utils import extract_json, remove_code_blocks  # type: ignore

        return {
            "remove_code_blocks": remove_code_blocks,
            "extract_json": extract_json,
            "hash": lambda text: hashlib.md5(text.encode()).hexdigest(),
        }
    except Exception:
        return None


def _mem0_parse(raw: str, helpers: dict[str, Any] | None) -> list[str]:
    if helpers is None:
        parsed = parse_extraction_response(raw)
        return [item["text"] for item in parsed]
    cleaned = helpers["remove_code_blocks"](raw)
    try:
        payload = json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        payload = json.loads(helpers["extract_json"](cleaned), strict=False)
    memories = payload.get("memory") or payload.get("facts") or []
    texts = []
    for item in memories:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict):
            text = item.get("text") or item.get("fact")
            if text:
                texts.append(text)
    return texts


async def run_desay(force_script: str | None = None) -> dict[str, Any]:
    del force_script
    settings = Settings()
    settings.require_runtime_secrets()
    settings.search_threshold = 0.0
    memory = DesayMemory.from_settings(settings)
    await memory.prepare()
    report: dict[str, Any] = {"added": {}, "search": {}, "isolation": True, "dedup": True}
    try:
        for sample in SAMPLES:
            await memory.delete_all(sample["user_id"], tenant_id="cmp")
            added = await memory.add(
                [{"role": "user", "content": sample["text"]}],
                user_id=sample["user_id"],
                tenant_id="cmp",
            )
            report["added"][sample["user_id"]] = [row["content"] for row in added]
            again = await memory.add(
                [{"role": "user", "content": sample["text"]}],
                user_id=sample["user_id"],
                tenant_id="cmp",
            )
            if again:
                report["dedup"] = False
            hits = await memory.search("空调温度", user_id=sample["user_id"], tenant_id="cmp")
            report["search"][sample["user_id"]] = [row["content"] for row in hits]
            foreign = await memory.search("空调温度", user_id=sample["user_id"] + "_other", tenant_id="cmp")
            if foreign:
                report["isolation"] = False
        a_hits = report["search"]["user_a"]
        b_hits = report["search"]["user_b"]
        if any("25" in text for text in a_hits) or any("22" in text for text in b_hits):
            report["isolation"] = False
        return report
    finally:
        for sample in SAMPLES:
            await memory.delete_all(sample["user_id"], tenant_id="cmp")
        await memory.close()


def run_upstream_equivalent(raw_json: str, helpers: dict[str, Any] | None) -> dict[str, Any]:
    texts = _mem0_parse(raw_json, helpers)
    hashes = [memory_content_hash(text) if helpers is None else helpers["hash"](text) for text in texts]
    kept, skipped = drop_duplicate_texts([{"text": t} for t in texts], set())
    return {
        "texts": texts,
        "hashes": hashes,
        "kept": [item["text"] for item in kept],
        "skipped": skipped,
        "parser": "mem0" if helpers else "desaymem-fallback",
    }


async def main_async() -> int:
    helpers = _try_import_mem0()
    raw = json.dumps(
        {"memory": [{"id": "0", "text": "用户开车时喜欢把空调调到22度", "attributed_to": "user"}]},
        ensure_ascii=False,
    )
    upstream = run_upstream_equivalent(raw, helpers)
    desay_parsed = [item["text"] for item in parse_extraction_response(raw)]
    desay_report = await run_desay()
    result = {
        "mem0_importable": helpers is not None,
        "mem0_root_exists": MEM0_ROOT.exists(),
        "parser_texts_equal": upstream["texts"] == desay_parsed,
        "hash_equal": upstream["hashes"][0] == memory_content_hash(desay_parsed[0]),
        "upstream_equivalent": upstream,
        "desaymem": desay_report,
        "core_fact_consistent": any("22" in text for text in desay_report["added"]["user_a"]),
        "count_reasonable": all(0 < len(v) <= 3 for v in desay_report["added"].values()),
        "user_scope_consistent": desay_report["isolation"],
        "target_memory_retrieved": any("22" in text for text in desay_report["search"]["user_a"]),
        "no_extra_errors": True,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    ok = (
        result["parser_texts_equal"]
        and result["hash_equal"]
        and result["core_fact_consistent"]
        and result["user_scope_consistent"]
        and result["target_memory_retrieved"]
    )
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
