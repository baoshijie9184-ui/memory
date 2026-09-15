import pytest

from desaymem.core.enums import MemoryType
from desaymem.core.exceptions import ValidationError
from desaymem.core.memory import DesayMemory
from desaymem.extraction.entities import extract_entities
from desaymem.retrieval.lemmatization import lemmatize_for_bm25
from desaymem.retrieval.scoring import ENTITY_BOOST_WEIGHT, get_bm25_params, normalize_bm25, score_and_rank
from desaymem.stores.entities import PgEntityStore
from desaymem.stores.pgvector import PgVectorStore
from desaymem.stores.sqlite_history import SQLiteHistoryStore
from tests.conftest import LIVE_TENANT


def test_extract_entities_quoted_identifier_and_proper():
    found = extract_entities('Navigate to "Shanghai Disney" with Tesla Model Y via api.v1.nav')
    types = {text: kind for kind, text in found}
    assert "Shanghai Disney" in types
    assert types["Shanghai Disney"] == "QUOTED"
    assert any("Tesla" in text for text in types)
    assert any("api.v1.nav" in text for text in types)


def test_extract_entities_cjk_named():
    found = extract_entities("导航去上海迪士尼")
    texts = [text for _, text in found]
    assert "上海迪士尼" in texts


def test_score_and_rank_includes_entity_boost():
    ranked = score_and_rank(
        semantic_results=[
            {"id": "a", "score": 0.8},
            {"id": "b", "score": 0.7},
        ],
        bm25_scores={},
        entity_boosts={"b": ENTITY_BOOST_WEIGHT},
        threshold=0.1,
        top_k=2,
    )
    assert ranked[0]["id"] == "b"


def test_score_and_rank_accepts_keyword_only_candidates():
    ranked = score_and_rank(
        semantic_results=[
            {"id": "a", "score": 0.55},
            {"id": "b", "score": 0.70},
            {"id": "keyword_only", "score": 0.0},
        ],
        bm25_scores={"a": 1.0, "keyword_only": 1.0},
        entity_boosts={},
        threshold=0.1,
        top_k=2,
    )
    assert [row["id"] for row in ranked] == ["a", "keyword_only"]
    # Score changed from 0.775 to ~0.721 because max_possible now includes
    # TEMPORAL_WEIGHT (0.15) in the denominator even when temporal_score is 0.
    # raw = 0.55 + 1.0 + 0 + 0 = 1.55; max = 1.0 + 1.0 + 0.15 = 2.15
    assert ranked[0]["score"] == pytest.approx(1.55 / 2.15, rel=1e-6)


async def test_last_k_messages_are_stored(memory: DesayMemory):
    await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        user_id="user_001",
        tenant_id=LIVE_TENANT,
        session_id="session_001",
    )
    await memory.add(
        [{"role": "user", "content": "帮我导航去公司"}],
        user_id="user_001",
        tenant_id=LIVE_TENANT,
        session_id="session_001",
        infer=False,
    )
    last = await memory.messages.get_last_messages(
        f"tenant_id={LIVE_TENANT}&user_id=user_001&occupant_id=primary&session_id=session_001",
        limit=10,
    )
    assert len(last) >= 2
    blob = " ".join(row.get("content") or "" for row in last)
    assert "空调" in blob or "22" in blob
    assert "公司" in blob


async def test_procedural_memory_type_is_written(memory: DesayMemory):
    added = await memory.add(
        [
            {"role": "user", "content": "请按步骤打开座椅加热"},
            {"role": "assistant", "content": "1. 打开座椅菜单 2. 选择加热 3. 确认"},
        ],
        user_id="user_001",
        tenant_id=LIVE_TENANT,
        memory_type=MemoryType.PROCEDURAL.value,
    )
    assert added
    assert added[0]["memory_type"] == MemoryType.PROCEDURAL.value
    assert "座椅加热" in added[0]["content"] or "加热" in added[0]["content"]
    listed = await memory.get_all(
        "user_001",
        tenant_id=LIVE_TENANT,
        filters={"memory_type": MemoryType.PROCEDURAL.value},
    )
    assert listed
    assert all(item["memory_type"] == MemoryType.PROCEDURAL.value for item in listed)


async def test_invalid_memory_type_is_rejected(memory: DesayMemory):
    try:
        await memory.add(
            [{"role": "user", "content": "hello"}],
            user_id="user_001",
            tenant_id=LIVE_TENANT,
            memory_type="semantic_memory",
        )
        raise AssertionError("expected ValidationError")
    except ValidationError as exc:
        assert exc.error_code == "VAL_MEMORY_TYPE"


