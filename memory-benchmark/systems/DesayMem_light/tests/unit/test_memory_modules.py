from datetime import datetime, timezone
from uuid import uuid4

import pytest

from desaymem_light.contracts.providers import ChatResult, EmbeddingResult
from desaymem_light.domain.enums import MemoryType, MessageRole
from desaymem_light.domain.models import (
    EventBuildRequest,
    FactExtractionRequest,
    MemoryRecord,
    MemoryScope,
    Message,
    ModelUsage,
    SegmentRequest,
    SessionScope,
    Topic,
)
from desaymem_light.modules.event.deterministic_builder import DeterministicEventBuilder
from desaymem_light.modules.fact.mem0_extractor import Mem0AdditiveExtractor
from desaymem_light.modules.session.lightmem_segmenter import LightMemTopicSegmenter


class FakeEmbedding:
    dimensions = 2

    async def embed(self, texts, purpose):
        vectors = [[1.0, 0.0], [0.0, 1.0]][: len(texts)]
        return EmbeddingResult(vectors=vectors, model="fake", latency_ms=0)


class FakeTokenizer:
    def count(self, text):
        return len(text.split())


class FakeLlm:
    async def generate_json(self, request):
        return ChatResult(
            data={"memory": [{
                "text": "用户请求播放周杰伦的歌曲",
                "fact_type": "action",
                "attributed_to": "user",
                "source_message_numbers": [0],
                "semantic_tags": ["音乐", "周杰伦", "音乐"],
            }]},
            usage=ModelUsage(model="fake", input_tokens=10, output_tokens=4, latency_ms=1),
        )


def _scope() -> SessionScope:
    return SessionScope(
        tenant_id="t1", user_id="u1", vehicle_id="v1", occupant_id="driver", session_id="s1"
    )


def _message(sequence_no: int, content: str, role=MessageRole.USER) -> Message:
    now = datetime.now(timezone.utc)
    return Message(
        id=uuid4(), scope=_scope(), request_id=f"r{sequence_no}", sequence_no=sequence_no,
        role=role, content=content, content_tokens=1,
        occurred_at=now, ingested_at=now,
    )


@pytest.mark.asyncio
async def test_lightmem_segmenter_keeps_last_topic_pending() -> None:
    messages = [_message(1, "music"), _message(2, "navigation")]
    segmenter = LightMemTopicSegmenter(
        embedding=FakeEmbedding(), tokenizer=FakeTokenizer(),
        options={"similarity_threshold": 0.5},
    )
    result = await segmenter.segment(SegmentRequest(scope=_scope(), messages=messages))

    assert len(result.topics) == 1
    assert result.topics[0].message_ids == [messages[0].id]
    assert result.pending_message_ids == [messages[1].id]


@pytest.mark.asyncio
async def test_segmenter_keeps_user_and_assistant_in_one_turn_and_reuses_cache() -> None:
    class TurnEmbedding:
        dimensions = 2
        model = "test-model"
        def __init__(self): self.calls = 0
        async def embed(self, texts, purpose):
            self.calls += 1
            vectors = [
                [0.0, 1.0] if "navigation" in text else [1.0, 0.0]
                for text in texts
            ]
            return EmbeddingResult(vectors=vectors, model=self.model, latency_ms=0)

    messages = [
        _message(1, "play music", MessageRole.USER),
        _message(2, "playing music", MessageRole.ASSISTANT),
        _message(3, "start navigation", MessageRole.USER),
    ]
    embedding = TurnEmbedding()
    segmenter = LightMemTopicSegmenter(
        embedding=embedding, tokenizer=FakeTokenizer(),
        options={"similarity_threshold": 0.5},
    )

    first = await segmenter.segment(SegmentRequest(scope=_scope(), messages=messages))
    second = await segmenter.segment(SegmentRequest(scope=_scope(), messages=messages))

    assert first.topics[0].message_ids == [messages[0].id, messages[1].id]
    assert first.pending_message_ids == [messages[2].id]
    assert [topic.message_ids for topic in second.topics] == [
        topic.message_ids for topic in first.topics
    ]
    assert second.pending_message_ids == first.pending_message_ids
    assert embedding.calls == 1


