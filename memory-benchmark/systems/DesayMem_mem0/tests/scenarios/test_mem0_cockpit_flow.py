"""Live cockpit flow against DashScope + PostgreSQL + SQLite history.db."""

from __future__ import annotations

from desaymem.core.enums import MemoryType
from desaymem.core.memory import DesayMemory
from desaymem.retrieval.lemmatization import lemmatize_for_bm25
from desaymem.stores.sqlite_history import SQLiteHistoryStore
from tests.conftest import LIVE_TENANT

DRIVER = "user_driver"
PASSENGER = "user_passenger"
SESSION = "session_drive_001"


def _print(step: str, detail: str = "") -> None:
    suffix = f"  {detail}" if detail else ""
    print(f"  {step}{suffix}")


async def test_mem0_cockpit_end_to_end_flow(memory: DesayMemory):
    """One sitting: write cockpit turns, retrieve, isolate, history, cleanup."""
    print("\n=== DesayMem_mem0 live cockpit flow ===")
    assert isinstance(memory.db, SQLiteHistoryStore)
    assert memory.db is memory.messages
    assert getattr(memory.db, "db_path", "") != ":memory:"

    print("\n[1] Driver conversations (ADD-only + infer=false facts)")
    climate = await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        user_id=DRIVER,
        tenant_id=LIVE_TENANT,
        vehicle_id="vehicle_001",
        occupant_id="primary",
        session_id=SESSION,
        scene="driving",
    )
    assert climate, "climate preference should be extracted"
    assert any("22" in row["content"] for row in climate)
    _print("ADD climate", climate[0]["content"])

    nav = await memory.add(
        [{"role": "user", "content": '导航去 "上海迪士尼"'}],
        user_id=DRIVER,
        tenant_id=LIVE_TENANT,
        vehicle_id="vehicle_001",
        session_id=SESSION,
        infer=False,
    )
    assert nav and "上海迪士尼" in nav[0]["content"]
    _print("ADD nav (raw)", nav[0]["content"])

    music = await memory.add(
        [
            {"role": "user", "content": "开车时喜欢听周杰伦"},
            {"role": "assistant", "content": "已为你打开周杰伦歌单"},
        ],
        user_id=DRIVER,
        tenant_id=LIVE_TENANT,
        session_id=SESSION,
        infer=False,
    )
    assert any("周杰伦" in row["content"] for row in music)
    _print("ADD music (raw)", music[0]["content"])

    car = await memory.add(
        [{"role": "user", "content": "User likes Tesla Model Y"}],
        user_id=DRIVER,
        tenant_id=LIVE_TENANT,
        session_id=SESSION,
        infer=False,
    )
    assert car[0]["content"] == "User likes Tesla Model Y"
    _print("ADD car (raw)", car[0]["content"])

    print("\n[2] Last-k from the same session is stored")
    await memory.add(
        [{"role": "user", "content": "帮我把座椅加热打开"}],
        user_id=DRIVER,
        tenant_id=LIVE_TENANT,
        session_id=SESSION,
        infer=False,
    )
    last = await memory.messages.get_last_messages(
        f"tenant_id={LIVE_TENANT}&user_id={DRIVER}&occupant_id=primary&session_id={SESSION}",
        limit=10,
    )
    contents = " ".join(row.get("content") or "" for row in last)
    assert "22" in contents or "空调" in contents
    assert "周杰伦" in contents
    _print("last-k count", str(len(last)))

    print("\n[3] Repeat climate utterance is MD5-deduped")
    again = await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        user_id=DRIVER,
        tenant_id=LIVE_TENANT,
        session_id=SESSION,
    )
    assert again == []
    _print("dedup skipped", "empty add result")

    print("\n[4] Passenger has a different climate preference")
    passenger = await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到25度"}],
        user_id=PASSENGER,
        tenant_id=LIVE_TENANT,
        session_id="session_ride_002",
    )
    assert passenger and any("25" in row["content"] for row in passenger)
    _print("ADD passenger climate", passenger[0]["content"])

    print("\n[5] Nine-phase search returns the right facts per user")
    driver_ac = await memory.search("用户习惯的空调温度是多少", user_id=DRIVER, tenant_id=LIVE_TENANT, top_k=5)
    assert any("22" in row["content"] for row in driver_ac)
    assert not any("25" in row["content"] for row in driver_ac)
    _print("driver AC hits", ", ".join(row["content"] for row in driver_ac))

    passenger_ac = await memory.search("空调温度", user_id=PASSENGER, tenant_id=LIVE_TENANT, top_k=5)
    assert any("25" in row["content"] for row in passenger_ac)
    assert not any("22" in row["content"] for row in passenger_ac)
    _print("passenger AC hits", ", ".join(row["content"] for row in passenger_ac))

    music_hits = await memory.search("周杰伦", user_id=DRIVER, tenant_id=LIVE_TENANT, top_k=5)
    assert any("周杰伦" in row["content"] for row in music_hits)
    _print("music hits", ", ".join(row["content"] for row in music_hits))

    nav_hits = await memory.search("上海迪士尼", user_id=DRIVER, tenant_id=LIVE_TENANT, top_k=5)
    assert any("上海迪士尼" in row["content"] for row in nav_hits)
    _print("nav hits", ", ".join(row["content"] for row in nav_hits))

    print("\n[6] BM25 keyword search can hit CJK terms")
    keyword_hits = await memory.store.keyword_search(
        lemmatize_for_bm25("空调"),
        tenant_id=LIVE_TENANT,
        user_id=DRIVER,
        top_k=5,
    )
    assert keyword_hits
    assert any("22" in item.content or "空调" in item.content for item in keyword_hits)
    _print("BM25 空调", keyword_hits[0].content)

    print("\n[7] Entity store links quoted destinations")
    entities = await memory.entities.list(tenant_id=LIVE_TENANT, user_id=DRIVER)
    assert any("上海迪士尼" in item.entity_text or "Tesla" in item.entity_text for item in entities)
    _print("entities", ", ".join(f"{item.entity_type}:{item.entity_text}" for item in entities[:6]))

    print("\n[8] History records ADD then DELETE")
    climate_id = climate[0]["id"]
    events = await memory.history(climate_id)
    assert events and events[0]["event"] == "ADD"
    deleted = await memory.delete(climate_id, user_id=DRIVER, tenant_id=LIVE_TENANT)
    assert deleted is True
    kinds = [item["event"] for item in await memory.history(climate_id)]
    assert "ADD" in kinds and "DELETE" in kinds
    foreign = await memory.delete(nav[0]["id"], user_id=PASSENGER, tenant_id=LIVE_TENANT)
    assert foreign is False
    _print("history", " -> ".join(kinds))

    print("\n[9] Procedural memory is a separate type")
    procedure = await memory.add(
        [
            {"role": "user", "content": "请按步骤打开座椅加热"},
            {"role": "assistant", "content": "1. 打开座椅菜单 2. 选择加热 3. 确认"},
        ],
        user_id=DRIVER,
        tenant_id=LIVE_TENANT,
        memory_type=MemoryType.PROCEDURAL.value,
    )
    assert procedure[0]["memory_type"] == MemoryType.PROCEDURAL.value
    listed = await memory.get_all(
        DRIVER,
        tenant_id=LIVE_TENANT,
        filters={"memory_type": MemoryType.PROCEDURAL.value},
    )
    assert listed and all(row["memory_type"] == MemoryType.PROCEDURAL.value for row in listed)
    _print("procedural", procedure[0]["memory_type"])

    print("\n[10] delete_all clears vector / last-k / entities for that user")
    cleared = await memory.delete_all(DRIVER, tenant_id=LIVE_TENANT)
    assert cleared >= 1
    assert await memory.get_all(DRIVER, tenant_id=LIVE_TENANT) == []
    assert await memory.entities.list(tenant_id=LIVE_TENANT, user_id=DRIVER) == []
    leftover = await memory.messages.get_last_messages(
        f"tenant_id={LIVE_TENANT}&user_id={DRIVER}&occupant_id=primary&session_id={SESSION}"
    )
    assert leftover == []
    still_passenger = await memory.get_all(PASSENGER, tenant_id=LIVE_TENANT)
    assert still_passenger and any("25" in row["content"] for row in still_passenger)
    _print("delete_all driver", f"removed={cleared}, passenger kept={len(still_passenger)}")
    print("=== live cockpit flow passed ===\n")