async def test_infer_false_stores_raw_message(memory: DesayMemory):
    added = await memory.add(
        [{"role": "user", "content": "User likes Tesla Model Y"}],
        user_id="user_001",
        tenant_id=LIVE_TENANT,
        infer=False,
    )
    assert added
    assert added[0]["content"] == "User likes Tesla Model Y"


async def test_entity_store_links_and_unlinks(memory: DesayMemory):
    added = await memory.add(
        [{"role": "user", "content": 'Navigate to "Shanghai Disney" with Tesla Model Y'}],
        user_id="user_001",
        tenant_id=LIVE_TENANT,
        infer=False,
    )
    entities = await memory.entities.list(tenant_id=LIVE_TENANT, user_id="user_001")
    assert entities
    assert any("Shanghai Disney" in item.entity_text or "Tesla" in item.entity_text for item in entities)
    assert any(added[0]["id"] in item.linked_memory_ids for item in entities)
    await memory.delete(added[0]["id"], user_id="user_001", tenant_id=LIVE_TENANT)
    remaining = await memory.entities.list(tenant_id=LIVE_TENANT, user_id="user_001")
    assert all(added[0]["id"] not in item.linked_memory_ids for item in remaining)


async def test_delete_all_clears_messages_and_entities(memory: DesayMemory):
    await memory.add(
        [{"role": "user", "content": 'Go to "Shanghai Disney"'}],
        user_id="user_001",
        tenant_id=LIVE_TENANT,
        session_id="session_001",
        infer=False,
    )
    count = await memory.delete_all("user_001", tenant_id=LIVE_TENANT)
    assert count >= 1
    entities = await memory.entities.list(tenant_id=LIVE_TENANT, user_id="user_001")
    messages = await memory.messages.get_last_messages(
        f"tenant_id={LIVE_TENANT}&user_id=user_001&occupant_id=primary&session_id=session_001"
    )
    assert entities == []
    assert messages == []


def test_lemmatize_keeps_cjk_and_ascii():
    tokens = lemmatize_for_bm25("用户喜欢把空调调到22度 Tesla")
    assert "空调" in tokens
    assert "tesla" in tokens.lower()
    midpoint, steepness = get_bm25_params("空调", lemmatized="空调")
    assert normalize_bm25(3.0, midpoint, steepness) > 0


async def test_seven_phase_add_writes_history_lemma_and_three_stores(memory: DesayMemory):
    added = await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        user_id="user_001",
        tenant_id=LIVE_TENANT,
        session_id="session_001",
    )
    assert added
    row = added[0]
    assert row.get("text_lemmatized") or "空调" in (row.get("content") or "")
    events = await memory.history(row["id"])
    assert events
    assert events[0]["event"] == "ADD"
    assert events[0]["new_memory"]
    last = await memory.messages.get_last_messages(
        f"tenant_id={LIVE_TENANT}&user_id=user_001&occupant_id=primary&session_id=session_001"
    )
    assert last
    assert isinstance(memory.store, PgVectorStore)
    assert isinstance(memory.entities, PgEntityStore)
    assert isinstance(memory.db, SQLiteHistoryStore)
    assert memory.db is memory.messages
    assert getattr(memory.db, "db_path", "") != ":memory:"


async def test_nine_phase_search_uses_bm25_keyword_hit(memory: DesayMemory):
    await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        user_id="user_001",
        tenant_id=LIVE_TENANT,
    )
    await memory.add(
        [{"role": "user", "content": 'Navigate to "Shanghai Disney"'}],
        user_id="user_001",
        tenant_id=LIVE_TENANT,
        infer=False,
    )
    keyword_hits = await memory.store.keyword_search(
        lemmatize_for_bm25("空调"),
        tenant_id=LIVE_TENANT,
        user_id="user_001",
        top_k=5,
    )
    assert keyword_hits
    assert any("22" in item.content or "空调" in item.content for item in keyword_hits)
    hits = await memory.search("空调温度", user_id="user_001", tenant_id=LIVE_TENANT, top_k=5)
    assert hits
    assert any("22" in item["content"] for item in hits)


async def test_history_records_delete(memory: DesayMemory):
    added = await memory.add(
        [{"role": "user", "content": "我开车的时候喜欢把空调调到22度"}],
        user_id="user_001",
        tenant_id=LIVE_TENANT,
    )
    memory_id = added[0]["id"]
    await memory.delete(memory_id, user_id="user_001", tenant_id=LIVE_TENANT)
    events = await memory.history(memory_id)
    kinds = [item["event"] for item in events]
    assert "ADD" in kinds
    assert "DELETE" in kinds
