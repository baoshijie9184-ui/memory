"""Generate a deterministic multi-tenant one-year cockpit memory benchmark.

Directory layout (user-facing organization):

    data/tenants/<tenant_id>/<vehicle_id>/<user_id>/sessions.jsonl
    data/tenants/<tenant_id>/<vehicle_id>/<user_id>/persona.json
    data/tenants/<tenant_id>/<vehicle_id>/_shared_multi_occupant.jsonl

Isolation matrix (see README for the full table):

    tenant oem_suv_family:
        veh_suv_001: usr_father (driver, commute+family+western+air)
                     usr_mother (driver when father away, latte, 麦田)
                     usr_child  (rear seat, 汪汪队 + yogurt)
                     usr_grandma (front seat, 粤剧 + Wednesday hospital)
                     _shared_multi_occupant.jsonl (same-session multi-occupant)
        veh_suv_002: usr_father (weekend camping car)
    tenant oem_sedan_biz:
        veh_sed_001: usr_father (same user_id across tenants, company car)

Determinism: global seed + per-(day, user) derived sub-seed.

Usage:
    python scripts/generate_multitenant_dataset.py
    python scripts/generate_multitenant_dataset.py --end-date 2026-09-07 --seed 20260907
"""

from __future__ import annotations

import argparse
import json
import random
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

TZ_CN = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
TENANTS_DIR = ROOT / "data" / "tenants"
DEFAULT_MANIFEST = ROOT / "data" / "manifest.json"
DEFAULT_CASES = ROOT / "data" / "eval_cases.json"

RECORD_TYPE = "cockpit_session"


# ─────────────────────────────────────────────────────────────────────────────
# Utterance pools.  (user_says, assistant_reply) pairs; {placeholders} filled
# at build time.  Pools are per-persona so the SAME behaviour is expressed with
# different wording AND different content across users — this is what gives the
# isolation assertions their discriminating power.
# ─────────────────────────────────────────────────────────────────────────────

# father: morning commute on the SUV / sedan (identical wording pool so the
# cross-vehicle dedup case has genuinely identical content_hash inputs).
FATHER_COMMUTE_MORNING = [
    ("去公司，空调和音乐照平时通勤习惯来",
     "已导航去公司，空调23度并播放通勤轻音乐。"),
    ("去上班，跟平时一样就行",
     "好的，按您通勤习惯导航到德赛科技园，空调23度、风量2档，给您放通勤轻音乐。"),
    ("去德赛科技园，老习惯",
     "已规划去德赛科技园的最快路线，空调调到23度，通勤轻音乐已播放。"),
    ("出发去公司，还是原来的设置",
     "出发路线已规划，目的地德赛科技园，走最快路线。空调23度、通勤歌单已就绪。"),
]

FATHER_COMMUTE_EVENING = [
    ("回家，走平时最快的路线",
     "已规划回家路线，并避开当前拥堵路段。"),
    ("导航回家，走最快的",
     "好的，按最快路线导航回家，前方拥堵已避开。"),
    ("回去了，老路线",
     "已走回家的常规最快路线。"),
    ("回惠城区，别堵车的那条路",
     "已为您选择回家的非拥堵路线，距离稍远但通行更快。"),
]

MOTHER_COMMUTE = [
    ("去西湖那边上班，空调和音乐照我的习惯",
     "已导航至惠州西湖，避开高速走市内道路，空调24度并播放爵士歌单。"),
    ("去公司，老规矩",
     "好的，按您的习惯：惠州西湖、市内道路、空调24度、爵士歌单。"),
    ("去上班，还是我平时那套",
     "已按习惯设置：目的地惠州西湖、避开高速、空调24度，爵士歌单已播放。"),
]

FATHER_FAMILY_DINING = [
    ("带孩子去童梦森林亲子餐厅，还是要有宝宝椅和儿童活动区的",
     "已导航至童梦森林亲子餐厅，距离3.2公里，有宝宝椅和儿童活动区。"),
    ("今天带娃去童梦森林吧，记得要宝宝椅",
     "已规划前往童梦森林亲子餐厅，已备注宝宝椅和儿童活动区。"),
    ("去童梦森林，上次那家有儿童游乐设施的",
     "童梦森林亲子餐厅已设为目的地，全程3.2公里。您之前评价过宝宝椅不错。"),
]

MOTHER_FAMILY_DINING = [
    ("带孩子去麦田亲子餐厅，那家宝宝椅和活动区都不错",
     "已导航至麦田亲子餐厅，距离5.8公里，宝宝椅和儿童活动区已确认。"),
    ("还是去麦田吧，孩子熟门熟路了",
     "好的，麦田亲子餐厅已设为目的地。根据您的记录，这是您最常去的亲子餐厅。"),
    ("去麦田亲子餐厅，老地方",
     "麦田亲子餐厅已导航，距离5.8公里。"),
]

FAMILY_FEEDBACK = [
    ("孩子很喜欢这里，停车也方便，下次亲子吃饭可以优先考虑",
     "好的，我会记住这次亲子用餐体验。"),
    ("这地方不错，以后带娃来就选这了",
     "已记录您的反馈，下次家庭聚餐会优先推荐。"),
]

FATHER_FOOD_PREF = [
    ("今天还是想吃清蒸鲈鱼，少油、不要香菜，口味清淡一点",
     "好的，已记录您偏好清蒸鲈鱼、少油、不加香菜和清淡口味。"),
    ("来份清蒸鲈鱼吧，老规矩，少油不加香菜",
     "收到，清蒸鲈鱼、少油、不加香菜。"),
    ("想吃鲈鱼了，清淡点，少油不要香菜",
     "清淡口味的清蒸鲈鱼，少油不加香菜，已记下。"),
]

MOTHER_FOOD_PREF = [
    ("中午想吃得健康点，藜麦沙拉配拿铁，少糖",
     "已记录：藜麦沙拉、少糖拿铁，健康轻食偏好。"),
    ("老规矩，沙拉和拿铁，拿铁少糖",
     "好的，藜麦沙拉配少糖拿铁，已按习惯记录。"),
]

MOTHER_LATTE = [
    ("去我的咖啡店，拿铁少糖，加燕麦奶",
     "已记录您的拿铁偏好：少糖、燕麦奶，并导航至您常去的咖啡店。"),
    ("路过咖啡店停一下，老样子来杯拿铁",
     "已按您的固定搭配下单：拿铁、少糖、燕麦奶。"),
]