async def test_mem0_cockpit_http_roundtrip(api_client):
    """Same climate fact through FastAPI with live stores."""
    created = await api_client.post(
        "/v1/memories",
        json={
            "tenant_id": LIVE_TENANT,
            "user_id": DRIVER,
            "vehicle_id": "vehicle_001",
            "session_id": SESSION,
            "scene": "driving",
            "messages": [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["memories"]
    memory_id = body["memories"][0]["id"]

    searched = await api_client.post(
        "/v1/memories/search",
        json={
            "tenant_id": LIVE_TENANT,
            "user_id": DRIVER,
            "query": "用户习惯的空调温度是多少",
            "top_k": 5,
        },
    )
    assert searched.status_code == 200
    assert any("22" in item["content"] for item in searched.json()["memories"])

    leaked = await api_client.post(
        "/v1/memories/search",
        json={"tenant_id": LIVE_TENANT, "user_id": PASSENGER, "query": "空调温度", "top_k": 5},
    )
    assert leaked.status_code == 200
    assert leaked.json()["memories"] == []

    history = await api_client.get(
        f"/v1/users/{DRIVER}/memories/{memory_id}/history",
        params={"tenant_id": LIVE_TENANT},
    )
    assert history.status_code == 200
    assert history.json()["events"][0]["event"] == "ADD"

    denied = await api_client.delete(
        f"/v1/users/{PASSENGER}/memories/{memory_id}",
        params={"tenant_id": LIVE_TENANT},
    )
    assert denied.status_code == 404
