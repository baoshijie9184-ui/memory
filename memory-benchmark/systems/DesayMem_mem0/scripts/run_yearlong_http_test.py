"""HTTP year-long fuzzy-memory eval against a live DesayMem API.

Records quantified metrics plus write/expect/actual recall contrasts:

  1. Store inventory — what the cloud actually wrote after import
  2. Expected facts — what gold says each query should surface
  3. Actual hits — what search returned (Top-K contents + labels)
  4. Metrics — fact recall, P@K, MRR, Top1, trap/leak rates

This script talks ONLY to HTTP. It does not import DesayMemory.

Examples:
    python scripts/run_yearlong_http_test.py --mode dry-run
    python scripts/run_yearlong_http_test.py --mode all --profile core --reset
    python scripts/run_yearlong_http_test.py --mode search --label baseline_pre_layers

Default target: http://47.115.228.135/memory
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
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

# Topic buckets used to inventory what was written into the store.
INVENTORY_TOPICS: tuple[dict[str, Any], ...] = (
    {"id": "tongmeng", "label": "童梦森林", "aliases": ("童梦森林",)},
    {"id": "maitian", "label": "麦田亲子", "aliases": ("麦田亲子", "麦田")},
    {"id": "xiaoxiang", "label": "小象花园", "aliases": ("小象花园", "小象")},
    {"id": "tongqudao", "label": "童趣岛(一次收藏)", "aliases": ("童趣岛",)},
    {"id": "diet_pref", "label": "饮食偏好(少油/香菜/清淡/粤菜)", "aliases": ("少油", "香菜", "清淡", "粤菜", "鲈鱼", "白灼虾", "酿豆腐")},
    {"id": "air_habit", "label": "空气老规矩", "aliases": ("内循环", "空气净化", "车窗", "3档", "3挡")},
    {"id": "western", "label": "西餐习惯", "aliases": ("西冷", "七分熟", "蘑菇汤", "柠檬水")},
    {"id": "last_week_anchor", "label": "上周锚点(8-29)", "aliases": ("2026-08-29", "8月29", "08-29", "08月29")},
    {"id": "commute_dest", "label": "通勤目的地(德赛科技园)", "aliases": ("德赛科技园",)},
    {"id": "commute_music", "label": "通勤音乐(轻音乐)", "aliases": ("通勤轻音乐",)},
    {"id": "ac_23", "label": "空调23度", "aliases": ("23度", "23℃", "23 C", "23°", "temperature_c=23")},
    {"id": "commute_route", "label": "通勤路线(最快/回家)", "aliases": ("最快", "fastest", "回家")},
)

# Gold-aware cases. must_facts drive the primary numeric score.
CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "fuzzy_01",
        "query": "我们经常去的亲子餐厅",
        "gold_expect": "高频到访应指向童梦森林；童趣岛只收藏过一次，不能当成经常去。",
        "expected_top_aliases": ("童梦森林",),
        "must_facts": (
            {"id": "habit_place", "label": "童梦森林（经常去）", "aliases": ("童梦森林",)},
        ),
        "should_facts": (
            {"id": "child_amenity", "label": "宝宝椅/儿童活动区", "aliases": ("宝宝椅", "儿童活动区")},
        ),
        "trap_facts": (
            {"id": "one_shot", "label": "童趣岛（仅收藏一次）", "aliases": ("童趣岛",)},
        ),
        "noise_facts": (
            {"id": "also_frequent", "label": "麦田/小象（也常去，但gold顶位是童梦）", "aliases": ("麦田亲子", "麦田", "小象花园", "小象")},
        ),
        "keywords": ("童梦森林", "亲子", "孩子"),
    },
    {
        "id": "fuzzy_02",
        "query": "根据我爱吃的菜，找个最近的地方",
        "gold_expect": "记忆只应给出饮食偏好；不能编造最近的具体餐厅。",
        "expected_top_aliases": ("少油", "香菜", "清淡", "粤菜", "鲈鱼", "白灼虾", "酿豆腐"),
        "must_facts": (
            {"id": "no_oil", "label": "少油", "aliases": ("少油",)},
            {"id": "no_cilantro", "label": "不要香菜", "aliases": ("香菜",)},
            {"id": "cantonese_or_light", "label": "粤菜/清淡", "aliases": ("粤菜", "清淡")},
            {"id": "favorite_dish", "label": "鲈鱼/白灼虾/酿豆腐", "aliases": ("鲈鱼", "白灼虾", "酿豆腐")},
        ),
        "should_facts": (),
        "trap_facts": (),
        "noise_facts": (
            {"id": "poi_distance", "label": "距离/餐厅POI（偏好缺失时属噪声）", "aliases": ("公里", "距离")},
        ),
        "keywords": ("粤菜", "少油", "香菜"),
    },
    {
        "id": "fuzzy_03",
        "query": "外面的空气质量不好，按老规矩给我调整一下",
        "gold_expect": "召回关窗+内循环+净化3档+23度这一组；记忆层不负责是否自动执行。",
        "expected_top_aliases": ("内循环", "空气净化", "车窗"),
        "must_facts": (
            {"id": "windows", "label": "关窗", "aliases": ("车窗", "关窗")},
            {"id": "recirc", "label": "内循环", "aliases": ("内循环", "internal")},
            {"id": "purifier_3", "label": "净化3档", "aliases": ("3档", "3挡", "level 3")},
            {"id": "climate_23", "label": "23度", "aliases": ("23度", "23℃", "23 C", "23°", "climate:23")},
        ),
        "should_facts": (),
        "trap_facts": (),
        "noise_facts": (),
        "keywords": ("车窗", "内循环", "空气净化", "3档", "23"),
    },
    {
        "id": "fuzzy_04",
        "query": "帮我找一下西餐厅，按照习惯进行点餐",
        "gold_expect": "召回固定套餐即可；未选店就声称点餐成功算硬失败（本脚本只测记忆召回）。",
        "expected_top_aliases": ("西冷", "七分熟"),
        "must_facts": (
            {"id": "steak", "label": "西冷牛排七分熟", "aliases": ("西冷", "七分熟")},
            {"id": "soup", "label": "蘑菇汤", "aliases": ("蘑菇汤",)},
            {"id": "lemon", "label": "柠檬水", "aliases": ("柠檬水",)},
        ),
        "should_facts": (
            {"id": "no_cilantro", "label": "不要香菜", "aliases": ("香菜",)},
        ),
        "trap_facts": (),
        "noise_facts": (),
        "keywords": ("西冷牛排", "七分熟", "蘑菇汤", "柠檬水"),
    },
    {
        "id": "fuzzy_05",
        "query": "根据上周的亲子餐厅，帮我预定一个差不多的餐厅",
        "gold_expect": "上周锚点是 2026-08-29 童梦森林；8-22 麦田不在该自然周；童趣岛是相似候选不是上周到访。",
        "expected_top_aliases": ("童梦森林", "2026-08-29", "8月29", "08-29"),
        "must_facts": (
            {"id": "last_week_place", "label": "上周到访=童梦森林", "aliases": ("童梦森林",)},
            {"id": "last_week_date", "label": "日期锚点 2026-08-29", "aliases": ("2026-08-29", "8月29", "08-29", "08月29")},
        ),
        "should_facts": (
            {"id": "similar_candidate", "label": "相似候选童趣岛", "aliases": ("童趣岛",)},
            {"id": "amenities", "label": "儿童活动区/宝宝椅", "aliases": ("儿童活动区", "宝宝椅")},
        ),
        "trap_facts": (),
        "noise_facts": (
            {"id": "outside_week", "label": "窗外到访(麦田/小象)", "aliases": ("麦田亲子", "麦田", "小象花园", "小象")},
        ),
        "keywords": ("2026-08-29", "童梦森林", "儿童活动区", "宝宝椅"),
    },
    # ── 常规画像查询 ──────────────────────────────────────────────
    # 考察精准画像召回：L3 profile 直读 + L1 证据命中
    {
        "id": "routine_01",
        "query": "导航去公司",
        "gold_expect": "通勤目的地是德赛科技园，走最快路线。",
        "expected_top_aliases": ("德赛科技园",),
        "must_facts": (
            {"id": "commute_dest", "label": "通勤目的地=德赛科技园", "aliases": ("德赛科技园",)},
        ),
        "should_facts": (
            {"id": "route_pref", "label": "路线偏好=最快", "aliases": ("最快", "fastest")},
        ),
        "trap_facts": (),
        "noise_facts": (
            {"id": "restaurant", "label": "餐厅(噪声)", "aliases": ("童梦森林", "麦田", "西餐厅")},
        ),
        "keywords": ("德赛科技园", "公司", "通勤"),
    },
    {
        "id": "routine_02",
        "query": "回家走老路线",
        "gold_expect": "回家路线是最快路线，通勤习惯。",
        "expected_top_aliases": ("回家", "最快"),
        "must_facts": (
            {"id": "home_route", "label": "回家路线=最快", "aliases": ("回家", "最快")},
        ),
        "should_facts": (),
        "trap_facts": (),
        "noise_facts": (
            {"id": "company", "label": "去公司(噪声)", "aliases": ("德赛科技园",)},
        ),
        "keywords": ("回家", "最快", "路线"),
    },
    {
        "id": "routine_03",
        "query": "空调调到平时的温度",
        "gold_expect": "通勤空调习惯是23度，风量2档。",
        "expected_top_aliases": ("23",),
        "must_facts": (
            {"id": "ac_temp", "label": "空调温度=23度", "aliases": ("23度", "23℃", "23 C", "23°", "temperature_c=23", "temperature:23")},
        ),
        "should_facts": (
            {"id": "ac_fan", "label": "风量=2档", "aliases": ("风量2", "fan_level=2", "fan:2", "2档")},
        ),
        "trap_facts": (),
        "noise_facts": (
            {"id": "air_quality", "label": "空气差车控(噪声)", "aliases": ("内循环", "空气净化", "AQI")},
        ),
        "keywords": ("空调", "23", "通勤"),
    },
    {
        "id": "routine_04",
        "query": "播放我最喜欢的音乐",
        "gold_expect": "通勤音乐习惯是通勤轻音乐，音量18。",
        "expected_top_aliases": ("通勤轻音乐",),
        "must_facts": (
            {"id": "music_playlist", "label": "播放列表=通勤轻音乐", "aliases": ("通勤轻音乐",)},
        ),
        "should_facts": (
            {"id": "music_volume", "label": "音量=18", "aliases": ("音量18", "volume=18", "volume:18")},
        ),
        "trap_facts": (),
        "noise_facts": (),
        "keywords": ("音乐", "通勤轻音乐", "播放"),
    },
    {
        "id": "routine_05",
        "query": "我平时爱吃什么菜",
        "gold_expect": "饮食偏好：清蒸鲈鱼/白灼虾/客家酿豆腐，少油、不要香菜、清淡口味。",
        "expected_top_aliases": ("鲈鱼", "白灼虾", "酿豆腐"),
        "must_facts": (
            {"id": "fav_dish", "label": "拿手菜=鲈鱼/白灼虾/酿豆腐", "aliases": ("鲈鱼", "白灼虾", "酿豆腐")},
            {"id": "no_oil", "label": "少油", "aliases": ("少油",)},
            {"id": "no_cilantro", "label": "不要香菜", "aliases": ("香菜",)},
        ),
        "should_facts": (
            {"id": "light_taste", "label": "清淡口味", "aliases": ("清淡",)},
            {"id": "cantonese", "label": "粤菜", "aliases": ("粤菜",)},
        ),
        "trap_facts": (),
        "noise_facts": (
            {"id": "western", "label": "西餐(噪声)", "aliases": ("西冷", "牛排", "蘑菇汤")},
        ),
        "keywords": ("鲈鱼", "白灼虾", "酿豆腐", "少油", "香菜"),
    },
    {
        "id": "routine_06",
        "query": "我西餐一般怎么点",
        "gold_expect": "西餐点餐习惯：西冷牛排七分熟、蘑菇汤、柠檬水不加冰、不要香菜。",
        "expected_top_aliases": ("西冷", "七分熟"),
        "must_facts": (
            {"id": "steak", "label": "西冷牛排七分熟", "aliases": ("西冷", "七分熟")},
            {"id": "soup", "label": "蘑菇汤", "aliases": ("蘑菇汤",)},
            {"id": "lemon", "label": "柠檬水不加冰", "aliases": ("柠檬水",)},
        ),
        "should_facts": (
            {"id": "no_cilantro", "label": "不要香菜", "aliases": ("香菜",)},
        ),
        "trap_facts": (),
        "noise_facts": (
            {"id": "cantonese", "label": "粤菜(噪声)", "aliases": ("清蒸鲈鱼", "白灼虾")},
        ),
        "keywords": ("西冷", "七分熟", "蘑菇汤", "柠檬水"),
    },
    {
        "id": "routine_07",
        "query": "空气差的时候怎么调",
        "gold_expect": "空气差老规矩：关窗、内循环、净化3档、空调23度。",
        "expected_top_aliases": ("内循环", "空气净化", "车窗"),
        "must_facts": (
            {"id": "windows", "label": "关窗", "aliases": ("车窗", "关窗")},
            {"id": "recirc", "label": "内循环", "aliases": ("内循环", "internal")},
            {"id": "purifier_3", "label": "净化3档", "aliases": ("3档", "3挡", "level 3")},
        ),
        "should_facts": (
            {"id": "climate_23", "label": "23度", "aliases": ("23度", "23℃", "23 C", "23°")},
        ),
        "trap_facts": (),
        "noise_facts": (
            {"id": "commute_music", "label": "通勤音乐(噪声)", "aliases": ("通勤轻音乐",)},
        ),
        "keywords": ("车窗", "内循环", "空气净化", "3档"),
    },
    {
        "id": "routine_08",
        "query": "孩子喜欢去哪吃饭",
        "gold_expect": "孩子喜欢去童梦森林亲子餐厅（高频），需要有宝宝椅和儿童活动区。",
        "expected_top_aliases": ("童梦森林",),
        "must_facts": (
            {"id": "kid_restaurant", "label": "童梦森林亲子餐厅", "aliases": ("童梦森林",)},
        ),
        "should_facts": (
            {"id": "child_amenity", "label": "宝宝椅/儿童活动区", "aliases": ("宝宝椅", "儿童活动区")},
            {"id": "kid_like", "label": "孩子喜欢", "aliases": ("孩子", "喜欢")},
        ),
        "trap_facts": (
            {"id": "one_shot", "label": "童趣岛（仅收藏一次）", "aliases": ("童趣岛",)},
        ),
        "noise_facts": (
            {"id": "western", "label": "西餐(噪声)", "aliases": ("西冷", "牛排")},
        ),
        "keywords": ("童梦森林", "亲子", "孩子"),
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


def _text_of(memory: dict[str, Any]) -> str:
    parts = [str(memory.get("content") or "")]
    meta = memory.get("metadata")
    if isinstance(meta, dict):
        parts.append(json.dumps(meta, ensure_ascii=False))
    return "\n".join(parts)


def _aliases_hit(text: str, aliases: Iterable[str]) -> list[str]:
    return [alias for alias in aliases if alias and alias in text]


def _fact_hit(text: str, fact: dict[str, Any]) -> list[str]:
    return _aliases_hit(text, fact.get("aliases") or ())


def _first_rank(hits: list[dict[str, Any]], fact: dict[str, Any]) -> int | None:
    for index, hit in enumerate(hits, 1):
        if _fact_hit(_text_of(hit), fact):
            return index
    return None


def _match_memories(memories: list[dict[str, Any]], aliases: Iterable[str]) -> list[dict[str, Any]]:
    alias_tuple = tuple(aliases)
    matched: list[dict[str, Any]] = []
    for memory in memories:
        if _aliases_hit(_text_of(memory), alias_tuple):
            matched.append(memory)
    return matched


def build_store_inventory(memories: list[dict[str, Any]]) -> dict[str, Any]:
    by_scene = Counter(str(item.get("scene") or "") for item in memories)
    by_type = Counter(str(item.get("memory_type") or "") for item in memories)
    topics: list[dict[str, Any]] = []
    for topic in INVENTORY_TOPICS:
        matched = _match_memories(memories, topic["aliases"])
        topics.append(
            {
                "id": topic["id"],
                "label": topic["label"],
                "count": len(matched),
                "sample_contents": [str(item.get("content") or "")[:160] for item in matched[:5]],
            }
        )
    return {
        "total": len(memories),
        "by_scene": dict(sorted(by_scene.items(), key=lambda item: (-item[1], item[0]))),
        "by_memory_type": dict(sorted(by_type.items(), key=lambda item: (-item[1], item[0]))),
        "topics": topics,
        "memories": [
            {
                "id": item.get("id"),
                "memory_type": item.get("memory_type"),
                "scene": item.get("scene"),
                "content": item.get("content"),
                "created_at": item.get("created_at"),
                "occurred_at": (item.get("metadata") or {}).get("occurred_at")
                if isinstance(item.get("metadata"), dict)
                else None,
            }
            for item in memories
        ],
    }


def evaluate_case(
    case: dict[str, Any],
    hits: list[dict[str, Any]],
    search_body: dict[str, Any],
    store_memories: list[dict[str, Any]],
) -> dict[str, Any]:
    combined = "\n".join(_text_of(hit) for hit in hits)
    top_content = str(hits[0].get("content") or "") if hits else ""
    k = len(hits)

    def fact_rows(facts: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for fact in facts:
            written = _match_memories(store_memories, fact.get("aliases") or ())
            hit_aliases = _fact_hit(combined, fact)
            rank = _first_rank(hits, fact)
            hit_contents = [
                {
                    "rank": index,
                    "id": hit.get("id"),
                    "content": hit.get("content"),
                    "score": hit.get("score"),
                }
                for index, hit in enumerate(hits, 1)
                if _fact_hit(_text_of(hit), fact)
            ]
            rows.append(
                {
                    "id": fact["id"],
                    "label": fact["label"],
                    "aliases": list(fact.get("aliases") or ()),
                    "written_count": len(written),
                    "written_samples": [str(item.get("content") or "")[:160] for item in written[:3]],
                    "retrieved": bool(hit_aliases),
                    "matched_aliases": hit_aliases,
                    "first_rank": rank,
                    "retrieved_hits": hit_contents[:5],
                }
            )
        return rows

    must_rows = fact_rows(case.get("must_facts") or ())
    should_rows = fact_rows(case.get("should_facts") or ())
    trap_rows = fact_rows(case.get("trap_facts") or ())
    noise_rows = fact_rows(case.get("noise_facts") or ())

    must_hit = sum(1 for row in must_rows if row["retrieved"])
    must_total = len(must_rows) or 0
    should_hit = sum(1 for row in should_rows if row["retrieved"])
    should_total = len(should_rows) or 0
    fact_recall = must_hit / must_total if must_total else 0.0
    should_recall = should_hit / should_total if should_total else None

    relevant_ranks: list[int] = []
    labeled_hits: list[dict[str, Any]] = []
    for index, hit in enumerate(hits, 1):
        text = _text_of(hit)
        labels: list[str] = []
        if any(_fact_hit(text, fact) for fact in case.get("must_facts") or ()):
            labels.append("MUST")
            relevant_ranks.append(index)
        if any(_fact_hit(text, fact) for fact in case.get("should_facts") or ()):
            labels.append("SHOULD")
        if any(_fact_hit(text, fact) for fact in case.get("trap_facts") or ()):
            labels.append("TRAP")
        if any(_fact_hit(text, fact) for fact in case.get("noise_facts") or ()):
            labels.append("NOISE")
        if not labels:
            labels.append("OTHER")
        labeled_hits.append(
            {
                "rank": index,
                "id": hit.get("id"),
                "memory_type": hit.get("memory_type"),
                "content": hit.get("content"),
                "score": hit.get("score"),
                "scene": hit.get("scene"),
                "created_at": hit.get("created_at"),
                "occurred_at": (hit.get("metadata") or {}).get("occurred_at")
                if isinstance(hit.get("metadata"), dict)
                else None,
                "labels": labels,
            }
        )

    precision_at_k = (len(relevant_ranks) / k) if k else 0.0
    first_relevant = relevant_ranks[0] if relevant_ranks else None
    mrr = (1.0 / first_relevant) if first_relevant else 0.0
    top1_expected = bool(hits) and bool(_aliases_hit(top_content, case.get("expected_top_aliases") or ()))
    trap_top1 = bool(hits) and any(_fact_hit(top_content, fact) for fact in case.get("trap_facts") or ())
    noise_in_topk = sum(1 for item in labeled_hits if "NOISE" in item["labels"] and "MUST" not in item["labels"])
    noise_ratio = (noise_in_topk / k) if k else 0.0

    keywords = tuple(case.get("keywords") or ())
    keyword_matched = _aliases_hit(combined, keywords)
    keyword_missing = [item for item in keywords if item not in keyword_matched]
    keyword_recall = len(keyword_matched) / len(keywords) if keywords else 0.0

    must_aliases = tuple(
        alias for fact in case.get("must_facts") or () for alias in (fact.get("aliases") or ())
    )
    written_must_rows = _match_memories(store_memories, must_aliases)
    retrieved_must_ids = {
        hit.get("id")
        for hit in hits
        if any(_fact_hit(_text_of(hit), fact) for fact in case.get("must_facts") or ())
    }
    store_coverage = (
        len({item.get("id") for item in written_must_rows if item.get("id") in retrieved_must_ids})
        / len(written_must_rows)
        if written_must_rows
        else None
    )

    flags: list[str] = []
    if trap_top1 and not top1_expected:
        flags.append("trap_promoted_to_top1")
    if case["id"] == "fuzzy_05" and not any(row["retrieved"] for row in must_rows if row["id"] == "last_week_date"):
        flags.append("last_week_anchor_missing")
    if case["id"] == "fuzzy_05" and noise_in_topk and first_relevant and first_relevant > 3:
        flags.append("outside_week_outranks_anchor")
    if written_must_rows and must_hit == 0:
        flags.append("written_but_not_retrieved")
    if must_total and must_hit < must_total and any(row["written_count"] == 0 for row in must_rows if not row["retrieved"]):
        flags.append("expected_never_written")

    metrics = {
        "top_k": k,
        "must_fact_recall": round(fact_recall, 4),
        "must_facts_hit": must_hit,
        "must_facts_total": must_total,
        "should_fact_recall": None if should_recall is None else round(should_recall, 4),
        "precision_at_k": round(precision_at_k, 4),
        "mrr": round(mrr, 4),
        "first_relevant_rank": first_relevant,
        "top1_expected": top1_expected,
        "trap_top1": trap_top1,
        "noise_ratio": round(noise_ratio, 4),
        "keyword_recall": round(keyword_recall, 4),
        "store_must_written": len(written_must_rows),
        "store_coverage_at_k": None if store_coverage is None else round(store_coverage, 4),
        "profile_attached": isinstance(search_body.get("profile"), dict),
        "episode_hits": sum(1 for hit in hits if hit.get("memory_type") == "episodic_memory"),
        "passed_fact": fact_recall >= 0.8,
        "passed_keyword": keyword_recall >= 0.8,
    }

    return {
        "case_id": case["id"],
        "query": case["query"],
        "gold_expect": case["gold_expect"],
        "metrics": metrics,
        "flags": flags,
        "expected": {
            "must": must_rows,
            "should": should_rows,
            "trap": trap_rows,
            "noise": noise_rows,
        },
        "actual_hits": labeled_hits,
        "keyword_matched": keyword_matched,
        "keyword_missing": keyword_missing,
        "profile": search_body.get("profile"),
        # legacy flat fields for older report readers
        "passed_keyword": metrics["passed_keyword"],
        "keyword_recall": metrics["keyword_recall"],
        "matched": keyword_matched,
        "missing": keyword_missing,
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
    written_log: list[dict[str, Any]] = []
    total = len(rows)
    for index, row in enumerate(rows, 1):
        payload = add_payload(row)
        last_error = ""
        ok = False
        body: dict[str, Any] = {}
        added: list[Any] = []
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
            written_log.append(
                {
                    "session_id": row.get("session_id"),
                    "started_at": row.get("started_at"),
                    "scene": row.get("scene"),
                    "tags": row.get("tags") or [],
                    "added_count": len(added),
                    "added": [
                        {
                            "id": item.get("id"),
                            "memory_type": item.get("memory_type"),
                            "content": item.get("content"),
                            "event": item.get("event"),
                        }
                        for item in added
                        if isinstance(item, dict)
                    ],
                    "beliefs_applied": body.get("beliefs_applied"),
                    "episode": body.get("episode"),
                }
            )
            extra = ""
            if isinstance(body.get("episode"), dict):
                extra = " episode=yes"
            print(
                f"[{index:03d}/{total:03d}] ✓ {row.get('started_at')} {row.get('scene')} "
                f"新增={len(added)} beliefs={body.get('beliefs_applied', '-')}{extra}"
            )
            for item in added[:3]:
                if isinstance(item, dict):
                    print(f"         → {item.get('content')}")
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
        "written_log": written_log,
    }


async def run_search(
    client: CloudClient,
    *,
    top_k: int,
    store_memories: list[dict[str, Any]],
) -> dict[str, Any]:
    evaluations: list[dict[str, Any]] = []
    profile = None
    try:
        profile = await client.get_profile()
    except Exception as exc:
        profile = {"error": f"{type(exc).__name__}: {exc}"}
    for case in CASES:
        body = await client.search(case["query"], top_k=top_k)
        hits = body.get("memories") or []
        evaluation = evaluate_case(case, hits, body, store_memories)
        evaluations.append(evaluation)
        metrics = evaluation["metrics"]
        mark = "通过" if metrics["passed_fact"] else "未通过"
        print(f"\n===== [{case['id']}] {mark} =====")
        print(f"1. 用户输入：{case['query']}")
        print("2. 实际返回：")
        for hit in evaluation["actual_hits"][:5]:
            score = hit["score"]
            score_text = "None" if score is None else f"{score:.4f}"
            labels = ",".join(hit["labels"])
            print(f"   Top{hit['rank']} [{labels}] score={score_text}｜{hit['content']}")
        print(f"3. 应该返回：{case['gold_expect']}")
        for row in evaluation["expected"]["must"]:
            status = f"命中@{row['first_rank']}" if row["retrieved"] else "未召回"
            written = f"库内{row['written_count']}条" if row["written_count"] else "库内未写入"
            print(f"   - must {row['label']}｜{written}｜{status}")
        print(
            f"4. 指标：must_recall={metrics['must_fact_recall']:.0%} "
            f"({metrics['must_facts_hit']}/{metrics['must_facts_total']})；"
            f"P@{metrics['top_k']}={metrics['precision_at_k']:.0%}；"
            f"MRR={metrics['mrr']:.2f}；Top1期望={metrics['top1_expected']}"
        )
        if evaluation["flags"]:
            print(f"   红旗：{'、'.join(evaluation['flags'])}")

    fact_passed = sum(1 for item in evaluations if item["metrics"]["passed_fact"])
    keyword_passed = sum(1 for item in evaluations if item["metrics"]["passed_keyword"])
    aggregate = {
        "cases": len(evaluations),
        "fact_pass": fact_passed,
        "fact_pass_rate": round(fact_passed / len(evaluations), 4) if evaluations else 0.0,
        "keyword_pass": keyword_passed,
        "keyword_pass_rate": round(keyword_passed / len(evaluations), 4) if evaluations else 0.0,
        "macro_must_recall": round(
            sum(item["metrics"]["must_fact_recall"] for item in evaluations) / len(evaluations), 4
        )
        if evaluations
        else 0.0,
        "macro_precision_at_k": round(
            sum(item["metrics"]["precision_at_k"] for item in evaluations) / len(evaluations), 4
        )
        if evaluations
        else 0.0,
        "macro_mrr": round(sum(item["metrics"]["mrr"] for item in evaluations) / len(evaluations), 4)
        if evaluations
        else 0.0,
        "top1_hit_rate": round(
            sum(1 for item in evaluations if item["metrics"]["top1_expected"]) / len(evaluations), 4
        )
        if evaluations
        else 0.0,
    }
    return {
        "aggregate": aggregate,
        "passed_keyword": keyword_passed,
        "total": len(evaluations),
        "pass_rate": aggregate["keyword_pass_rate"],
        "profile_endpoint": profile,
        "evaluations": evaluations,
    }


def _pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.0%}"


def _explain_case_metrics(item: dict[str, Any]) -> list[str]:
    """Explain metric formulas with this case's plugged-in numbers."""
    m = item.get("metrics") or {}
    k = m.get("top_k") or 0
    must_hit = m.get("must_facts_hit", 0)
    must_total = m.get("must_facts_total", 0)
    first = m.get("first_relevant_rank")
    relevant = sum(1 for hit in item.get("actual_hits") or [] if "MUST" in (hit.get("labels") or []))
    noise_only = sum(
        1
        for hit in item.get("actual_hits") or []
        if "NOISE" in (hit.get("labels") or []) and "MUST" not in (hit.get("labels") or [])
    )
    keyword_matched = item.get("keyword_matched") or []
    keyword_missing = item.get("keyword_missing") or []
    keyword_total = len(keyword_matched) + len(keyword_missing)
    cov = m.get("store_coverage_at_k")
    store_must = m.get("store_must_written", 0)
    lines = [
        "#### 指标怎么算（本题代入）",
        "",
        f"- **must_fact_recall** = 命中的 must 事实数 / must 事实总数 "
        f"= {must_hit}/{must_total} = {_pct(m.get('must_fact_recall'))}；"
        f" ≥80% 记通过 → {'PASS' if m.get('passed_fact') else 'FAIL'}",
        f"- **P@{k} (precision_at_k)** = Top-{k} 中带 `MUST` 标签的条数 / {k} "
        f"= {relevant}/{k} = {_pct(m.get('precision_at_k'))}",
        f"- **MRR** = 1 / 第一条 `MUST` 命中名次"
        + (f" = 1/{first} = {m.get('mrr')}" if first else " = 0（Top-K 内无 MUST）"),
        f"- **Top1期望** = Top1 文本是否包含期望顶位别名 → "
        f"{'是' if m.get('top1_expected') else '否'}",
        f"- **noise_ratio** = 仅含 `NOISE`、不含 `MUST` 的条数 / {k} "
        f"= {noise_only}/{k} = {_pct(m.get('noise_ratio'))}",
        f"- **keyword_recall**（旧口径）= 命中关键词数 / 关键词总数 "
        f"= {len(keyword_matched)}/{keyword_total or 0} = {_pct(m.get('keyword_recall'))}"
        + (f"；缺少：{keyword_missing}" if keyword_missing else ""),
        f"- **store_coverage_at_k** = 库内匹配 must 的记忆里，有多少出现在本次 Top-{k} "
        f"= {store_must} 条库内 must 相关 → 覆盖 {_pct(cov) if cov is not None else '-'}",
        f"- 标签含义：`MUST`=命中应召回事实；`SHOULD`=加分项；`TRAP`=陷阱；"
        f"`NOISE`=干扰项；`OTHER`=未归类",
    ]
    if item.get("flags"):
        lines.append(f"- 红旗：{', '.join(item['flags'])}")
    lines.append("")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    search = report.get("search") or {}
    aggregate = search.get("aggregate") or {}
    store = report.get("store") or {}
    lines = [
        f"# 云端模糊记忆评测 — {report.get('label')}",
        "",
        f"- 时间：{report.get('recorded_at')}",
        f"- 目标：`{report.get('base_url')}`",
        f"- 健康检查：`{json.dumps(report.get('health'), ensure_ascii=False)}`",
        f"- 数据：`{report.get('dataset')}`",
        f"- profile：{report.get('profile')}，导入会话：{(report.get('import') or {}).get('selected_sessions', store.get('import_sessions', '-'))}",
        f"- 库内记忆：{store.get('total', report.get('store_count', '-'))}",
        "",
        "## 指标定义（全局）",
        "",
        "| 指标 | 公式 | 用途 |",
        "| --- | --- | --- |",
        "| must_fact_recall | 命中的 must 事实数 / must 事实总数 | **主通过线**，≥80% 记 PASS |",
        "| P@K | Top-K 中带 MUST 的条数 / K | 看返回列表里有多少是真正相关的 |",
        "| MRR | 1 / 第一条 MUST 的名次 | 看正确答案排得多靠前 |",
        "| Top1期望 | Top1 是否命中 expected_top 别名 | 看第一名对不对 |",
        "| store_coverage_at_k | 库内 must 相关记忆出现在 Top-K 的比例 | 区分「没写入」vs「写入了没召回」 |",
        "| keyword_recall | 关键词命中数 / 关键词总数 | 兼容旧口径，不作主结论 |",
        "",
        f"- 本题汇总：must 通过 **{aggregate.get('fact_pass', 0)}/{aggregate.get('cases', 0)}** "
        f"({_pct(aggregate.get('fact_pass_rate'))})；"
        f"macro must_recall={_pct(aggregate.get('macro_must_recall'))}；"
        f"macro P@K={_pct(aggregate.get('macro_precision_at_k'))}；"
        f"macro MRR={aggregate.get('macro_mrr', 0):.3f}；"
        f"Top1命中率={_pct(aggregate.get('top1_hit_rate'))}",
        "",
        "## 库内写入摘要（检索前已存在什么）",
        "",
    ]
    if store:
        lines.append(f"- 总数：{store.get('total')}")
        lines.append(f"- 按 scene：`{json.dumps(store.get('by_scene') or {}, ensure_ascii=False)}`")
        lines.append(f"- 按 memory_type：`{json.dumps(store.get('by_memory_type') or {}, ensure_ascii=False)}`")
        lines.append("")
        lines.append("| 主题 | 条数 | 样例 |")
        lines.append("| --- | ---: | --- |")
        for topic in store.get("topics") or []:
            sample = " / ".join((topic.get("sample_contents") or [])[:2]).replace("|", "\\|")
            lines.append(f"| {topic.get('label')} | {topic.get('count')} | {sample[:120]} |")
        lines.append("")
    else:
        lines.append("_本次未拉取库内清单。_")
        lines.append("")

    if report.get("import"):
        imp = dict(report["import"])
        written_log = imp.pop("written_log", None)
        lines.extend(
            [
                "## 导入统计",
                "",
                f"```json\n{json.dumps(imp, ensure_ascii=False, indent=2)}\n```",
                "",
            ]
        )
        if written_log is not None:
            lines.append(f"_逐条写入日志共 {len(written_log)} 个会话，见 JSON `import.written_log`。_")
            lines.append("")

    lines.append("## 逐题记录：用户输入 → 实际返回 → 应该返回")
    lines.append("")
    lines.append("| 用例 | 用户输入 | must召回 | P@K | MRR | Top1 |")
    lines.append("| --- | --- | ---: | ---: | ---: | --- |")
    for item in search.get("evaluations") or []:
        m = item.get("metrics") or {}
        lines.append(
            f"| {item['case_id']} | {item['query']} | "
            f"{_pct(m.get('must_fact_recall'))} ({m.get('must_facts_hit')}/{m.get('must_facts_total')}) | "
            f"{_pct(m.get('precision_at_k'))} | {m.get('mrr', 0):.2f} | "
            f"{'Y' if m.get('top1_expected') else 'N'} |"
        )
    lines.append("")

    for item in search.get("evaluations") or []:
        m = item.get("metrics") or {}
        mark = "PASS" if m.get("passed_fact") else "FAIL"
        lines.append(f"### {item['case_id']} [{mark}]")
        lines.append("")
        lines.append("#### 1. 用户输入")
        lines.append("")
        lines.append(f"> {item['query']}")
        lines.append("")
        lines.append("#### 2. 实际返回（系统检索 Top-K）")
        lines.append("")
        actual = item.get("actual_hits") or []
        if not actual:
            lines.append("_空结果_")
        else:
            for hit in actual:
                content = (hit.get("content") or "").replace("\n", " ")
                labels = ",".join(hit.get("labels") or [])
                score = hit.get("score")
                score_text = "None" if score is None else f"{float(score):.4f}"
                lines.append(
                    f"{hit['rank']}. **[{labels}]** score={score_text}  "
                    f"`{hit.get('memory_type')}`  {content}"
                )
        lines.append("")
        lines.append("#### 3. 应该返回（gold 期望）")
        lines.append("")
        lines.append(item.get("gold_expect") or "")
        lines.append("")
        lines.append("| 优先级 | 期望事实 | 库内是否已写入 | 实际是否召回 | 最高名次 | 对照说明 |")
        lines.append("| --- | --- | ---: | --- | ---: | --- |")
        kind_title = {
            "must": "必须召回",
            "should": "最好有",
            "trap": "不该当顶位",
            "noise": "干扰项",
        }
        for kind in ("must", "should", "trap", "noise"):
            for row in (item.get("expected") or {}).get(kind) or []:
                written = row.get("written_count") or 0
                written_text = f"有 {written} 条" if written else "**未写入**"
                retrieved = "已召回" if row.get("retrieved") else "**未召回**"
                rank = row.get("first_rank") if row.get("first_rank") is not None else "-"
                if kind == "must" and written == 0:
                    note = "写入侧缺口：库里没有，检索不可能召回"
                elif kind == "must" and not row.get("retrieved"):
                    note = "写入了但没召回：检索/排序问题"
                elif kind == "must" and row.get("first_rank") and row["first_rank"] > 1:
                    note = f"召回了但排在第 {row['first_rank']}，Top1 不是它"
                elif kind == "trap" and row.get("first_rank") == 1:
                    note = "陷阱被抬到 Top1"
                elif kind == "noise" and row.get("first_rank") == 1:
                    note = "干扰项占了 Top1"
                elif kind == "should" and row.get("retrieved"):
                    note = "加分项已出现"
                else:
                    sample = " / ".join(row.get("written_samples") or [])[:80]
                    note = sample or "-"
                note = note.replace("|", "\\|")
                lines.append(
                    f"| {kind_title[kind]} | {row.get('label')} | {written_text} | "
                    f"{retrieved} | {rank} | {note} |"
                )
        lines.append("")
        lines.extend(_explain_case_metrics(item))
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloud HTTP year-long fuzzy memory eval")
    parser.add_argument(
        "--mode",
        choices=("dry-run", "import", "search", "all", "render"),
        default="dry-run",
        help="render=仅用已有 JSON 重写 Markdown，不访问云端",
    )
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
    parser.add_argument(
        "--from-json",
        type=Path,
        default=None,
        help="render 模式读取的报告 JSON（默认 {report-dir}/{label}_latest.json）",
    )
    return parser.parse_args()