FATHER_WESTERN = [
    ("去蓝岸西餐厅，点餐还是按我的习惯",
     "已记录：西冷牛排七分熟、蘑菇汤、柠檬水不加冰，并备注不要香菜。"),
    ("到蓝岸了，帮我按老样子点餐吧",
     "按您的习惯：西冷牛排七分熟、蘑菇汤、柠檬水不加冰，不加香菜。"),
    ("在西餐厅，照旧给我点",
     "好的，西冷七分熟、蘑菇汤、柠檬水不加冰，不放香菜。"),
]

FATHER_POOR_AIR = [
    ("外面空气不太好，AQI都{aqi}了，按之前那套处理",
     "已关闭车窗、切换内循环，并将空气净化调到3档。"),
    ("空气质量这么差，按老规矩来吧",
     "已执行：关窗、内循环、净化器3档，同时空调保持23度。"),
    ("外面太闷了，车里空气也一般，按之前的方案弄一下",
     "已关闭所有车窗切换到内循环模式，空气净化器升到3档。"),
]

CHILD_REQUESTS = [
    ("我要看汪汪队",
     "好的，已为您播放《汪汪队立大功》。"),
    ("放汪汪队嘛，我想看冒险湾的那集",
     "已播放《汪汪队立大功》冒险湾系列。"),
    ("还是看汪汪队吧",
     "已切换到《汪汪队立大功》。"),
]

CHILD_SNACK = [
    ("我想喝酸奶",
     "好的，已记录。到家提醒您拿酸奶。"),
    ("能给我买个草莓酸奶吗",
     "已添加到购物提醒：草莓酸奶。"),
]

GRANDMA_OPERA = [
    ("放粤剧吧，音量调到12",
     "已为您播放粤剧频道，音量12。"),
    ("还是听粤剧，老习惯",
     "粤剧频道已播放，音量按习惯调到12。"),
    ("换个粤剧选段，声音别太大",
     "已切换粤剧选段，音量12。"),
]

GRANDMA_HOSPITAL = [
    ("周三去医院，还是市中心医院，老时间",
     "已导航至市中心医院，走市内道路，预计9:20到达。"),
    ("老规矩，市中心医院透析，八点半出发",
     "已按周三透析行程规划：市中心医院、市内道路。"),
    ("这周三还是去医院，跟以前一样",
     "好的，已按您的周三习惯规划市中心医院行程。"),
]

CAMPING = [
    ("去红花湖露营，出发前把露营模式设置好",
     "露营模式已开启：后排放倒提示、空调24度、后备箱常电。"),
    ("周末去红花湖，还是上次的露营设置",
     "已恢复上次露营配置：后排放倒、空调24度。"),
    ("去红花湖，按露营那套来",
     "露营模式已就绪，导航至红花湖营地。"),
]

SEDAN_COMMUTE = [
    # Same wording as FATHER_COMMUTE_MORNING pool — intentional: this feeds the
    # (tenant_id, user_id, content_hash) dedup test case flt_04.
    ("去公司，空调和音乐照平时通勤习惯来",
     "已导航去公司，空调23度并播放通勤轻音乐。"),
    ("去上班，跟平时一样就行",
     "好的，按您通勤习惯导航到德赛科技园，空调23度、风量2档，给您放通勤轻音乐。"),
]

# ── one-off deviations (should NOT become stable memories) ──────────────────
DEVIATIONS = [
    ("今天有点冷，空调调到25度吧", "已调高空调到25度。", {"action": "set_climate", "parameters": {"temperature_c": 25}}),
    ("换个路线吧，今天三环有点堵", "已重新规划路线，走北环大道避开拥堵。", {"action": "navigate", "parameters": {"route_preference": "avoid_congestion"}}),
    ("不要通勤轻音乐了，来点新闻", "已切换到新闻频道。", {"action": "play_media", "parameters": {"playlist": "新闻频道"}}),
    ("今天不带孩子，随便找个地方吃", "附近有三家评分较高的粤菜馆，需要推荐吗？", {"action": "poi_search", "parameters": {"category": "粤菜馆"}}),
]

# ── chit-chat (no memory value; should not surface in recalls) ───────────────
CHIT_CHAT = [
    ("今天天气不错啊", "是的，惠州今天晴，26度，适合出行。"),
    ("最近新闻有什么", "为您播报今日要闻摘要。"),
    ("现在几点了", "现在是{now_time}。"),
    ("这条路上次堵了好久", "当前路段通畅，预计不堵。"),
]

# ── charging / maintenance (new scenario habits) ────────────────────────────
CHARGING = [
    ("电量低了，去我常去的充电站", "已导航至惠州南站充电站，快充桩。", "惠州南站充电站"),
    ("该充电了，老地方", "已规划至您常去的惠州南站充电站。", "惠州南站充电站"),
]

