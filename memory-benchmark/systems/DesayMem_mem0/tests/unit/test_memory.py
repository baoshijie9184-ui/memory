import pytest

from desaymem.core.logging import RedactingFilter, redact_text
from desaymem.core.memory import DesayMemory
from tests.conftest import LIVE_TENANT


async def test_preference_extract_and_search(memory: DesayMemory):
    added = await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        user_id="user_001",
        tenant_id=LIVE_TENANT,
        vehicle_id="vehicle_001",
        scene="driving",
    )
    assert added
    assert any("22" in item["content"] for item in added)
    hits = await memory.search("我习惯多少度", user_id="user_001", tenant_id=LIVE_TENANT, top_k=5)
    assert hits
    assert any("22" in item["content"] for item in hits)


async def test_user_isolation(memory: DesayMemory):
    await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        user_id="user_a",
        tenant_id=LIVE_TENANT,
    )
    await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到25度"}],
        user_id="user_b",
        tenant_id=LIVE_TENANT,
    )
    hits_a = await memory.search("空调温度", user_id="user_a", tenant_id=LIVE_TENANT)
    hits_b = await memory.search("空调温度", user_id="user_b", tenant_id=LIVE_TENANT)
    assert all(item["user_id"] == "user_a" for item in hits_a)
    assert all(item["user_id"] == "user_b" for item in hits_b)
    assert any("22" in item["content"] for item in hits_a)
    assert any("25" in item["content"] for item in hits_b)
    assert not any("25" in item["content"] for item in hits_a)
    assert not any("22" in item["content"] for item in hits_b)


async def test_tenant_isolation(memory: DesayMemory):
    await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        user_id="user_001",
        tenant_id="pytest_oem_a",
    )
    hits = await memory.search("空调", user_id="user_001", tenant_id="pytest_oem_b")
    assert hits == []
    listed = await memory.get_all("user_001", tenant_id="pytest_oem_b")
    assert listed == []


async def test_duplicate_input_does_not_grow(memory: DesayMemory):
    payload = [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}]
    first = await memory.add(payload, user_id="user_001", tenant_id=LIVE_TENANT)
    second = await memory.add(payload, user_id="user_001", tenant_id=LIVE_TENANT)
    listed = await memory.get_all("user_001", tenant_id=LIVE_TENANT)
    assert first
    assert second == []
    semantic = [item for item in listed if item.get("memory_type") == "semantic_memory"]
    assert len(semantic) == len(first)


async def test_cannot_delete_another_users_memory(memory: DesayMemory):
    added = await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        user_id="user_a",
        tenant_id=LIVE_TENANT,
    )
    memory_id = added[0]["id"]
    deleted = await memory.delete(memory_id, user_id="user_b", tenant_id=LIVE_TENANT)
    assert deleted is False
    remaining = await memory.get_all("user_a", tenant_id=LIVE_TENANT)
    assert remaining[0]["id"] == memory_id


async def test_delete_and_delete_all(memory: DesayMemory):
    added = await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        user_id="user_001",
        tenant_id=LIVE_TENANT,
    )
    assert await memory.delete(added[0]["id"], user_id="user_001", tenant_id=LIVE_TENANT)
    await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        user_id="user_001",
        tenant_id=LIVE_TENANT,
    )
    count = await memory.delete_all("user_001", tenant_id=LIVE_TENANT)
    assert count >= 1
    assert await memory.get_all("user_001", tenant_id=LIVE_TENANT) == []


def test_secrets_are_redacted_from_log_text():
    text = "api_key=sk-secret password=superpass postgresql://desaymem:change_me@postgres:5432/desaymem"
    redacted = redact_text(text)
    assert "sk-secret" not in redacted
    assert "superpass" not in redacted
    assert "change_me" not in redacted
    assert "***" in redacted
    assert RedactingFilter().filter.__name__ == "filter"


async def test_metadata_vehicle_filter(memory: DesayMemory):
    await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        user_id="user_001",
        tenant_id=LIVE_TENANT,
        vehicle_id="vehicle_001",
    )
    hits = await memory.search(
        "空调",
        user_id="user_001",
        tenant_id=LIVE_TENANT,
        filters={"vehicle_id": "vehicle_002"},
    )
    assert hits == []


def test_from_settings_rejects_memory_history():
    from desaymem.core.config import Settings
    from desaymem.core.exceptions import ConfigurationError
    from desaymem.stores.memory import InMemoryVectorStore

    settings = Settings(history_db_path=":memory:")
    with pytest.raises(ConfigurationError, match="HISTORY_DB_PATH"):
        DesayMemory.from_settings(settings)

    settings = Settings(history_db_path="history.db")
    with pytest.raises(ConfigurationError, match="in-memory"):
        DesayMemory.from_settings(settings, store=InMemoryVectorStore(embedding_dims=8))


async def test_backend_info_is_persistent(memory: DesayMemory):
    info = memory.backend_info()
    assert "PgVectorStore" in info["memories"]
    assert "PgEntityStore" in info["entities"]
    assert ":memory:" not in info["history"]