def write_reports(report: dict[str, Any], report_dir: Path, label: str) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"{label}_{stamp}.json"
    md_path = report_dir / f"{label}_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    latest_json = report_dir / f"{label}_latest.json"
    latest_md = report_dir / f"{label}_latest.md"
    latest_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    return md_path, json_path


async def async_main(args: argparse.Namespace) -> int:
    if args.mode == "render":
        source = args.from_json or (args.report_dir / f"{args.label}_latest.json")
        if not source.exists():
            print(f"找不到报告 JSON：{source}", file=sys.stderr)
            return 2
        report = json.loads(source.read_text(encoding="utf-8-sig"))
        md_path, json_path = write_reports(report, args.report_dir, args.label)
        print(f"已按「输入→实际→期望→指标」重写报告：{md_path}")
        print(f"JSON：{json_path}")
        return 0

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
        "metric_definitions": {
            "must_fact_recall": "命中的 must 事实数 / must 事实总数",
            "precision_at_k": "Top-K 中带 MUST 标签的条数 / K",
            "mrr": "1 / 第一条 MUST 命中的名次",
            "top1_expected": "Top1 文本是否包含 expected_top_aliases",
            "store_coverage_at_k": "库内匹配 must 的记忆中，有多少出现在本次 Top-K",
            "passed_fact": "must_fact_recall >= 0.8",
        },
    }
    store_memories: list[dict[str, Any]] = []
    try:
        report["health"] = await client.health()
        print("健康检查：", json.dumps(report["health"], ensure_ascii=False))
        if args.mode in {"import", "all"}:
            if args.reset:
                deleted = await client.delete_all()
                print("已清空合成用户：", json.dumps(deleted, ensure_ascii=False))
                report["reset"] = deleted
            report["import"] = await import_rows(client, selected, retries=args.retries)
        if args.mode in {"import", "search", "all"}:
            listed = await client.list_memories(limit=500)
            store_memories = listed.get("memories") or []
            report["store_count"] = listed.get("count", len(store_memories))
            report["store"] = build_store_inventory(store_memories)
            print(f"当前该用户记忆条数：{report['store_count']}")
            for topic in report["store"]["topics"]:
                print(f"  写入主题 {topic['label']}: {topic['count']}")
        if args.mode in {"search", "all"}:
            report["search"] = await run_search(client, top_k=args.top_k, store_memories=store_memories)
    finally:
        await client.close()

    md_path, json_path = write_reports(report, args.report_dir, args.label)
    print(f"\n报告：{md_path}")
    print(f"JSON：{json_path}")
    if report.get("import", {}).get("failed", 0):
        return 1
    search = report.get("search")
    if search:
        aggregate = search.get("aggregate") or {}
        if aggregate.get("fact_pass", 0) < aggregate.get("cases", 0):
            return 1
    return 0


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