MAINTENANCE = [
    ("车提示保养了，帮我预约老地方那家店", "已为您预约德赛授权服务中心，周四上午。", "德赛授权服务中心"),
    ("保养提醒又来了，还是约上次那家", "已预约德赛授权服务中心。", "德赛授权服务中心"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Personas
# ─────────────────────────────────────────────────────────────────────────────

class Persona:
    def __init__(
        self,
        user_id: str,
        role: str,
        occupant: str,
        habits: list[str],
        noise: list[str],
        recall_anchors: list[dict[str, Any]],
    ) -> None:
        self.user_id = user_id
        self.role = role
        self.occupant = occupant
        self.habits = habits
        self.noise = noise
        self.recall_anchors = recall_anchors

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "role": self.role,
            "occupant": self.occupant,
            "expected_habits": self.habits,
            "expected_noise": self.noise,
            "recall_anchors": self.recall_anchors,
        }


PERSONAS: dict[str, Persona] = {
    "usr_father": Persona(
        user_id="usr_father",
        role="主驾车主（SUV001 主用 / SUV002 露营 / SED001 公司配车）",
        occupant="driver",
        habits=[
            "通勤导航德赛科技园，最快路线",
            "通勤空调23度风量2档 + 通勤轻音乐音量18",
            "亲子餐厅首选童梦森林（宝宝椅/儿童活动区）",
            "清蒸鲈鱼、少油、不要香菜、清淡",
            "西餐固定：西冷七分熟+蘑菇汤+柠檬水不加冰",
            "空气差老规矩：关窗+内循环+净化3档",
            "充电常去惠州南站充电站",
            "保养预约德赛授权服务中心",
        ],
        noise=["偶发25度空调", "偶发换路线/换新闻", "单次收藏童趣岛"],
        recall_anchors=[
            {"topic": "通勤", "query": "导航去公司", "expected": ["德赛科技园"]},
            {"topic": "亲子餐厅", "query": "我们经常去的亲子餐厅", "expected": ["童梦森林"], "not_expected_top1": ["童趣岛"]},
            {"topic": "空调", "query": "空调调到平时的温度", "expected": ["23"]},
            {"topic": "音乐", "query": "播放我最喜欢的音乐", "expected": ["通勤轻音乐"]},
            {"topic": "饮食", "query": "我平时爱吃什么菜", "expected": ["鲈鱼", "少油", "香菜"]},
            {"topic": "西餐", "query": "我西餐一般怎么点", "expected": ["西冷", "蘑菇汤", "柠檬水"]},
            {"topic": "空气差", "query": "空气差的时候怎么调", "expected": ["内循环", "3档"]},
        ],
    ),
    "usr_mother": Persona(
        user_id="usr_mother",
        role="第二驾驶者（father 出差周接管通勤）+ 副驾乘客",
        occupant="driver",
        habits=[
            "通勤导航惠州西湖，避开高速走市内道路",
            "通勤空调24度 + 爵士歌单",
            "亲子餐厅首选麦田（与 father 的童梦森林区分）",
            "健康轻食：藜麦沙拉+少糖拿铁",
            "拿铁固定少糖+燕麦奶",
        ],
        noise=["偶发听有声书"],
        recall_anchors=[
            {"topic": "通勤", "query": "导航去上班", "expected": ["惠州西湖", "市内道路"]},
            {"topic": "亲子餐厅", "query": "我们经常去的亲子餐厅", "expected": ["麦田"], "not_expected_top1": ["童梦森林"]},
            {"topic": "咖啡", "query": "我的拿铁怎么点", "expected": ["少糖", "燕麦奶"]},
            {"topic": "轻食", "query": "我中午吃什么比较健康", "expected": ["藜麦沙拉"]},
        ],
    ),
    "usr_child": Persona(
        user_id="usr_child",
        role="后座儿童乘客",
        occupant="passenger_rear",
        habits=["观看《汪汪队立大功》", "酸奶零食（草莓酸奶）"],
        noise=["偶发要看小猪佩奇"],
        recall_anchors=[
            {"topic": "动画", "query": "我想看的动画片", "expected": ["汪汪队"]},
            {"topic": "零食", "query": "我的零食", "expected": ["酸奶"]},
        ],
    ),
    "usr_grandma": Persona(
        user_id="usr_grandma",
        role="副驾老人（每周三透析行程）",
        occupant="passenger_front",
        habits=["每周三市中心医院透析导航（市内道路）", "粤剧频道音量12"],
        noise=["偶发听评书"],
        recall_anchors=[
            {"topic": "行程", "query": "周三的行程怎么安排", "expected": ["市中心医院", "透析"]},
            {"topic": "戏曲", "query": "我平时听什么", "expected": ["粤剧", "12"]},
        ],
    ),
}

CAMPING_PERSONA = Persona(
    user_id="usr_father",
    role="周末露营车驾驶者（veh_suv_002）",
    occupant="driver",
    habits=["露营模式：后排放倒+空调24度+红花湖营地"],
    noise=[],
    recall_anchors=[
        {"topic": "露营", "query": "露营怎么设置", "expected": ["后排放倒", "24"]},
    ],
)


# ─────────────────────────────────────────────────────────────────────────────
# Calendar model: business trips / vacations shape who drives which days.
# ─────────────────────────────────────────────────────────────────────────────

def build_calendar(start: date, end: date, seed: int) -> dict[date, dict[str, Any]]:
    """Mark special weeks: father business trips (mother takes over commute),
    family vacations (no sessions at all)."""
    rng = random.Random(seed ^ 0xCA1E)
    cal: dict[date, dict[str, Any]] = {}
    cur = start
    trip_weeks: list[date] = []
    vac_weeks: list[date] = []
    while cur <= end:
        cal[cur] = {"father_away": False, "family_vacation": False}
        cur += timedelta(days=1)
    # pick trip/vacation weeks deterministically: ~4 trip weeks, 2 vacation weeks
    mondays = []
    cur = start
    while cur <= end:
        if cur.weekday() == 0:
            mondays.append(cur)
        cur += timedelta(days=1)
    if len(mondays) >= 10:
        picks = rng.sample(mondays[4:-4], min(6, len(mondays) - 8))  # avoid edges
        trip_weeks = picks[:4]
        vac_weeks = picks[4:]
    for monday in trip_weeks:
        for i in range(5):
            d = monday + timedelta(days=i)
            if d in cal:
                cal[d]["father_away"] = True
    for monday in vac_weeks:
        for i in range(7):
            d = monday + timedelta(days=i)
            if d in cal:
                cal[d]["family_vacation"] = True
    return cal


# ─────────────────────────────────────────────────────────────────────────────
# Session builders
# ─────────────────────────────────────────────────────────────────────────────

SEQ = 0


def _next_seq() -> int:
    global SEQ
    SEQ += 1
    return SEQ


def _dt(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), TZ_CN)


def _operation(action: str, **parameters: Any) -> dict[str, Any]:
    return {"action": action, "parameters": parameters, "status": "success"}


