import httpx

from tests.conftest import LIVE_TENANT


async def test_health_and_swagger(api_client: httpx.AsyncClient):
    health = await api_client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    docs = await api_client.get("/docs")
    assert docs.status_code == 200
    schema = await api_client.get("/openapi.json")
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert "/v1/memories" in paths
    assert "/v1/memories/search" in paths
    assert "/v1/users/{user_id}/profile" in paths
    assert "/v1/users/{user_id}/memories/{memory_id}/history" in paths


async def test_add_search_list_delete_api(api_client: httpx.AsyncClient):
    created = await api_client.post(
        "/v1/memories",
        json={
            "tenant_id": LIVE_TENANT,
            "user_id": "user_001",
            "vehicle_id": "vehicle_001",
            "occupant_id": "primary",
            "session_id": "session_001",
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
            "user_id": "user_001",
            "query": "用户习惯的空调温度是多少",
            "top_k": 5,
            "filters": {"vehicle_id": "vehicle_001"},
        },
    )
    assert searched.status_code == 200
    assert any("22" in item["content"] for item in searched.json()["memories"])

    listed = await api_client.get("/v1/users/user_001/memories", params={"tenant_id": LIVE_TENANT})
    assert listed.status_code == 200
    assert listed.json()["count"] >= 1

    denied = await api_client.delete(
        f"/v1/users/user_b/memories/{memory_id}",
        params={"tenant_id": LIVE_TENANT},
    )
    assert denied.status_code == 404

    deleted = await api_client.delete(
        f"/v1/users/user_001/memories/{memory_id}",
        params={"tenant_id": LIVE_TENANT},
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    blocked = await api_client.delete("/v1/users/user_001/memories", params={"tenant_id": LIVE_TENANT})
    assert blocked.status_code == 400
    cleared = await api_client.delete(
        "/v1/users/user_001/memories",
        params={"tenant_id": LIVE_TENANT, "confirm": True},
    )
    assert cleared.status_code == 200


async def test_api_procedural_and_infer_false(api_client: httpx.AsyncClient):
    raw = await api_client.post(
        "/v1/memories",
        json={
            "tenant_id": LIVE_TENANT,
            "user_id": "user_001",
            "infer": False,
            "messages": [{"role": "user", "content": "User likes Tesla Model Y"}],
        },
    )
    assert raw.status_code == 201
    assert raw.json()["memories"][0]["content"] == "User likes Tesla Model Y"

    procedural = await api_client.post(
        "/v1/memories",
        json={
            "tenant_id": LIVE_TENANT,
            "user_id": "user_001",
            "memory_type": "procedural_memory",
            "messages": [
                {"role": "user", "content": "打开座椅加热"},
                {"role": "assistant", "content": "已打开座椅加热"},
            ],
        },
    )
    assert procedural.status_code == 201
    body = procedural.json()["memories"][0]
    assert body["memory_type"] == "procedural_memory"

    bad = await api_client.post(
        "/v1/memories",
        json={
            "tenant_id": LIVE_TENANT,
            "user_id": "user_001",
            "memory_type": "semantic_memory",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert bad.status_code == 400
    assert bad.json()["error_code"] == "VAL_MEMORY_TYPE"

    created = await api_client.post(
        "/v1/memories",
        json={
            "tenant_id": LIVE_TENANT,
            "user_id": "user_hist",
            "messages": [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        },
    )
    assert created.status_code == 201, created.text
    memory_id = created.json()["memories"][0]["id"]
    history = await api_client.get(
        f"/v1/users/user_hist/memories/{memory_id}/history",
        params={"tenant_id": LIVE_TENANT},
    )
    assert history.status_code == 200
    assert history.json()["events"]
    assert history.json()["events"][0]["event"] == "ADD"
    other_user = await api_client.get(
        f"/v1/users/someone_else/memories/{memory_id}/history",
        params={"tenant_id": LIVE_TENANT},
    )
    assert other_user.status_code == 404
    other_tenant = await api_client.get(
        f"/v1/users/user_hist/memories/{memory_id}/history",
        params={"tenant_id": "pytest_oem_b"},
    )
    assert other_tenant.status_code == 404
