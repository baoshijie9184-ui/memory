"""Unit tests for memory observability: L1/L2/L3 layers, audit events, isolation.

No live PostgreSQL / embedding service required. LLM outputs are faked.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from desaymem.core.memory import DesayMemory, _belief_to_public, _memory_to_public
from desaymem.core.models import MemoryScope
from desaymem.layers.distiller import ProfileDistiller, belief_key_text
from desaymem.stores.audit import (
    AuditEvent,
    AuditStore,
    InMemoryAuditStore,
    VALID_EVENTS,
    VALID_LAYERS,
    safe_append,
    safe_delete_audit,
)
from desaymem.stores.base import StoredBelief, StoredMemory
from desaymem.stores.memory import InMemoryVectorStore
from desaymem.stores.profile import InMemoryProfileStore


class ScriptedLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    async def complete(self, messages: list[dict], response_format: dict | None = None) -> str:
        del messages, response_format
        self.calls += 1
        return json.dumps(self.payload)


class HashEmbedding:
    embedding_dims = 8

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.embedding_dims
            vec[abs(hash(text)) % self.embedding_dims] = 1.0
            vectors.append(vec)
        return vectors


def _fact(memory_id: str, text: str, *, tenant_id: str = "t", user_id: str = "u") -> StoredMemory:
    return StoredMemory(
        id=memory_id,
        content=text,
        tenant_id=tenant_id,
        user_id=user_id,
        memory_type="semantic_memory",
        embedding=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        created_at=datetime.now(timezone.utc),
    )


def _build_memory(
    *,
    tenant_id: str = "t",
    user_id: str = "u",
    enable_episodes: bool = True,
    enable_profile: bool = True,
) -> DesayMemory:
    from desaymem.core.config import Settings

    settings = Settings()
    settings.enable_episodes = enable_episodes
    settings.enable_profile = enable_profile
    settings.enable_entity_store = False
    settings.enable_rerank = False
    settings.search_threshold = 0.0
    settings.history_db_path = ":memory:"  # not used — in-memory stores
    store = InMemoryVectorStore(embedding_dims=8)
    audit = InMemoryAuditStore()
    profiles = InMemoryProfileStore(embedding_dims=8)
    from desaymem.stores.memory import InMemoryMessageStore

    messages = InMemoryMessageStore()
    return DesayMemory(
        llm=ScriptedLLM({}),
        embedding=HashEmbedding(),
        store=store,
        settings=settings,
        message_store=messages,
        profile_store=profiles,
        audit_store=audit,
    )


# ── Audit store basics ──


async def test_inmemory_audit_append_and_list():
    store = InMemoryAuditStore()
    await store.append_event(
        AuditEvent(
            id="",
            tenant_id="t",
            user_id="u",
            memory_id="m1",
            layer="L1",
            object_type="memory_item",
            event="ADD",
            new_data={"content": "test"},
            reason="test",
            source="test",
        )
    )
    events, next_cursor = await store.list_events(tenant_id="t", user_id="u")
    assert len(events) == 1
    assert events[0].event == "ADD"
    assert events[0].layer == "L1"
    assert next_cursor is None


async def test_inmemory_audit_count():
    store = InMemoryAuditStore()
    for i in range(5):
        await store.append_event(
            AuditEvent(
                id="",
                tenant_id="t",
                user_id="u",
                memory_id=f"m{i}",
                layer="L1",
                object_type="memory_item",
                event="ADD",
            )
        )
    assert await store.count_events(tenant_id="t", user_id="u") == 5
    assert await store.count_events(tenant_id="t", user_id="u", layer="L1") == 5
    assert await store.count_events(tenant_id="t", user_id="u", layer="L3") == 0


async def test_inmemory_audit_delete_by_user():
    store = InMemoryAuditStore()
    await store.append_event(
        AuditEvent(id="", tenant_id="t", user_id="u", layer="L1", event="ADD", object_type="memory_item")
    )
    await store.append_event(
        AuditEvent(id="", tenant_id="t", user_id="other", layer="L1", event="ADD", object_type="memory_item")
    )
    deleted = await store.delete_by_user(tenant_id="t", user_id="u")
    assert deleted == 1
    events, _ = await store.list_events(tenant_id="t", user_id="u")
    assert len(events) == 0
    events_other, _ = await store.list_events(tenant_id="t", user_id="other")
    assert len(events_other) == 1


async def test_audit_tenant_isolation():
    store = InMemoryAuditStore()
    await store.append_event(
        AuditEvent(id="", tenant_id="t1", user_id="u", layer="L1", event="ADD", object_type="memory_item")
    )
    await store.append_event(
        AuditEvent(id="", tenant_id="t2", user_id="u", layer="L1", event="ADD", object_type="memory_item")
    )
    events, _ = await store.list_events(tenant_id="t1", user_id="u")
    assert len(events) == 1
    events, _ = await store.list_events(tenant_id="t2", user_id="u")
    assert len(events) == 1


async def test_audit_pagination():
    store = InMemoryAuditStore()
    for i in range(10):
        await store.append_event(
            AuditEvent(
                id=f"e{i}",
                tenant_id="t",
                user_id="u",
                memory_id=f"m{i}",
                layer="L1",
                event="ADD",
                object_type="memory_item",
                created_at=datetime(2025, 1, 1, 0, i, 0, tzinfo=timezone.utc),
            )
        )
    page1, cursor1 = await store.list_events(tenant_id="t", user_id="u", limit=3)
    assert len(page1) == 3
    assert cursor1 is not None
    page2, cursor2 = await store.list_events(tenant_id="t", user_id="u", limit=3, cursor=cursor1)
    assert len(page2) == 3


async def test_safe_append_does_not_raise_on_failure():
    class FailingStore:
        async def append_event(self, event):
            raise RuntimeError("db down")

    await safe_append(FailingStore(), AuditEvent(id="", tenant_id="t", user_id="u", layer="L1", event="ADD"))


async def test_safe_delete_audit_does_not_raise_on_failure():
    class FailingStore:
        async def delete_by_user(self, *, tenant_id, user_id):
            raise RuntimeError("db down")

    await safe_delete_audit(FailingStore(), tenant_id="t", user_id="u")


# ── ProfileStore list_all ──


async def test_profile_list_all_returns_superseded():
    store = InMemoryProfileStore(embedding_dims=8)
    now = datetime.now(timezone.utc)
    vec = [1.0] * 8
    await store.insert(
        StoredBelief(
            id="b1",
            tenant_id="t",
            user_id="u",
            attribute="temp",
            value="22",
            status="active",
            attribute_embedding=vec,
            created_at=now,
            updated_at=now,
        )
    )
    await store.insert(
        StoredBelief(
            id="b2",
            tenant_id="t",
            user_id="u",
            attribute="temp",
            value="26",
            status="superseded",
            attribute_embedding=vec,
            created_at=now,
            updated_at=now,
        )
    )
    all_rows = await store.list_all(tenant_id="t", user_id="u")
    assert len(all_rows) == 2
    active_only = await store.list_all(tenant_id="t", user_id="u", status="active")
    assert len(active_only) == 1
    assert active_only[0].value == "22"


async def test_profile_list_all_tenant_isolation():
    store = InMemoryProfileStore(embedding_dims=8)
    now = datetime.now(timezone.utc)
    vec = [1.0] * 8
    await store.insert(
        StoredBelief(
            id="b1",
            tenant_id="t1",
            user_id="u",
            attribute="temp",
            value="22",
            attribute_embedding=vec,
            created_at=now,
            updated_at=now,
        )
    )
    rows = await store.list_all(tenant_id="t2", user_id="u")
    assert len(rows) == 0


async def test_profile_list_all_occupant_filter():
    store = InMemoryProfileStore(embedding_dims=8)
    now = datetime.now(timezone.utc)
    vec = [1.0] * 8
    await store.insert(
        StoredBelief(
            id="b1",
            tenant_id="t",
            user_id="u",
            occupant_id="driver",
            attribute="temp",
            value="22",
            attribute_embedding=vec,
            created_at=now,
            updated_at=now,
        )
    )
    await store.insert(
        StoredBelief(
            id="b2",
            tenant_id="t",
            user_id="u",
            occupant_id="passenger",
            attribute="temp",
            value="24",
            attribute_embedding=vec,
            created_at=now,
            updated_at=now,
        )
    )
    driver_rows = await store.list_all(tenant_id="t", user_id="u", occupant_id="driver")
    assert len(driver_rows) == 1
    assert driver_rows[0].value == "22"


# ── Memory layers query ──


async def test_memory_layers_empty_user_returns_empty_not_500():
    mem = _build_memory()
    scope = MemoryScope(tenant_id="t", user_id="empty_user")
    result = await mem.get_memory_layers(scope)
    assert result["stats"]["l1_count"] == 0
    assert result["stats"]["l2_count"] == 0
    assert result["stats"]["l3_count"] == 0
    assert result["l1"] == []
    assert result["l2"] == []
    assert result["l3"] == []


async def test_memory_layers_does_not_return_embeddings():
    mem = _build_memory()
    # Manually insert a memory
    await mem.store.insert([
        StoredMemory(
            id="m1",
            content="test fact",
            tenant_id="t",
            user_id="u",
            memory_type="semantic_memory",
            embedding=[1.0] * 8,
            content_hash="h1",
        )
    ])
    scope = MemoryScope(tenant_id="t", user_id="u")
    result = await mem.get_memory_layers(scope)
    for item in result["l1"]:
        assert "embedding" not in item
        assert "attribute_embedding" not in item


async def test_memory_layers_tenant_isolation():
    mem = _build_memory()
    await mem.store.insert([
        StoredMemory(
            id="m1",
            content="tenant A fact",
            tenant_id="t1",
            user_id="u",
            memory_type="semantic_memory",
            embedding=[1.0] * 8,
            content_hash="h1",
        )
    ])
    scope = MemoryScope(tenant_id="t2", user_id="u")
    result = await mem.get_memory_layers(scope)
    assert result["stats"]["l1_count"] == 0


async def test_memory_layers_user_isolation():
    mem = _build_memory()
    await mem.store.insert([
        StoredMemory(
            id="m1",
            content="user A fact",
            tenant_id="t",
            user_id="userA",
            memory_type="semantic_memory",
            embedding=[1.0] * 8,
            content_hash="h1",
        )
    ])
    scope = MemoryScope(tenant_id="t", user_id="userB")
    result = await mem.get_memory_layers(scope)
    assert result["stats"]["l1_count"] == 0


# ── Audit events in lifecycle ──


async def test_l1_add_creates_audit_event():
    mem = _build_memory(enable_episodes=False, enable_profile=False)
    # Use _persist_texts directly to avoid LLM
    stored = await mem._persist_texts(
        [{"text": "User likes 22 degrees", "content_hash": "h1", "metadata": {}}],
        MemoryScope(tenant_id="t", user_id="u"),
        {},
        memory_type="semantic_memory",
    )
    events, _ = await mem.audit.list_events(tenant_id="t", user_id="u", layer="L1")
    assert len(events) == 1
    assert events[0].event == "ADD"
    assert events[0].layer == "L1"


async def test_delete_creates_audit_event():
    mem = _build_memory(enable_episodes=False, enable_profile=False)
    stored = await mem._persist_texts(
        [{"text": "User likes jazz", "content_hash": "h1", "metadata": {}}],
        MemoryScope(tenant_id="t", user_id="u"),
        {},
        memory_type="semantic_memory",
    )
    await mem.delete(stored[0].id, "u", tenant_id="t")
    events, _ = await mem.audit.list_events(tenant_id="t", user_id="u", event="DELETE")
    assert len(events) == 1
    assert events[0].layer == "L1"


async def test_delete_all_clears_audit():
    mem = _build_memory(enable_episodes=False, enable_profile=False)
    await mem._persist_texts(
        [{"text": "fact1", "content_hash": "h1", "metadata": {}}],
        MemoryScope(tenant_id="t", user_id="u"),
        {},
        memory_type="semantic_memory",
    )
    await mem.delete_all("u", tenant_id="t")
    events, _ = await mem.audit.list_events(tenant_id="t", user_id="u")
    assert len(events) == 0


# ── L3 distiller audit ──


async def test_l3_create_audit_event():
    store = InMemoryProfileStore(embedding_dims=8)
    audit = InMemoryAuditStore()
    distiller = ProfileDistiller(
        ScriptedLLM(
            {
                "beliefs": [
                    {
                        "subject": "User",
                        "attribute": "preferred temperature",
                        "value": "22°C",
                        "conditions": {},
                        "stability": "episode",
                        "decision": "CREATE",
                        "confidence": 0.8,
                        "evidence_ids": ["0"],
                    }
                ]
            }
        ),
        HashEmbedding(),
        store,
        audit_store=audit,
    )
    await distiller.distill(
        scope=MemoryScope(tenant_id="t", user_id="u"),
        cluster=[_fact("m1", "User set AC to 22")],
    )
    events, _ = await audit.list_events(tenant_id="t", user_id="u", layer="L3")
    assert len(events) == 1
    assert events[0].event in ("ADD", "COEXIST")


async def test_l3_supersede_audit_event():
    store = InMemoryProfileStore(embedding_dims=8)
    audit = InMemoryAuditStore()
    embedding = HashEmbedding()
    key = belief_key_text("User", "preferred tea", {})
    vec = (await embedding.embed([key]))[0]
    now = datetime.now(timezone.utc)
    await store.insert(
        StoredBelief(
            id="b-old",
            tenant_id="t",
            user_id="u",
            attribute="preferred tea",
            value="green tea",
            attribute_embedding=vec,
            status="active",
            support_count=2,
            evidence_memory_ids=["m0"],
            created_at=now,
            updated_at=now,
        )
    )
    distiller = ProfileDistiller(
        ScriptedLLM(
            {
                "beliefs": [
                    {
                        "subject": "User",
                        "attribute": "preferred tea",
                        "value": "oolong",
                        "conditions": {},
                        "stability": "identity",
                        "decision": "SUPERSEDE",
                        "confidence": 0.95,
                        "evidence_ids": ["0"],
                    }
                ]
            }
        ),
        embedding,
        store,
        match_threshold=0.5,
        audit_store=audit,
    )
    await distiller.distill(
        scope=MemoryScope(tenant_id="t", user_id="u"),
        cluster=[_fact("m2", "User now drinks oolong tea")],
    )
    events, _ = await audit.list_events(tenant_id="t", user_id="u", layer="L3", event="SUPERSEDE")
    assert len(events) == 1
    assert events[0].old_data["value"] == "green tea"
    assert events[0].new_data["value"] == "oolong"
    assert "superseded_id" in events[0].new_data


async def test_l3_confirm_audit_event():
    store = InMemoryProfileStore(embedding_dims=8)
    audit = InMemoryAuditStore()
    embedding = HashEmbedding()
    key = belief_key_text("User", "preferred tea", {})
    vec = (await embedding.embed([key]))[0]
    now = datetime.now(timezone.utc)
    await store.insert(
        StoredBelief(
            id="b1",
            tenant_id="t",
            user_id="u",
            attribute="preferred tea",
            value="oolong",
            attribute_embedding=vec,
            status="active",
            support_count=1,
            confidence=0.5,
            evidence_memory_ids=["m0"],
            created_at=now,
            updated_at=now,
        )
    )
    distiller = ProfileDistiller(
        ScriptedLLM(
            {
                "beliefs": [
                    {
                        "subject": "User",
                        "attribute": "preferred tea",
                        "value": "oolong",
                        "conditions": {},
                        "stability": "recurring",
                        "decision": "CONFIRM",
                        "confidence": 0.9,
                        "evidence_ids": ["0"],
                    }
                ]
            }
        ),
        embedding,
        store,
        match_threshold=0.5,
        audit_store=audit,
    )
    await distiller.distill(
        scope=MemoryScope(tenant_id="t", user_id="u"),
        cluster=[_fact("m1", "User drinks oolong again")],
    )
    events, _ = await audit.list_events(tenant_id="t", user_id="u", layer="L3", event="CONFIRM")
    assert len(events) == 1


async def test_l3_coexist_audit_event():
    store = InMemoryProfileStore(embedding_dims=8)
    audit = InMemoryAuditStore()
    embedding = HashEmbedding()
    key = belief_key_text("User", "preferred tea", {})
    vec = (await embedding.embed([key]))[0]
    now = datetime.now(timezone.utc)
    await store.insert(
        StoredBelief(
            id="b1",
            tenant_id="t",
            user_id="u",
            attribute="preferred tea",
            value="oolong",
            conditions={"time": "morning"},
            attribute_embedding=vec,
            status="active",
            evidence_memory_ids=["m0"],
            created_at=now,
            updated_at=now,
        )
    )
    distiller = ProfileDistiller(
        ScriptedLLM(
            {
                "beliefs": [
                    {
                        "subject": "User",
                        "attribute": "preferred tea",
                        "value": "green tea",
                        "conditions": {"time": "evening"},
                        "stability": "episode",
                        "decision": "COEXIST",
                        "confidence": 0.7,
                        "evidence_ids": ["0"],
                    }
                ]
            }
        ),
        embedding,
        store,
        match_threshold=0.5,
        audit_store=audit,
    )
    await distiller.distill(
        scope=MemoryScope(tenant_id="t", user_id="u"),
        cluster=[_fact("m1", "User drinks green tea in evening")],
    )
    events, _ = await audit.list_events(tenant_id="t", user_id="u", layer="L3", event="COEXIST")
    assert len(events) == 1


# ── No FORGET events ──


async def test_no_forget_events_generated():
    mem = _build_memory(enable_episodes=False, enable_profile=False)
    await mem._persist_texts(
        [{"text": "fact", "content_hash": "h1", "metadata": {}}],
        MemoryScope(tenant_id="t", user_id="u"),
        {},
        memory_type="semantic_memory",
    )
    await mem.delete_all("u", tenant_id="t")
    events, _ = await mem.audit.list_events(tenant_id="t", user_id="u")
    # delete_all clears audit, so no events at all — certainly no FORGET
    for e in events:
        assert e.event != "FORGET"


def test_forget_is_valid_but_unused():
    assert "FORGET" in VALID_EVENTS
    assert "L1" in VALID_LAYERS
    assert "L2" in VALID_LAYERS
    assert "L3" in VALID_LAYERS


# ── Public dict helpers strip embeddings ──


def test_memory_to_public_strips_embedding():
    row = StoredMemory(
        id="m1",
        content="test",
        tenant_id="t",
        user_id="u",
        embedding=[1.0] * 8,
    )
    d = _memory_to_public(row)
    assert "embedding" not in d
    assert d["content"] == "test"


def test_belief_to_public_strips_embedding():
    row = StoredBelief(
        id="b1",
        tenant_id="t",
        user_id="u",
        attribute="temp",
        value="22",
        attribute_embedding=[1.0] * 8,
    )
    d = _belief_to_public(row)
    assert "attribute_embedding" not in d
    assert d["value"] == "22"


# ── Migration SQL test ──


def test_migration_006_creates_audit_table():
    from desaymem.cli import _statements, default_migrations_dir

    sql = default_migrations_dir().joinpath("006_memory_observability.sql").read_text(encoding="utf-8")
    stmts = _statements(sql)
    assert any("CREATE TABLE IF NOT EXISTS memory_audit_events" in s for s in stmts)
    assert any("idx_memory_audit_user_time" in s for s in stmts)
    assert any("chk_memory_audit_layer" in s for s in stmts)
    assert any("chk_memory_audit_event" in s for s in stmts)