def make_session(
    *,
    tenant_id: str,
    vehicle_id: str,
    user_id: str,
    occupant_id: str,
    started_at: datetime,
    scene: str,
    messages: list[dict[str, str]],
    operations: list[dict[str, Any]],
    passengers: list[str] | None = None,
    weather: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    cross_occupant: bool = False,
) -> dict[str, Any]:
    ended_at = started_at + timedelta(minutes=max(18, 8 + len(operations) * 7))
    session_id = f"drive_{started_at:%Y%m%d_%H%M}_{_next_seq():04d}"
    dated: list[dict[str, str]] = []
    for m in messages:
        row = dict(m)
        if row["role"] == "user":
            row["content"] = f"[{started_at:%Y-%m-%d %H:%M}] {row['content']}"
        dated.append(row)
    return {
        "record_type": RECORD_TYPE,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "vehicle_id": vehicle_id,
        "session_id": session_id,
        "cross_occupant": cross_occupant,
        "scene": scene,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "lifecycle": [
            {"event": "ignition_on", "occurred_at": started_at.isoformat()},
            {"event": "ignition_off", "occurred_at": ended_at.isoformat()},
        ],
        "occupants": ["driver"] + (passengers or []),
        "weather": weather or {},
        "messages": dated,
        "operations": operations,
        "tags": tags or [],
        "api_payload": {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "vehicle_id": vehicle_id,
            "session_id": session_id,
            "occupant_id": occupant_id,
            "scene": scene,
            "source": "synthetic_cockpit",
            "messages": dated,
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


TENANT_SUV = "oem_suv_family"
TENANT_SEDAN = "oem_sedan_biz"
VEH_SUV1 = "veh_suv_001"
VEH_SUV2 = "veh_suv_002"
VEH_SED1 = "veh_sed_001"


def father_commute(day: date, rng: random.Random, evening: bool, tenant: str, vehicle: str) -> dict[str, Any]:
    if evening:
        user, reply = rng.choice(FATHER_COMMUTE_EVENING)
        started = _dt(day, 18, rng.choice([2, 8, 16, 24, 35]))
        ops = [_operation("navigate", destination="家", route_preference="fastest")]
    else:
        user, reply = rng.choice(FATHER_COMMUTE_MORNING)
        started = _dt(day, 8, rng.choice([0, 5, 12, 18]))
        ops = [
            _operation("navigate", destination="德赛科技园", route_preference="fastest"),
            _operation("set_climate", temperature_c=23, fan_level=2, mode="auto"),
            _operation("play_media", playlist="通勤轻音乐", volume=18),
        ]
    return make_session(
        tenant_id=tenant, vehicle_id=vehicle, user_id="usr_father", occupant_id="driver",
        started_at=started, scene="commute", messages=[
            {"role": "user", "content": user},
            {"role": "assistant", "content": reply},
        ], operations=ops, tags=["通勤习惯", "father"],
    )


def mother_commute(day: date, rng: random.Random, evening: bool) -> dict[str, Any]:
    user, reply = rng.choice(MOTHER_COMMUTE)
    hour, minute = (18, rng.choice([5, 15, 25])) if evening else (8, rng.choice([10, 22, 40]))
    dest = "惠州西湖" if not evening else "家"
    ops = [
        _operation("navigate", destination=dest, route_preference="city_roads"),
        _operation("set_climate", temperature_c=24, fan_level=2, mode="auto"),
        _operation("play_media", playlist="爵士歌单", volume=16),
    ]
    return make_session(
        tenant_id=TENANT_SUV, vehicle_id=VEH_SUV1, user_id="usr_mother", occupant_id="driver",
        started_at=_dt(day, hour, minute), scene="commute", messages=[
            {"role": "user", "content": user},
            {"role": "assistant", "content": reply},
        ], operations=ops, tags=["通勤习惯", "mother"],
    )


def family_dining(day: date, rng: random.Random, who: str) -> dict[str, Any]:
    pool = FATHER_FAMILY_DINING if who == "father" else MOTHER_FAMILY_DINING
    user_id = f"usr_{who}"
    nav_user, nav_reply = rng.choice(pool)
    fb_user, fb_reply = rng.choice(FAMILY_FEEDBACK)
    name = "童梦森林亲子餐厅" if who == "father" else "麦田亲子餐厅"
    distance = 3.2 if who == "father" else 5.8
    started = _dt(day, 11, rng.choice([20, 28, 35]))
    ops = [
        _operation("navigate", destination=name, distance_km=distance),
        _operation("place_visit", name=name, category="亲子餐厅"),
        _operation("place_feedback", rating=5, features=["宝宝椅", "儿童活动区", "停车方便"]),
    ]
    return make_session(
        tenant_id=TENANT_SUV, vehicle_id=VEH_SUV1, user_id=user_id, occupant_id="driver",
        started_at=started, scene="family_dining", messages=[
            {"role": "user", "content": nav_user},
            {"role": "assistant", "content": nav_reply},
            {"role": "user", "content": fb_user},
            {"role": "assistant", "content": fb_reply},
        ], operations=ops, passengers=["spouse", "child"],
        tags=["亲子餐厅", who, "孩子喜欢"],
    )


def food_preference(day: date, rng: random.Random, who: str) -> dict[str, Any]:
    pool = FATHER_FOOD_PREF if who == "father" else MOTHER_FOOD_PREF
    user_id = f"usr_{who}"
    user, reply = rng.choice(pool)
    return make_session(
        tenant_id=TENANT_SUV, vehicle_id=VEH_SUV1, user_id=user_id, occupant_id="driver",
        started_at=_dt(day, 12, rng.choice([5, 14, 26])), scene="dining_preference", messages=[
            {"role": "user", "content": user},
            {"role": "assistant", "content": reply},
        ], operations=[
            _operation("record_food_preference", dish="清蒸鲈鱼" if who == "father" else "藜麦沙拉",
                       less_oil=True, cilantro=False, flavor="light" if who == "father" else "healthy"),
        ], tags=["饮食偏好", who],
    )


def mother_latte(day: date, rng: random.Random) -> dict[str, Any]:
    user, reply = rng.choice(MOTHER_LATTE)
    return make_session(
        tenant_id=TENANT_SUV, vehicle_id=VEH_SUV1, user_id="usr_mother", occupant_id="driver",
        started_at=_dt(day, 10, rng.choice([8, 30])), scene="coffee_run", messages=[
            {"role": "user", "content": user},
            {"role": "assistant", "content": reply},
        ], operations=[
            _operation("navigate", destination="常去咖啡店", category="咖啡店"),
            _operation("place_order", items=["拿铁少糖燕麦奶"]),
        ], tags=["咖啡偏好", "mother"],
    )


def western_dining(day: date, rng: random.Random) -> dict[str, Any]:
    restaurant = rng.choice(["蓝岸西餐厅", "橡木厨房", "湖畔牛排馆"])
    pool = [v for v in FATHER_WESTERN if restaurant in v[0]] or FATHER_WESTERN[:1]
    user, reply = pool[0]
    started = _dt(day, 18, rng.choice([35, 45]))
    return make_session(
        tenant_id=TENANT_SUV, vehicle_id=VEH_SUV1, user_id="usr_father", occupant_id="driver",
        started_at=started, scene="western_dining", messages=[
            {"role": "user", "content": user},
            {"role": "assistant", "content": reply},
        ], operations=[
            _operation("navigate", destination=restaurant, category="西餐厅"),
            _operation("restaurant_order", items=["西冷牛排七分熟", "蘑菇汤", "柠檬水不加冰"], notes=["不要香菜"]),
        ], passengers=["spouse"], tags=["西餐习惯", "father"],
    )


def poor_air(day: date, rng: random.Random) -> dict[str, Any]:
    aqi = rng.randint(155, 218)
    user, reply = rng.choice(FATHER_POOR_AIR)
    user = user.format(aqi=aqi)
    return make_session(
        tenant_id=TENANT_SUV, vehicle_id=VEH_SUV1, user_id="usr_father", occupant_id="driver",
        started_at=_dt(day, 17, rng.choice([10, 25, 40])), scene="poor_air_quality", messages=[
            {"role": "user", "content": user},
            {"role": "assistant", "content": reply},
        ], operations=[
            _operation("close_windows", zones="all"),
            _operation("set_air_circulation", mode="internal"),
            _operation("set_air_purifier", level=3),
            _operation("set_climate", temperature_c=23, fan_level=2),
        ], weather={"aqi": aqi, "air_quality": "poor"}, tags=["空气差老规矩", "father"],
    )


def child_request(day: date, rng: random.Random, kind: str = "cartoon") -> dict[str, Any]:
    pool = CHILD_REQUESTS if kind == "cartoon" else CHILD_SNACK
    user, reply = rng.choice(pool)
    return make_session(
        tenant_id=TENANT_SUV, vehicle_id=VEH_SUV1, user_id="usr_child", occupant_id="passenger_rear",
        started_at=_dt(day, rng.choice([10, 15, 16]), rng.choice([0, 15, 30])), scene="child_request",
        messages=[{"role": "user", "content": user}, {"role": "assistant", "content": reply}],
        operations=[_operation("play_media", playlist="汪汪队立大功", volume=14) if kind == "cartoon"
                    else _operation("shopping_reminder", item="草莓酸奶")],
        tags=["儿童请求", "child"],
    )


def grandma_opera(day: date, rng: random.Random) -> dict[str, Any]:
    user, reply = rng.choice(GRANDMA_OPERA)
    return make_session(
        tenant_id=TENANT_SUV, vehicle_id=VEH_SUV1, user_id="usr_grandma", occupant_id="passenger_front",
        started_at=_dt(day, rng.choice([9, 14]), rng.choice([5, 20])), scene="opera_time",
        messages=[{"role": "user", "content": user}, {"role": "assistant", "content": reply}],
        operations=[_operation("play_media", playlist="粤剧频道", volume=12)],
        tags=["戏曲偏好", "grandma"],
    )


def grandma_hospital(day: date, rng: random.Random) -> dict[str, Any]:
    user, reply = rng.choice(GRANDMA_HOSPITAL)
    return make_session(
        tenant_id=TENANT_SUV, vehicle_id=VEH_SUV1, user_id="usr_grandma", occupant_id="passenger_front",
        started_at=_dt(day, 8, 30), scene="hospital_trip", messages=[
            {"role": "user", "content": user},
            {"role": "assistant", "content": reply},
        ], operations=[
            _operation("navigate", destination="市中心医院", route_preference="city_roads"),
        ], tags=["医院行程", "grandma", "周三透析"],
    )


def camping_trip(day: date, rng: random.Random) -> dict[str, Any]:
    user, reply = rng.choice(CAMPING)
    return make_session(
        tenant_id=TENANT_SUV, vehicle_id=VEH_SUV2, user_id="usr_father", occupant_id="driver",
        started_at=_dt(day, 9, rng.choice([0, 15])), scene="camping", messages=[
            {"role": "user", "content": user},
            {"role": "assistant", "content": reply},
        ], operations=[
            _operation("navigate", destination="红花湖营地", category="露营地"),
            _operation("set_climate", temperature_c=24),
            _operation("camping_mode", rear_seats_fold=True),
        ], passengers=["spouse", "child"], tags=["露营习惯", "father"],
    )


def sedan_commute(day: date, rng: random.Random) -> dict[str, Any]:
    user, reply = rng.choice(SEDAN_COMMUTE)
    return make_session(
        tenant_id=TENANT_SEDAN, vehicle_id=VEH_SED1, user_id="usr_father", occupant_id="driver",
        started_at=_dt(day, 8, rng.choice([0, 8, 15])), scene="commute", messages=[
            {"role": "user", "content": user},
            {"role": "assistant", "content": reply},
        ], operations=[
            _operation("navigate", destination="德赛科技园", route_preference="fastest"),
            _operation("set_climate", temperature_c=23, fan_level=2, mode="auto"),
            _operation("play_media", playlist="通勤轻音乐", volume=18),
        ], tags=["通勤习惯", "father", "公司配车"],
    )


def deviation_session(day: date, rng: random.Random, tenant: str, vehicle: str, user_id: str) -> dict[str, Any]:
    user, reply, op = rng.choice(DEVIATIONS)
    return make_session(
        tenant_id=tenant, vehicle_id=vehicle, user_id=user_id, occupant_id="driver",
        started_at=_dt(day, rng.choice([8, 13, 19]), rng.choice([10, 40])), scene="deviation",
        messages=[{"role": "user", "content": user}, {"role": "assistant", "content": reply}],
        operations=[op], tags=["偶发偏离", user_id],
    )


def chit_chat_session(day: date, rng: random.Random, tenant: str, vehicle: str, user_id: str) -> dict[str, Any]:
    user, reply = rng.choice(CHIT_CHAT)
    user = user.format(now_time=f"{rng.randint(9, 18):02d}:{rng.randint(0, 59):02d}")
    return make_session(
        tenant_id=tenant, vehicle_id=vehicle, user_id=user_id, occupant_id="driver",
        started_at=_dt(day, rng.choice([9, 12, 17]), rng.choice([5, 35])), scene="chit_chat",
        messages=[{"role": "user", "content": user}, {"role": "assistant", "content": reply}],
        operations=[], tags=["闲聊", user_id],
    )


def charging_session(day: date, rng: random.Random, vehicle: str) -> dict[str, Any]:
    user, reply, station = rng.choice(CHARGING)
    return make_session(
        tenant_id=TENANT_SUV, vehicle_id=vehicle, user_id="usr_father", occupant_id="driver",
        started_at=_dt(day, rng.choice([10, 20]), rng.choice([0, 30])), scene="charging", messages=[
            {"role": "user", "content": user},
            {"role": "assistant", "content": reply},
        ], operations=[_operation("navigate", destination=station, category="充电站")],
        tags=["充电习惯", "father"],
    )


def maintenance_session(day: date, rng: random.Random, vehicle: str) -> dict[str, Any]:
    user, reply, shop = rng.choice(MAINTENANCE)
    return make_session(
        tenant_id=TENANT_SUV, vehicle_id=vehicle, user_id="usr_father", occupant_id="driver",
        started_at=_dt(day, rng.choice([11, 15]), rng.choice([10, 45])), scene="maintenance", messages=[
            {"role": "user", "content": user},
            {"role": "assistant", "content": reply},
        ], operations=[_operation("book_maintenance", shop=shop)],
        tags=["保养习惯", "father"],
    )


def shared_multi_occupant_trip(day: date, rng: random.Random) -> list[dict[str, Any]]:
    """One family Saturday trip; same session split across occupants."""
    base = _dt(day, 10, rng.choice([0, 30]))
    session_id = f"drive_shared_{base:%Y%m%d_%H%M}_{_next_seq():04d}"
    records = []

    def _mk(user_id: str, occupant: str, offset_min: int, msgs, ops, tags):
        started = base + timedelta(minutes=offset_min)
        rec = make_session(
            tenant_id=TENANT_SUV, vehicle_id=VEH_SUV1, user_id=user_id, occupant_id=occupant,
            started_at=started, scene="family_shared_trip", messages=msgs, operations=ops,
            passengers=["spouse", "child", "grandma"], tags=tags, cross_occupant=True,
        )
        rec["session_id"] = session_id
        rec["api_payload"]["session_id"] = session_id
        records.append(rec)
        return rec

    _mk("usr_father", "driver", 0, [
        {"role": "user", "content": rng.choice([
            "今天全家去麦田吧，孩子想去",
            "带全家去麦田亲子餐厅，走妈妈说的那条路",
        ])},
        {"role": "assistant", "content": "已导航至麦田亲子餐厅，全家出行模式。"},
    ], [_operation("navigate", destination="麦田亲子餐厅", route_preference="city_roads")], ["家庭出行", "father"])

    _mk("usr_child", "passenger_rear", 8, [
        {"role": "user", "content": rng.choice(CHILD_REQUESTS)[0]},
        {"role": "assistant", "content": "好的，已为您播放《汪汪队立大功》。"},
    ], [_operation("play_media", playlist="汪汪队立大功", volume=14)], ["家庭出行", "child"])

    _mk("usr_grandma", "passenger_front", 12, [
        {"role": "user", "content": rng.choice(GRANDMA_OPERA)[0]},
        {"role": "assistant", "content": "已为您播放粤剧频道，音量12。"},
    ], [_operation("play_media", playlist="粤剧频道", volume=12)], ["家庭出行", "grandma"])

    return records


def favorite_one_shot(day: date) -> dict[str, Any]:
    """童趣岛: collected ONCE — must never beat 童梦森林/麦田 in habit recalls."""
    return make_session(
        tenant_id=TENANT_SUV, vehicle_id=VEH_SUV1, user_id="usr_father", occupant_id="driver",
        started_at=_dt(day, 20, 10), scene="recommendation_feedback", messages=[
            {"role": "assistant", "content": "发现童趣岛家庭餐厅：距您4.1公里，有儿童活动区、宝宝椅和停车场，粤菜简餐，人均约110元。"},
            {"role": "user", "content": "这家看起来不错，先收藏，下次可以订"},
        ], operations=[
            _operation("favorite_place", name="童趣岛家庭餐厅", category="亲子餐厅", distance_km=4.1),
        ], tags=["相似亲子餐厅", "单次收藏", "father"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main generation
# ─────────────────────────────────────────────────────────────────────────────

def generate(start: date, end: date, seed: int) -> dict[str, list[dict[str, Any]]]:
    """Returns sessions bucketed by path key: (tenant, vehicle, user_or__shared)."""
    cal = build_calendar(start, end, seed)
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)

    def rng_for(day: date, user: str, salt: int = 0) -> random.Random:
        return random.Random((seed, day.toordinal(), user, salt).__hash__())

    day = start
    air_counter = 0
    latte_counter = 0
    while day <= end:
        info = cal[day]
        weekday = day.weekday()

        if not info["family_vacation"]:
            # ── SUV1 weekday commutes ────────────────────────────────────
            if weekday < 5 and not info["father_away"]:
                buckets[(TENANT_SUV, VEH_SUV1, "usr_father")].append(
                    father_commute(day, rng_for(day, "father_am"), evening=False, tenant=TENANT_SUV, vehicle=VEH_SUV1))
                if day.toordinal() % 3 != 0:
                    buckets[(TENANT_SUV, VEH_SUV1, "usr_father")].append(
                        father_commute(day, rng_for(day, "father_pm"), evening=True, tenant=TENANT_SUV, vehicle=VEH_SUV1))
            elif weekday < 5 and info["father_away"]:
                # mother takes over the commute during father's business trip
                buckets[(TENANT_SUV, VEH_SUV1, "usr_mother")].append(
                    mother_commute(day, rng_for(day, "mother_am"), evening=False))
                if day.toordinal() % 2 != 0:
                    buckets[(TENANT_SUV, VEH_SUV1, "usr_mother")].append(
                        mother_commute(day, rng_for(day, "mother_pm"), evening=True))

            # ── sedan: father company car, some weekdays (not vacation/trip)
            if weekday < 5 and day.toordinal() % 5 == 0 and not info["father_away"]:
                buckets[(TENANT_SEDAN, VEH_SED1, "usr_father")].append(
                    sedan_commute(day, rng_for(day, "sedan")))

            # ── Saturday family dining (father primary; mother every 3rd)
            if weekday == 5 and day.toordinal() % 14 < 7:
                who = "father" if day.toordinal() % 42 < 28 else "mother"
                buckets[(TENANT_SUV, VEH_SUV1, f"usr_{who}")].append(
                    family_dining(day, rng_for(day, f"dine_{who}"), who))
                # shared multi-occupant records go to the vehicle-level file
                if day.toordinal() % 42 >= 28:
                    buckets[(TENANT_SUV, VEH_SUV1, "_shared_multi_occupant")].extend(
                        shared_multi_occupant_trip(day, rng_for(day, "shared")))

            # ── food preference (father) / healthy lunch (mother)
            if day.toordinal() % 23 == 0:
                buckets[(TENANT_SUV, VEH_SUV1, "usr_father")].append(
                    food_preference(day, rng_for(day, "food"), "father"))
            if day.toordinal() % 23 == 11:
                buckets[(TENANT_SUV, VEH_SUV1, "usr_mother")].append(
                    food_preference(day, rng_for(day, "food_m"), "mother"))

            # ── western dining (father)
            if day.toordinal() % 31 == 0:
                buckets[(TENANT_SUV, VEH_SUV1, "usr_father")].append(
                    western_dining(day, rng_for(day, "west")))

            # ── poor air (father)
            if day.month in {1, 2, 8, 9, 12} and day.toordinal() % 29 == 0:
                buckets[(TENANT_SUV, VEH_SUV1, "usr_father")].append(poor_air(day, rng_for(day, "air")))
                air_counter += 1

            # ── mother latte runs (twice a month-ish)
            if weekday in {2, 4} and day.toordinal() % 14 == 3:
                buckets[(TENANT_SUV, VEH_SUV1, "usr_mother")].append(
                    mother_latte(day, rng_for(day, "latte")))
                latte_counter += 1

            # ── grandma: Wednesday hospital + opera rides
            if weekday == 2:
                buckets[(TENANT_SUV, VEH_SUV1, "usr_grandma")].append(
                    grandma_hospital(day, rng_for(day, "hosp")))
            if day.toordinal() % 11 == 0:
                buckets[(TENANT_SUV, VEH_SUV1, "usr_grandma")].append(
                    grandma_opera(day, rng_for(day, "opera")))

            # ── child requests (weekends + some school-run mornings)
            if weekday >= 5 and day.toordinal() % 7 < 4:
                buckets[(TENANT_SUV, VEH_SUV1, "usr_child")].append(
                    child_request(day, rng_for(day, "child_c"), "cartoon"))
            if day.toordinal() % 17 == 0:
                buckets[(TENANT_SUV, VEH_SUV1, "usr_child")].append(
                    child_request(day, rng_for(day, "child_s"), "snack"))

            # ── camping on SUV2 (alternating weekends)
            if weekday == 6 and day.toordinal() % 14 >= 7:
                buckets[(TENANT_SUV, VEH_SUV2, "usr_father")].append(
                    camping_trip(day, rng_for(day, "camp")))

            # ── charging / maintenance
            if day.toordinal() % 61 == 0:
                buckets[(TENANT_SUV, VEH_SUV1, "usr_father")].append(
                    charging_session(day, rng_for(day, "charge"), VEH_SUV1))
            if day.toordinal() % 121 == 5:
                buckets[(TENANT_SUV, VEH_SUV1, "usr_father")].append(
                    maintenance_session(day, rng_for(day, "maint"), VEH_SUV1))

            # ── one-off deviations + chit chat (probability-based, deterministic)
            r = rng_for(day, "dev")
            if r.random() < 0.06:
                who = "father" if r.random() < 0.7 else "mother"
                buckets[(TENANT_SUV, VEH_SUV1, f"usr_{who}")].append(
                    deviation_session(day, r, TENANT_SUV, VEH_SUV1, f"usr_{who}"))
            if r.random() < 0.07:
                who = "father" if r.random() < 0.8 else "mother"
                buckets[(TENANT_SUV, VEH_SUV1, f"usr_{who}")].append(
                    chit_chat_session(day, r, TENANT_SUV, VEH_SUV1, f"usr_{who}"))

        day += timedelta(days=1)

    # anchors near the end of the year for temporal queries
    end = end  # keep flake happy
    last_saturday = end - timedelta(days=(end.weekday() + 2) % 7)
    buckets[(TENANT_SUV, VEH_SUV1, "usr_father")].append(family_dining(last_saturday, random.Random(7001), "father"))
    buckets[(TENANT_SUV, VEH_SUV1, "usr_father")].append(poor_air(end - timedelta(days=2), random.Random(7002)))
    buckets[(TENANT_SUV, VEH_SUV1, "usr_father")].append(favorite_one_shot(end - timedelta(days=1)))
    buckets[(TENANT_SUV, VEH_SUV1, "usr_mother")].append(mother_latte(end - timedelta(days=3), random.Random(7003)))

    return buckets


def write_outputs(buckets: dict[tuple[str, str, str], list[dict[str, Any]]], start: date, end: date, seed: int) -> dict[str, Any]:
    TENANTS_DIR.mkdir(parents=True, exist_ok=True)
    stats: dict[str, Any] = {"tenants": {}}
    all_sessions: list[dict[str, Any]] = []

    for (tenant, vehicle, user_or_shared), sessions in sorted(buckets.items()):
        vehicle_dir = TENANTS_DIR / tenant / vehicle
        vehicle_dir.mkdir(parents=True, exist_ok=True)
        if user_or_shared == "_shared_multi_occupant":
            path = vehicle_dir / "_shared_multi_occupant.jsonl"
        else:
            user_dir = vehicle_dir / user_or_shared
            user_dir.mkdir(parents=True, exist_ok=True)
            path = user_dir / "sessions.jsonl"
        sessions.sort(key=lambda s: s["started_at"])
        with path.open("w", encoding="utf-8") as fh:
            for row in sessions:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        all_sessions.extend(sessions)
        t = stats["tenants"].setdefault(tenant, {"vehicles": {}})
        v = t["vehicles"].setdefault(vehicle, {"users": {}})
        v["users"][user_or_shared] = {"sessions": len(sessions)}
        if user_or_shared != "_shared_multi_occupant":
            persona = dict(PERSONAS.get(user_or_shared, PERSONAS["usr_father"]).to_dict())
            if vehicle == VEH_SUV2:
                persona = CAMPING_PERSONA.to_dict()
            persona["tenant_id"] = tenant
            persona["vehicle_id"] = vehicle
            (user_dir if 'user_dir' in dir() else vehicle_dir / user_or_shared).joinpath("persona.json").write_text(
                json.dumps(persona, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # manifest
    manifest = {
        "dataset": "multitenant_cockpit_sessions",
        "time_range": {"start": start.isoformat(), "end": end.isoformat(), "timezone": "Asia/Shanghai"},
        "seed": seed,
        "session_count": len(all_sessions),
        "matrix": stats["tenants"],
        "tag_counts": dict(sorted(Counter(t for s in all_sessions for t in s["tags"]).items())),
    }
    DEFAULT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def build_eval_cases() -> list[dict[str, Any]]:
    """Isolation (A), filter semantics (B), fuzzy recall (C), traps (D)."""
    cases: list[dict[str, Any]] = []

    # A. isolation
    cases += [
        {"id": "iso_01", "group": "isolation", "op": "search",
         "scope": {"tenant_id": TENANT_SEDAN, "user_id": "usr_father"},
         "query": "通勤习惯", "must": [], "must_not": ["童梦森林", "麦田", "露营"]},
        {"id": "iso_02", "group": "isolation", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_father", "vehicle_id": VEH_SUV1},
         "query": "通勤", "must": ["德赛科技园"], "must_not": ["公司配车", "红花湖"]},
        {"id": "iso_03", "group": "isolation", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_father"},
         "query": "咖啡 拿铁", "must": [], "must_not": ["拿铁", "燕麦奶"]},
        {"id": "iso_04", "group": "isolation", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_mother"},
         "query": "西餐点单", "must": [], "must_not": ["西冷", "蘑菇汤"]},
        {"id": "iso_05", "group": "isolation", "op": "search_dual",
         "scope_a": {"tenant_id": TENANT_SUV, "user_id": "usr_father"},
         "scope_b": {"tenant_id": TENANT_SUV, "user_id": "usr_mother"},
         "query": "我们经常去的亲子餐厅",
         "must_a": ["童梦森林"], "must_b": ["麦田"],
         "not_top1_a": ["麦田"], "not_top1_b": ["童梦森林"]},
        {"id": "iso_06", "group": "isolation", "op": "profile",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_father", "occupant_id": "driver"},
         "query": "音量偏好", "must_not": ["粤剧"]},
        {"id": "iso_07", "group": "isolation", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_child"},
         "query": "我想看的动画片", "must": ["汪汪队"], "must_not": ["通勤轻音乐", "爵士"]},
        {"id": "iso_08", "group": "isolation", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_grandma"},
         "query": "周三的行程", "must": ["市中心医院"], "must_not": ["德赛科技园"]},
        {"id": "iso_09", "group": "isolation", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_father"},
         "query": "医院行程", "must": [], "must_not": ["透析", "市中心医院"]},
    ]

    # B. filter semantics
    cases += [
        {"id": "flt_01", "group": "filter", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_father"},
         "query": "空调习惯", "no_filter": True,
         "expect_behavior": "returns memories across veh_suv_001+veh_suv_002 (backend default: vehicle not isolated)",
         "must": ["23"], "allow": ["24"]},
        {"id": "flt_02", "group": "filter", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_father", "vehicle_id": VEH_SUV1},
         "query": "空调习惯", "filters": {"vehicle_id": VEH_SUV1},
         "must": ["23"], "must_not_vehicle": [VEH_SUV2, VEH_SED1]},
        {"id": "flt_03", "group": "filter", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_father"},
         "query": "媒体偏好", "filters": {"occupant_id": "driver"},
         "must": ["通勤轻音乐"], "must_not": ["汪汪队", "粤剧"]},
        {"id": "flt_04", "group": "filter", "op": "dedup_check",
         "scope": {"tenant_id": TENANT_SEDAN, "user_id": "usr_father"},
         "query": "去公司，空调和音乐照平时通勤习惯来",
         "expect_behavior": "identical content in SUV1 and SED1 → single memory row (tenant,user,hash) dedup; vehicle_id = first-written",
         "must": [], "must_not": []},
    ]

    # C. fuzzy recall per persona (ported from yearlong benchmark, re-scoped)
    cases += [
        {"id": "rc_f_01", "group": "recall", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_father"},
         "query": "我们经常去的亲子餐厅", "must": ["童梦森林"], "not_top1": ["童趣岛"]},
        {"id": "rc_f_02", "group": "recall", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_father"},
         "query": "根据我爱吃的菜，找个最近的地方", "must": ["少油", "香菜", "鲈鱼"]},
        {"id": "rc_f_03", "group": "recall", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_father"},
         "query": "外面的空气质量不好，按老规矩给我调整一下", "must": ["车窗", "内循环", "3档"]},
        {"id": "rc_f_04", "group": "recall", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_father"},
         "query": "帮我找一下西餐厅，按照习惯进行点餐", "must": ["西冷", "蘑菇汤", "柠檬水"]},
        {"id": "rc_f_05", "group": "recall", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_father", "vehicle_id": VEH_SUV2},
         "query": "露营怎么设置", "must": ["后排放倒", "24"]},
        {"id": "rc_m_01", "group": "recall", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_mother"},
         "query": "我的拿铁怎么点", "must": ["少糖", "燕麦奶"]},
        {"id": "rc_m_02", "group": "recall", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_mother"},
         "query": "导航去上班", "must": ["惠州西湖", "市内道路"]},
        {"id": "rc_m_03", "group": "recall", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_mother"},
         "query": "我中午吃什么比较健康", "must": ["藜麦沙拉"]},
        {"id": "rc_g_01", "group": "recall", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_grandma"},
         "query": "我平时听什么", "must": ["粤剧"]},
        {"id": "rc_c_01", "group": "recall", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_child"},
         "query": "我的零食", "must": ["酸奶"]},
    ]

    # D. traps
    cases += [
        {"id": "trap_01", "group": "trap", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_father"},
         "query": "空调习惯温度", "must": ["23"], "not_top1": ["25"]},
        {"id": "trap_02", "group": "trap", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_father"},
         "query": "通勤听什么", "must": ["通勤轻音乐"], "not_top1": ["新闻"]},
        {"id": "trap_03", "group": "trap", "op": "search",
         "scope": {"tenant_id": TENANT_SUV, "user_id": "usr_father"},
         "query": "最近的天气怎么样", "must": [], "not_top1": ["26度", "晴"]},
    ]
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 9, 7))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--seed", type=int, default=20260907)
    args = parser.parse_args()
    if args.days < 30:
        parser.error("--days must be at least 30")
    start = args.end_date - timedelta(days=args.days - 1)
    buckets = generate(start, args.end_date, args.seed)
    manifest = write_outputs(buckets, start, args.end_date, args.seed)
    cases = build_eval_cases()
    DEFAULT_CASES.write_text(json.dumps({"cases": cases}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sessions": manifest["session_count"], "manifest": str(DEFAULT_MANIFEST),
                      "cases": len(cases), "tenants_dir": str(TENANTS_DIR)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
