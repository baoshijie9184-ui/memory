from datetime import datetime, timezone
from uuid import uuid4

import pytest

from desaymem_light.domain.enums import MemoryType, MessageRole
from desaymem_light.domain.models import (
    MemoryRecord,
    MemoryScope,
    Message,
    ProfileSnapshotView,
    SearchRequest,
    SessionScope,
)
from desaymem_light.modules.retrieval.hybrid_retriever import FilteredVectorRetriever


class Embedding:
    def __init__(self): self.calls = 0
    async def embed(self, texts, purpose):
        self.calls += 1
        return type("Result", (), {"vectors": [[0.1, 0.2] for _ in texts]})()


class Tokens:
    def count(self, text): return len(text)


class Memories:
    def __init__(self, memory, cross_event=None, summarized=None, fact=None, contained=None):
        self.memory, self.cross_event, self.summarized, self.fact, self.contained, self.calls = (
            memory, cross_event, summarized or [], fact, contained or [], [],
        )
    async def search(self, scope, vector, memory_type, limit, **filters):
        self.calls.append((memory_type, filters))
        if memory_type == "cross_event" and self.cross_event:
            return [(self.cross_event, 0.9)]
        if memory_type == "fact" and self.fact:
            return [(self.fact, 0.85)]
        return [(self.memory, 0.8)] if memory_type == "event" else []
    async def relation_targets(self, source_id, relation_type):
        if source_id == getattr(self.cross_event, "id", None) and relation_type == "summarizes":
            return self.summarized
        if source_id == getattr(self.memory, "id", None) and relation_type == "contains":
            return self.contained
        return []


class Profiles:
    async def current_snapshot(self, scope):
        return ProfileSnapshotView(id=uuid4(), version=2, summary="用户喜欢周杰伦。")


class ShortTerm:
    def __init__(self): self.calls = 0
    async def search(self, scope, vector, limit):
        self.calls += 1
        return []
    async def existing_ids(self, message_ids): return set()
    async def add(self, message, embedding): self.added = self.__dict__.get("added", 0) + 1
    async def trim(self, scope, keep): self.trimmed = keep


class Messages:
    def __init__(self, values=None, materialized=None):
        self.values = values or []
        self.materialized = materialized or []
    async def unmaterialized(self, scope, limit): return self.values[-limit:]
    async def recent_materialized(self, scope, limit): return self.materialized[-limit:]


class Uow:
    def __init__(self, memories, messages=None):
        self.memories, self.profiles = memories, Profiles()
        self.short_term_memories = ShortTerm()
        self.messages = messages or Messages()
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    async def commit(self): return None