@pytest.mark.asyncio
async def test_topic_message_limit_never_splits_a_turn() -> None:
    class SameEmbedding:
        dimensions = 2
        async def embed(self, texts, purpose):
            return EmbeddingResult(
                vectors=[[1.0, 0.0] for _ in texts], model="same", latency_ms=0
            )

    messages = [
        _message(1, "request", MessageRole.USER),
        _message(2, "answer", MessageRole.ASSISTANT),
        _message(3, "next request", MessageRole.USER),
    ]
    segmenter = LightMemTopicSegmenter(
        embedding=SameEmbedding(), tokenizer=FakeTokenizer(),
        options={"topic_max_messages": 2},
    )

    result = await segmenter.segment(SegmentRequest(scope=_scope(), messages=messages))

    assert result.topics[0].boundary_reason == "message_limit"
    assert result.topics[0].message_ids == [messages[0].id, messages[1].id]
    assert result.pending_message_ids == [messages[2].id]


@pytest.mark.asyncio
async def test_idle_force_flush_uses_explicit_boundary_reason() -> None:
    value = _message(1, "play music", MessageRole.USER)
    segmenter = LightMemTopicSegmenter(
        embedding=FakeEmbedding(), tokenizer=FakeTokenizer()
    )

    result = await segmenter.segment(
        SegmentRequest(
            scope=_scope(),
            messages=[value],
            force_flush=True,
            flush_reason="idle_timeout",
        )
    )

    assert result.topics[0].boundary_reason == "idle_timeout"


@pytest.mark.asyncio
async def test_mem0_extractor_uses_one_llm_call_result() -> None:
    source = _message(1, "播放周杰伦")
    topic = Topic(
        id=uuid4(), scope=_scope(), message_ids=[source.id], content="user: 播放周杰伦",
        token_count=5, boundary_reason="manual",
    )
    result = await Mem0AdditiveExtractor(llm=FakeLlm(), prompt="extract").extract(
        FactExtractionRequest(
            request_id=uuid4(), topic=topic, topic_messages=[source], prompt_version="v1"
        )
    )
    assert [fact.content for fact in result.facts] == ["用户请求播放周杰伦的歌曲"]
    assert result.facts[0].fact_type == "action"
    assert result.facts[0].source_message_ids == [source.id]
    assert result.facts[0].semantic_tags == ["音乐", "周杰伦"]
    assert result.facts[0].occurred_at == source.occurred_at
    assert result.usage.input_tokens == 10


@pytest.mark.asyncio
async def test_mem0_extractor_resolves_confirm_candidate_index() -> None:
    source = _message(1, "我一直喜欢周杰伦")
    existing = MemoryRecord(
        id=uuid4(),
        scope=MemoryScope(
            tenant_id="t1", user_id="u1", vehicle_id="v1", occupant_id="driver"
        ),
        memory_type=MemoryType.FACT,
        content="用户喜欢周杰伦",
        content_hash="existing",
        embedding=[1.0, 0.0],
        observed_at=source.observed_at if hasattr(source, "observed_at") else source.ingested_at,
    )

    class ConfirmLlm:
        async def generate_json(self, request):
            return ChatResult(
                data={"memory": [{
                    "text": "用户喜欢周杰伦",
                    "fact_type": "explicit_preference",
                    "operation": "CONFIRM",
                    "target_fact_index": 0,
                    "source_message_numbers": [0],
                }]},
                usage=ModelUsage(
                    model="fake", input_tokens=10, output_tokens=4, latency_ms=1
                ),
            )

    topic = Topic(
        id=uuid4(), scope=_scope(), message_ids=[source.id], content=source.content,
        token_count=5, boundary_reason="manual",
    )
    result = await Mem0AdditiveExtractor(llm=ConfirmLlm(), prompt="extract").extract(
        FactExtractionRequest(
            request_id=uuid4(), topic=topic, topic_messages=[source],
            existing_facts=[existing], prompt_version="v4",
        )
    )

    assert result.facts[0].operation == "CONFIRM"
    assert result.facts[0].target_fact_id == existing.id


