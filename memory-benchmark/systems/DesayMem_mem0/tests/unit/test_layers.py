"""Unit tests for L2 episodes and L3 profile distillation.

No live PostgreSQL / embedding service required. LLM outputs are faked.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from desaymem.core.memory import DesayMemory, _absolute_observation_time, _episode_time_bounds
from desaymem.core.exceptions import ValidationError
from desaymem.core.models import MemoryScope, ProfileView
from desaymem.extraction.parser import parse_json_payload
from desaymem.layers.distiller import ProfileDistiller, assemble_narrative, belief_key_text
from desaymem.layers.episodes import EpisodeBuilder, EpisodeJudgment, mean_pool
from desaymem.layers.prompts import EPISODE_SYSTEM_PROMPT, RERANK_SYSTEM_PROMPT
from desaymem.layers.reranker import SemanticReranker
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


def _fact(memory_id: str, text: str, *, embedding: list[float] | None = None) -> StoredMemory:
    return StoredMemory(
        id=memory_id,
        content=text,
        tenant_id="t",
        user_id="u",
        memory_type="semantic_memory",
        embedding=embedding or [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        created_at=datetime.now(timezone.utc),
    )


def test_parse_json_payload_accepts_object_and_array():
    assert parse_json_payload('{"a": 1}')["a"] == 1
    assert parse_json_payload("[1, 2]") == [1, 2]
    assert parse_json_payload("not json") is None


async def test_episode_builder_parses_continue_false_without_active():
    llm = ScriptedLLM({"continues": True, "episode_summary": "User visited a park", "confidence": 0.9})
    builder = EpisodeBuilder(llm)
    judgment = await builder.judge(
        messages=[{"role": "user", "content": "we went to the park"}],
        new_facts=[_fact("m1", "User went to the park")],
        active=None,
    )
    assert isinstance(judgment, EpisodeJudgment)
    assert judgment.continues is False
    assert "park" in judgment.summary


async def test_in_memory_active_episode_roundtrip():
    store = InMemoryVectorStore(embedding_dims=8)
    vec = [0.1] * 8
    active = StoredMemory(
        id="ep1",
        content="Trip to the park",
        tenant_id="t",
        user_id="u",
        occupant_id="primary",
        memory_type="episodic_memory",
        embedding=vec,
        metadata={"episode_status": "active", "source_memory_ids": ["m1"]},
        content_hash="h1",
    )
    await store.insert([active])
    found = await store.get_active_episode(tenant_id="t", user_id="u")
    assert found is not None
    assert found.id == "ep1"
    active.metadata = {"episode_status": "complete", "source_memory_ids": ["m1"]}
    active.content = "Trip to the park (done)"
    active.content_hash = "h2"
    assert await store.update(active)
    assert await store.get_active_episode(tenant_id="t", user_id="u") is None


async def test_distiller_drops_beliefs_without_grounded_evidence():
    store = InMemoryProfileStore(embedding_dims=8)
    distiller = ProfileDistiller(
        ScriptedLLM(
            {
                "beliefs": [
                    {
                        "subject": "User",
                        "attribute": "favorite park",
                        "value": "Central Park",
                        "conditions": {},
                        "stability": "episode",
                        "decision": "CREATE",
                        "confidence": 0.8,
                        "evidence_ids": ["missing"],
                    }
                ]
            }
        ),
        HashEmbedding(),
        store,
    )
    applied = await distiller.distill(
        scope=MemoryScope(tenant_id="t", user_id="u"),
        cluster=[_fact("m1", "User went to Central Park")],
    )
    assert applied == 0
    assert await store.list_active(tenant_id="t", user_id="u") == []


async def test_distiller_downgrades_recurring_with_single_evidence():
    store = InMemoryProfileStore(embedding_dims=8)
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
                        "decision": "CREATE",
                        "confidence": 0.9,
                        "evidence_ids": ["0"],
                    }
                ]
            }
        ),
        HashEmbedding(),
        store,
    )
    applied = await distiller.distill(
        scope=MemoryScope(tenant_id="t", user_id="u"),
        cluster=[_fact("m1", "User drank oolong tea")],
    )
    assert applied == 1
    rows = await store.list_active(tenant_id="t", user_id="u")
    assert len(rows) == 1
    assert rows[0].stability == "episode"
    assert rows[0].evidence_memory_ids == ["m1"]


async def test_distiller_supersede_keeps_old_row():
    store = InMemoryProfileStore(embedding_dims=8)
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
    )
    applied = await distiller.distill(
        scope=MemoryScope(tenant_id="t", user_id="u"),
        cluster=[_fact("m2", "User now drinks oolong tea")],
    )
    assert applied == 1
    active = await store.list_active(tenant_id="t", user_id="u")
    assert len(active) == 1
    assert active[0].value == "oolong"
    assert any(item.status == "superseded" and item.value == "green tea" for item in store._items)


async def test_reranker_keeps_selected_subset_and_falls_back():
    rows = [
        _fact("a", "User likes oolong tea"),
        _fact("b", "User visited a park last week"),
        _fact("c", "User bought a new kettle"),
    ]
    reranker = SemanticReranker(ScriptedLLM({"selected_ids": ["1"], "time_scope": None}))
    picked = await reranker.select("what happened last week", rows, profile=ProfileView(), top_k=2)
    assert [row.id for row in picked] == ["b", "a"]

    empty = SemanticReranker(ScriptedLLM({"unexpected": []}))
    fallback = await empty.select("query", rows, top_k=2)
    assert [row.id for row in fallback] == ["a", "b"]

    none_relevant = SemanticReranker(ScriptedLLM({"selected_ids": [], "time_scope": None}))
    padded = await none_relevant.select("query", rows, top_k=2)
    assert [row.id for row in padded] == ["a", "b"]


def test_assemble_narrative_skips_superseded():
    rows = [
        StoredBelief(id="1", tenant_id="t", user_id="u", attribute="tea", value="oolong", status="active"),
        StoredBelief(id="2", tenant_id="t", user_id="u", attribute="tea", value="green", status="superseded"),
    ]
    text = assemble_narrative(rows)
    assert "oolong" in text
    assert "green" not in text


def test_mean_pool_rejects_ragged_vectors():
    assert mean_pool([]) is None
    assert mean_pool([[1.0, 2.0], [3.0]]) is None
    assert mean_pool([[1.0, 3.0], [3.0, 5.0]]) == [2.0, 4.0]


def test_episode_and_rerank_prompts_assign_relative_time_to_query_stage():
    assert "Do NOT describe the episode with query-relative expressions" in EPISODE_SYSTEM_PROMPT
    assert "absolute occurred_at/occurred_end" in EPISODE_SYSTEM_PROMPT
    assert "Resolve query-relative phrases" in RERANK_SYSTEM_PROMPT


def test_episode_observation_time_is_always_absolute_iso8601():
    assert _absolute_observation_time("2026-09-01T08:30:00+08:00") == "2026-09-01T08:30:00+08:00"
    assert _absolute_observation_time("2026-09-01T08:30:00").endswith("+00:00")
    with pytest.raises(ValidationError, match="absolute ISO-8601"):
        _absolute_observation_time("上周")


def test_episode_time_bounds_come_from_l1_evidence():
    first = _fact("m1", "first")
    first.metadata = {"occurred_at": "2026-08-01T10:00:00+08:00"}
    second = _fact("m2", "second")
    second.metadata = {"occurred_at": "2026-08-03T18:00:00+08:00"}
    assert _episode_time_bounds([second, first], None) == (
        "2026-08-01T10:00:00+08:00",
        "2026-08-03T18:00:00+08:00",
    )


async def test_recalled_episode_expands_scoped_l1_evidence():
    store = InMemoryVectorStore(embedding_dims=8)
    fact = _fact("m1", "User drove to the park")
    episode = StoredMemory(
        id="ep1",
        content="A trip to the park",
        tenant_id="t",
        user_id="u",
        memory_type="episodic_memory",
        embedding=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        metadata={"source_memory_ids": ["m1"]},
    )
    await store.insert([fact, episode])
    memory = object.__new__(DesayMemory)
    memory.store = store
    expanded = await memory._expand_episode_evidence(
        [episode], MemoryScope(tenant_id="t", user_id="u"), limit=4
    )
    assert [row.id for row in expanded] == ["ep1", "m1"]
    assert expanded[1].metadata["expanded_from_episode_id"] == "ep1"