@pytest.mark.asyncio
async def test_retrieval_uses_one_embedding_and_no_llm():
    scope = MemoryScope(tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver")
    now = datetime.now(timezone.utc)
    memory = MemoryRecord(
        id=uuid4(), scope=scope, memory_type=MemoryType.EVENT, content="上班时播放周杰伦",
        content_hash="hash", embedding=[0.1, 0.2], occurred_at=now, observed_at=now,
    )
    repository, embedding = Memories(memory), Embedding()
    retriever = FilteredVectorRetriever(
        unit_of_work=lambda: Uow(repository), embedding=embedding, tokenizer=Tokens(),
        options={"context_token_limit": 200},
    )
    request = SearchRequest(
        request_id=uuid4(), query="通勤听什么", scope=scope, tags=["音乐"],
        time_from=now, vehicle_only=True,
    )

    result = await retriever.search(request)

    assert embedding.calls == 1
    assert result.usage.embedding_calls == 1 and result.usage.llm_calls == 0
    assert result.events[0].memory.id == memory.id
    assert result.profile.version == 2
    assert "用户画像" in result.agent_context and "上班时播放周杰伦" in result.agent_context
    assert result.agent_context.index("用户画像") < result.agent_context.index("事件")
    assert all(call[1]["tags"] == ["音乐"] for call in repository.calls)
    assert all(call[1]["vehicle_only"] is True for call in repository.calls)


@pytest.mark.asyncio
async def test_retrieval_includes_only_latest_pending_session_messages_within_token_budget():
    scope = MemoryScope(tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver")
    session_scope = SessionScope(**scope.model_dump(), session_id="s")
    now = datetime.now(timezone.utc)
    values = [
        Message(
            id=uuid4(), scope=session_scope, request_id=f"r{index}", sequence_no=index,
            role=MessageRole.USER, content=text, content_tokens=tokens,
            occurred_at=now, ingested_at=now,
        )
        for index, (text, tokens) in enumerate((("较早消息", 4), ("播放周杰伦", 4), ("声音大一点", 3)))
    ]
    memory = MemoryRecord(
        id=uuid4(), scope=scope, memory_type=MemoryType.EVENT, content="历史事件",
        content_hash="hash", embedding=[0.1, 0.2], occurred_at=now, observed_at=now,
    )
    repository = Memories(memory)
    uow = Uow(repository, Messages(values))
    retriever = FilteredVectorRetriever(
        unit_of_work=lambda: uow, embedding=Embedding(), tokenizer=Tokens(),
        options={"context_token_limit": 200, "session_buffer_max_tokens": 7},
    )

    result = await retriever.search(SearchRequest(
        request_id=uuid4(), query="调大什么", scope=scope, session_id="s",
    ))

    assert [hit.message.content for hit in result.short_term] == ["播放周杰伦", "声音大一点"]
    assert "较早消息" not in result.agent_context
    assert "播放周杰伦" in result.agent_context and "声音大一点" in result.agent_context
    # 轻量模式直接读取 Session，不做语义检索，也不持久化消息向量。
    assert uow.short_term_memories.calls == 0
    assert not hasattr(uow.short_term_memories, "added")
    assert not hasattr(uow.short_term_memories, "trimmed")


@pytest.mark.asyncio
async def test_retrieval_merges_materialized_last_k_before_pending_messages():
    scope = MemoryScope(tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver")
    session_scope = SessionScope(**scope.model_dump(), session_id="s")
    now = datetime.now(timezone.utc)

    def message(sequence_no, content):
        return Message(
            id=uuid4(), scope=session_scope, request_id=f"r{sequence_no}",
            sequence_no=sequence_no, role=MessageRole.USER, content=content,
            content_tokens=2, occurred_at=now, ingested_at=now,
        )

    materialized = [message(0, "播放周杰伦"), message(1, "正在播放晴天")]
    pending = [message(2, "换一首")]
    memory = MemoryRecord(
        id=uuid4(), scope=scope, memory_type=MemoryType.EVENT, content="历史事件",
        content_hash="hash", embedding=[0.1, 0.2], occurred_at=now, observed_at=now,
    )
    uow = Uow(Memories(memory), Messages(pending, materialized))
    retriever = FilteredVectorRetriever(
        unit_of_work=lambda: uow, embedding=Embedding(), tokenizer=Tokens(),
        options={"context_token_limit": 200, "materialized_last_k_messages": 2},
    )

    result = await retriever.search(SearchRequest(
        request_id=uuid4(), query="换什么", scope=scope, session_id="s",
    ))

    assert [hit.message.content for hit in result.short_term] == [
        "播放周杰伦", "正在播放晴天", "换一首",
    ]
    assert result.agent_context.index("近期对话") < result.agent_context.index("用户画像")


@pytest.mark.asyncio
async def test_latest_pending_turn_is_kept_whole_even_when_it_exceeds_budget():
    scope = MemoryScope(tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver")
    session_scope = SessionScope(**scope.model_dump(), session_id="s")
    now = datetime.now(timezone.utc)
    pending = [
        Message(id=uuid4(), scope=session_scope, request_id="r0", sequence_no=0,
                role=MessageRole.USER, content="older", content_tokens=3,
                occurred_at=now, ingested_at=now),
        Message(id=uuid4(), scope=session_scope, request_id="r1", sequence_no=1,
                role=MessageRole.USER, content="latest request", content_tokens=4,
                occurred_at=now, ingested_at=now),
        Message(id=uuid4(), scope=session_scope, request_id="r2", sequence_no=2,
                role=MessageRole.ASSISTANT, content="latest answer", content_tokens=4,
                occurred_at=now, ingested_at=now),
    ]
    memory = MemoryRecord(
        id=uuid4(), scope=scope, memory_type=MemoryType.EVENT, content="history",
        content_hash="hash", embedding=[0.1], occurred_at=now, observed_at=now,
    )
    retriever = FilteredVectorRetriever(
        unit_of_work=lambda: Uow(Memories(memory), Messages(pending)),
        embedding=Embedding(), tokenizer=Tokens(),
        options={"context_token_limit": 200, "session_buffer_max_tokens": 5},
    )

    result = await retriever.search(SearchRequest(
        request_id=uuid4(), query="what", scope=scope, session_id="s",
    ))

    assert [hit.message.content for hit in result.short_term] == [
        "latest request", "latest answer",
    ]


@pytest.mark.asyncio
async def test_retrieval_hides_events_already_summarized_by_returned_cross_event():
    scope = MemoryScope(tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver")
    now = datetime.now(timezone.utc)
    event = MemoryRecord(
        id=uuid4(), scope=scope, memory_type=MemoryType.EVENT, content="多次播放周杰伦",
        content_hash="event", embedding=[0.1], occurred_at=now, observed_at=now,
    )
    cross_event = MemoryRecord(
        id=uuid4(), scope=scope, memory_type=MemoryType.CROSS_EVENT,
        content="通勤时常听周杰伦", content_hash="cross", embedding=[0.1],
        occurred_at=now, observed_at=now,
    )
    repository = Memories(event, cross_event=cross_event, summarized=[event])
    retriever = FilteredVectorRetriever(
        unit_of_work=lambda: Uow(repository), embedding=Embedding(), tokenizer=Tokens(),
        options={"context_token_limit": 200},
    )

    result = await retriever.search(SearchRequest(
        request_id=uuid4(), query="通勤听什么", scope=scope,
    ))

    assert result.events == []
    assert result.cross_events[0].memory.id == cross_event.id
    assert result.agent_context.count("通勤时常听周杰伦") == 1


@pytest.mark.asyncio
async def test_retrieval_hides_facts_already_contained_by_returned_event():
    scope = MemoryScope(tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver")
    now = datetime.now(timezone.utc)
    fact = MemoryRecord(
        id=uuid4(), scope=scope, memory_type=MemoryType.FACT, content="播放了晴天",
        content_hash="fact", embedding=[0.1], occurred_at=now, observed_at=now,
    )
    event = MemoryRecord(
        id=uuid4(), scope=scope, memory_type=MemoryType.EVENT,
        content="用户播放了周杰伦的晴天", content_hash="event", embedding=[0.1],
        occurred_at=now, observed_at=now,
    )
    repository = Memories(event, fact=fact, contained=[fact])
    retriever = FilteredVectorRetriever(
        unit_of_work=lambda: Uow(repository), embedding=Embedding(), tokenizer=Tokens(),
        options={"context_token_limit": 200},
    )

    result = await retriever.search(SearchRequest(
        request_id=uuid4(), query="刚才播放了什么", scope=scope,
    ))

    assert result.events[0].memory.id == event.id
    assert result.facts == []
    assert fact.content not in result.agent_context
    assert event.content in result.agent_context
