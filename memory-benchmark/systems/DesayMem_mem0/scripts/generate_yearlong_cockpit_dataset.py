"""Generate a deterministic one-year cockpit memory benchmark.

The JSONL output models complete vehicle sessions (ignition on -> dialogue and
operations -> ignition off).  Repeated evidence is intentional: it allows a
memory system to infer habits instead of memorising a single statement.

Usage:
    python scripts/generate_yearlong_cockpit_dataset.py
    python scripts/generate_yearlong_cockpit_dataset.py --end-date 2026-09-02 --seed 20260902
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

TZ_CN = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "yearlong_cockpit_sessions.jsonl"
DEFAULT_GOLD = ROOT / "data" / "yearlong_fuzzy_memory_gold.json"

TENANT_ID = "oem_desay_demo"
USER_ID = "family_driver_001"
VEHICLE_ID = "vehicle_demo_001"


def _dt(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), TZ_CN)


def _operation(action: str, **parameters: Any) -> dict[str, Any]:
    return {"action": action, "parameters": parameters, "status": "success"}


def _session(
    seq: int,
    started_at: datetime,
    scene: str,
    messages: list[dict[str, str]],
    operations: list[dict[str, Any]],
    *,
    passengers: list[str] | None = None,
    weather: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    ended_at = started_at + timedelta(minutes=max(18, 8 + len(operations) * 7))
    session_id = f"drive_{started_at:%Y%m%d_%H%M}_{seq:04d}"
    dated_messages = []
    for message in messages:
        row = dict(message)
        # Current API cannot override memory_items.created_at. Keeping the
        # historical date in content makes temporal evidence available today.
        if row["role"] == "user":
            row["content"] = f"[{started_at:%Y-%m-%d %H:%M}] {row['content']}"
        dated_messages.append(row)
    return {
        "record_type": "cockpit_session",
        "tenant_id": TENANT_ID,
        "user_id": USER_ID,
        "vehicle_id": VEHICLE_ID,
        "session_id": session_id,
        "scene": scene,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "lifecycle": [
            {"event": "ignition_on", "occurred_at": started_at.isoformat()},
            {"event": "ignition_off", "occurred_at": ended_at.isoformat()},
        ],
        "occupants": ["driver"] + (passengers or []),
        "weather": weather or {},
        "messages": dated_messages,
        "operations": operations,
        "tags": tags or [],
        "api_payload": {
            "tenant_id": TENANT_ID,
            "user_id": USER_ID,
            "vehicle_id": VEHICLE_ID,
            "session_id": session_id,
            "scene": scene,
            "source": "synthetic_cockpit",
            "messages": dated_messages,
            "metadata": {
                "occurred_at": started_at.isoformat(),
                "ended_at": ended_at.isoformat(),
                "operations": operations,
                "tags": tags or [],
                "synthetic": True,
            },
            "infer": True,
        },
    }


def _commute_session(seq: int, day: date, rng: random.Random, evening: bool) -> dict[str, Any]:
    if evening:
        started = _dt(day, 18, rng.choice([2, 8, 16, 24, 35]))
        destination = "家"
        messages = [
            {"role": "user", "content": "回家，走平时最快的路线"},
            {"role": "assistant", "content": "已规划回家路线，并避开当前拥堵路段。"},
        ]
        operations = [_operation("navigate", destination=destination, route_preference="fastest")]
    else:
        started = _dt(day, 8, rng.choice([0, 5, 12, 18]))
        destination = "德赛科技园"
        messages = [
            {"role": "user", "content": "去公司，空调和音乐照平时通勤习惯来"},
            {"role": "assistant", "content": "已导航去公司，空调23度并播放通勤轻音乐。"},
        ]
        operations = [
            _operation("navigate", destination=destination, route_preference="fastest"),
            _operation("set_climate", temperature_c=23, fan_level=2, mode="auto"),
            _operation("play_media", playlist="通勤轻音乐", volume=18),
        ]
    return _session(seq, started, "commute", messages, operations, tags=["通勤习惯"])


def _poor_air_session(seq: int, day: date, rng: random.Random) -> dict[str, Any]:
    aqi = rng.randint(155, 218)
    started = _dt(day, 17, rng.choice([10, 25, 40]))
    messages = [
        {"role": "user", "content": f"外面空气不太好，AQI都{aqi}了，按之前那套处理"},
        {"role": "assistant", "content": "已关闭车窗、切换内循环，并将空气净化调到3档。"},
    ]
    operations = [
        _operation("close_windows", zones="all"),
        _operation("set_air_circulation", mode="internal"),
        _operation("set_air_purifier", level=3),
        _operation("set_climate", temperature_c=23, fan_level=2),
    ]
    return _session(
        seq, started, "poor_air_quality", messages, operations,
        weather={"aqi": aqi, "air_quality": "poor"}, tags=["空气差老规矩", "车控组合"],
    )


FAMILY_RESTAURANTS = [
    ("童梦森林亲子餐厅", "惠城区东江一路66号亲子中心2层", "粤菜简餐", 3.2),
    ("麦田亲子餐厅", "惠城区云山西路88号3层", "西式简餐", 5.8),
    ("小象花园家庭餐厅", "惠城区文昌一路18号", "融合菜", 7.4),
]


def _family_restaurant_session(seq: int, day: date, index: int) -> dict[str, Any]:
    name, address, cuisine, distance = FAMILY_RESTAURANTS[index % len(FAMILY_RESTAURANTS)]
    started = _dt(day, 11, 20 + index % 20)
    messages = [
        {"role": "user", "content": f"带孩子去{name}，还是要有宝宝椅和儿童活动区的"},
        {"role": "assistant", "content": f"已导航至{name}，距离{distance}公里。"},
        {"role": "user", "content": "孩子很喜欢这里，停车也方便，下次亲子吃饭可以优先考虑"},
        {"role": "assistant", "content": "好的，我会记住这次亲子用餐体验。"},
    ]
    operations = [
        _operation("navigate", destination=name, address=address, distance_km=distance),
        _operation("place_visit", name=name, category="亲子餐厅", cuisine=cuisine),
        _operation("place_feedback", rating=5 if name == "童梦森林亲子餐厅" else 4,
                   features=["宝宝椅", "儿童活动区", "停车方便"]),
    ]
    return _session(seq, started, "family_dining", messages, operations,
                    passengers=["spouse", "child"], tags=["亲子餐厅", cuisine, "孩子喜欢"])


def _food_preference_session(seq: int, day: date, variant: int) -> dict[str, Any]:
    dishes = ["清蒸鲈鱼", "白灼虾", "客家酿豆腐"]
    dish = dishes[variant % len(dishes)]
    started = _dt(day, 12, 5 + variant % 30)
    messages = [
        {"role": "user", "content": f"今天还是想吃{dish}，少油、不要香菜，口味清淡一点"},
        {"role": "assistant", "content": f"好的，已记录您偏好{dish}、少油、不加香菜和清淡口味。"},
    ]
    return _session(seq, started, "dining_preference", messages,
                    [_operation("record_food_preference", dish=dish, less_oil=True,
                                cilantro=False, flavor="light")], tags=["饮食偏好", "粤菜"])


def _western_order_session(seq: int, day: date, variant: int) -> dict[str, Any]:
    restaurant = ["蓝岸西餐厅", "橡木厨房", "湖畔牛排馆"][variant % 3]
    started = _dt(day, 18, 35)
    messages = [
        {"role": "user", "content": f"去{restaurant}，点餐还是按我的习惯"},
        {"role": "assistant", "content": "已记录：西冷牛排七分熟、蘑菇汤、柠檬水不加冰，并备注不要香菜。"},
    ]
    operations = [
        _operation("navigate", destination=restaurant, category="西餐厅"),
        _operation("restaurant_order", items=["西冷牛排七分熟", "蘑菇汤", "柠檬水不加冰"],
                   notes=["不要香菜"]),
    ]
    return _session(seq, started, "western_dining", messages, operations,
                    passengers=["spouse"], tags=["西餐习惯", "固定点餐"])


def _anchor_sessions(seq: int, end_date: date) -> list[dict[str, Any]]:
    """High-value recent evidence used by the gold fuzzy queries."""
    monday = end_date - timedelta(days=end_date.weekday())
    last_week_start = monday - timedelta(days=7)
    restaurant_day = last_week_start + timedelta(days=5)  # last Saturday
    poor_air_day = end_date - timedelta(days=2)
    result = [
        _family_restaurant_session(seq, restaurant_day, 0),
        _poor_air_session(seq + 1, poor_air_day, random.Random(7001)),
        _western_order_session(seq + 2, end_date - timedelta(days=10), 1),
    ]
    # A similar candidate which has not been visited; useful for booking tests.
    started = _dt(end_date - timedelta(days=1), 20, 10)
    result.append(_session(
        seq + 3, started, "recommendation_feedback",
        [
            {"role": "assistant", "content": "发现童趣岛家庭餐厅：距您4.1公里，有儿童活动区、宝宝椅和停车场，粤菜简餐，人均约110元。"},
            {"role": "user", "content": "这家看起来和上周去的差不多，先收藏，下次可以订"},
        ],
        [_operation("favorite_place", name="童趣岛家庭餐厅", category="亲子餐厅",
                    address="惠城区金山大道36号", distance_km=4.1,
                    features=["儿童活动区", "宝宝椅", "停车场"], cuisine="粤菜简餐")],
        tags=["相似亲子餐厅", "待预订"],
    ))
    return result


def generate(start_date: date, end_date: date, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    sessions: list[dict[str, Any]] = []
    seq = 1
    day = start_date
    restaurant_index = 0
    food_index = 0
    western_index = 0
    while day <= end_date:
        # Weekday commute provides continuous ignition-to-shutdown history.
        if day.weekday() < 5:
            sessions.append(_commute_session(seq, day, rng, evening=False)); seq += 1
            if day.toordinal() % 3 != 0:
                sessions.append(_commute_session(seq, day, rng, evening=True)); seq += 1
        # Repeated, spaced evidence is what should become a stable memory.
        if day.weekday() == 5 and day.toordinal() % 14 < 7:
            sessions.append(_family_restaurant_session(seq, day, restaurant_index)); seq += 1
            restaurant_index += 1
        if day.toordinal() % 23 == 0:
            sessions.append(_food_preference_session(seq, day, food_index)); seq += 1
            food_index += 1
        if day.toordinal() % 31 == 0:
            sessions.append(_western_order_session(seq, day, western_index)); seq += 1
            western_index += 1
        if day.month in {1, 2, 8, 9, 12} and day.toordinal() % 29 == 0:
            sessions.append(_poor_air_session(seq, day, rng)); seq += 1
        day += timedelta(days=1)

    anchor_dates = {date.fromisoformat(row["started_at"][:10]) for row in _anchor_sessions(seq, end_date)}
    sessions = [row for row in sessions if date.fromisoformat(row["started_at"][:10]) not in anchor_dates]
    sessions.extend(_anchor_sessions(seq, end_date))
    sessions.sort(key=lambda row: row["started_at"])
    return sessions


def build_gold(start_date: date, end_date: date, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    monday = end_date - timedelta(days=end_date.weekday())
    last_week = (monday - timedelta(days=7), monday - timedelta(days=1))
    cases = [
        {"id":"fuzzy_01","query":"我们经常去的亲子餐厅","intent":"habit_place_recall","strategy":"rank_with_evidence","expected_top":"童梦森林亲子餐厅","must_explain":"依据一年内多次亲子到访、孩子喜欢和评分记录，不应把一次到访直接说成经常。"},
        {"id":"fuzzy_02","query":"根据我爱吃的菜，找个最近的地方","intent":"preference_aware_nearby_search","strategy":"retrieve_preferences_then_call_poi","expected_preferences":["清蒸鲈鱼或白灼虾或客家酿豆腐","粤菜","少油","不要香菜","清淡"],"required_runtime_context":["current_location","open_status","route_distance"],"must_clarify_if_missing":"没有当前位置或实时POI结果时，只能说明偏好，不能虚构最近餐厅。"},
        {"id":"fuzzy_03","query":"外面的空气质量不好，按老规矩给我调整一下","intent":"habitual_vehicle_control","strategy":"confirm_or_execute_by_authorization","expected_operations":["close_windows","set_air_circulation:internal","set_air_purifier:3","set_climate:23C_fan2"],"evidence":"多次空气差场景中的相同操作组合"},
        {"id":"fuzzy_04","query":"帮我找一下西餐厅，按照习惯进行点餐","intent":"restaurant_search_and_order","strategy":"two_stage_action","expected_preferences":["西冷牛排七分熟","蘑菇汤","柠檬水不加冰","不要香菜"],"required_confirmation":["选择具体餐厅","确认菜单有对应菜品","提交订单前确认价格和数量"],"hard_failure":"未选定餐厅就声称点餐成功"},
        {"id":"fuzzy_05","query":"根据上周的亲子餐厅，帮我预定一个差不多的餐厅","intent":"temporal_similarity_booking","strategy":"temporal_recall_then_similarity_then_confirm","time_window":{"start":last_week[0].isoformat(),"end":last_week[1].isoformat()},"expected_reference":"童梦森林亲子餐厅","similarity_features":["亲子餐厅","儿童活动区","宝宝椅","停车方便","粤菜简餐","距离相近"],"expected_candidate":"童趣岛家庭餐厅","required_confirmation":["日期","时间","人数","儿童人数","预算","提交预订前确认"],"hard_failure":"跳过上周时间过滤，或未经确认直接预订"},
    ]
    counts = Counter(tag for row in sessions for tag in row["tags"])
    return {
        "dataset":"yearlong_cockpit_sessions.jsonl",
        "time_range":{"start":start_date.isoformat(),"end":end_date.isoformat(),"timezone":"Asia/Shanghai"},
        "session_count":len(sessions),
        "tag_counts":dict(sorted(counts.items())),
        "evaluation_policy":{"direct_answer_confidence":0.85,"clarify_below":0.60,
                             "hard_failures":["编造记忆","忽略时间边界","单次事件误判为稳定习惯","未经确认执行预订或高影响车控"]},
        "test_cases":cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 9, 2))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--seed", type=int, default=20260902)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    args = parser.parse_args()
    if args.days < 30:
        parser.error("--days must be at least 30")
    start_date = args.end_date - timedelta(days=args.days - 1)
    sessions = generate(start_date, args.end_date, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in sessions:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    gold = build_gold(start_date, args.end_date, sessions)
    args.gold.write_text(json.dumps(gold, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sessions":len(sessions), "output":str(args.output), "gold":str(args.gold)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