@pytest.mark.asyncio
async def test_mem0_extractor_never_reuses_historical_action() -> None:
    source = _message(1, "播放晴天")

    class IncorrectActionLlm:
        async def generate_json(self, request):
            return ChatResult(
                data={"memory": [{
                    "text": "用户请求播放《晴天》",
                    "fact_type": "action",
                    "operation": "CONFIRM",
                    "target_fact_index": 0,
                    "source_message_numbers": [0],
                }]},
                usage=ModelUsage(
                    model="fake", input_tokens=10, output_tokens=4, latency_ms=1
                ),
            )

    existing = MemoryRecord(
        id=uuid4(),
        scope=MemoryScope(
            tenant_id="t1", user_id="u1", vehicle_id="v1", occupant_id="driver"
        ),
        memory_type=MemoryType.FACT,
        content="用户请求播放《晴天》",
        content_hash="old-action",
        embedding=[1.0, 0.0],
        observed_at=source.ingested_at,
    )
    topic = Topic(
        id=uuid4(), scope=_scope(), message_ids=[source.id], content=source.content,
        token_count=5, boundary_reason="manual",
    )
    result = await Mem0AdditiveExtractor(
        llm=IncorrectActionLlm(), prompt="extract"
    ).extract(
        FactExtractionRequest(
            request_id=uuid4(), topic=topic, topic_messages=[source],
            existing_facts=[existing], prompt_version="v4",
        )
    )

    assert result.facts[0].operation == "ADD"
    assert result.facts[0].target_fact_id is None


def test_event_builder_is_deterministic_and_keeps_sources() -> None:
    now = datetime.now(timezone.utc)
    source = _message(1, "播放周杰伦")
    fact = MemoryRecord(
        id=uuid4(), scope=MemoryScope(
            tenant_id="t1", user_id="u1", vehicle_id="v1", occupant_id="driver"
        ),
        memory_type=MemoryType.FACT, content="用户喜欢周杰伦", content_hash="hash",
        embedding=[0.0], observed_at=now, occurred_at=now,
        metadata={
            "fact_type": "action",
            "source_message_ids": [str(source.id)],
            "semantic_tags": ["音乐"],
        },
    )
    result = DeterministicEventBuilder().build(
        EventBuildRequest(
            topic=Topic(
                id=uuid4(), scope=_scope(), message_ids=[source.id], content="x",
                token_count=1, boundary_reason="manual",
            ),
            messages=[source], facts=[fact], tags=[" 周杰伦 ", "周杰伦", "音乐"],
        )
    )
    assert result.should_create
    assert result.source_fact_ids == [fact.id]
    assert result.tags == ["周杰伦", "音乐"]
    assert "用户喜欢周杰伦" in result.content
    assert result.metadata["source_message_ids"] == [str(source.id)]
    assert result.metadata["fact_types"]["action"] == [str(fact.id)]


def test_event_builder_preserves_topic_fact_link_order() -> None:
    now = datetime.now(timezone.utc)
    message = _message(1, "再次确认偏好")
    scope = MemoryScope(
        tenant_id="t1", user_id="u1", vehicle_id="v1", occupant_id="driver"
    )
    first = MemoryRecord(
        id=uuid4(), scope=scope, memory_type=MemoryType.FACT,
        content="当前首先确认的历史事实", content_hash="first", embedding=[0.0],
        occurred_at=now, observed_at=now,
    )
    second = MemoryRecord(
        id=uuid4(), scope=scope, memory_type=MemoryType.FACT,
        content="当前随后确认但原始时间更早的事实", content_hash="second",
        embedding=[0.0], occurred_at=now.replace(year=now.year - 1), observed_at=now,
    )

    result = DeterministicEventBuilder().build(
        EventBuildRequest(
            topic=Topic(
                id=uuid4(), scope=_scope(), message_ids=[message.id], content="x",
                token_count=1, boundary_reason="manual",
            ),
            messages=[message], facts=[first, second],
        )
    )

    assert result.source_fact_ids == [first.id, second.id]
    assert result.content.index(first.content) < result.content.index(second.content)
