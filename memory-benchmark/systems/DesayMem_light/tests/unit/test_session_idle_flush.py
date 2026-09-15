from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from desaymem_light.application.ingest_service import IngestService
from desaymem_light.application.session_pipeline import SessionPipeline
from desaymem_light.domain.enums import MessageRole
from desaymem_light.domain.models import Message, SessionScope


def message(sequence=1):
    now = datetime.now(timezone.utc)
    scope = SessionScope(
        tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver", session_id="s"
    )
    return Message(
        id=uuid4(),
        scope=scope,
        request_id=f"r{sequence}",
        sequence_no=sequence,
        role=MessageRole.USER,
        content="play music",
        content_tokens=2,
        occurred_at=now,
        ingested_at=now,
    )


class Store:
    def __init__(self, messages):
        self.messages = messages
        self.jobs = []


class Messages:
    def __init__(self, store):
        self.store = store

    async def add(self, value):
        return value

    async def pending(self, scope, limit):
        return self.store.messages[:limit]


class Jobs:
    def __init__(self, store):
        self.store = store

    async def enqueue(self, kind, payload, idempotency_key, *, next_run_at=None):
        self.store.jobs.append((kind, payload, idempotency_key, next_run_at))
        return uuid4()


class ShortTerm:
    async def add(self, message, vector): raise AssertionError("disabled")
    async def trim(self, scope, keep): raise AssertionError("disabled")


class Topics:
    def __init__(self):
        self.values = []

    async def add(self, topic):
        self.values.append(topic)


class Uow:
    def __init__(self, store):
        self.messages = Messages(store)
        self.jobs = Jobs(store)
        self.short_term_memories = ShortTerm()
        self.topics = Topics()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def commit(self):
        return None


class Embedding:
    async def embed(self, texts, purpose):
        raise AssertionError("disabled")


class Segmenter:
    def __init__(self):
        self.calls = 0

    async def segment(self, request):
        self.calls += 1
        self.last_request = request
        return type(
            "Result",
            (),
            {"topics": [], "pending_message_ids": [item.id for item in request.messages]},
        )()


@pytest.mark.asyncio
async def test_ingest_schedules_immediate_and_validated_idle_flush():
    value = message()
    store = Store([value])
    service = IngestService(lambda: Uow(store), Embedding(), idle_flush_minutes=10)

    await service.ingest(value)

    assert len(store.jobs) == 2
    delayed = store.jobs[1]
    assert delayed[1]["force_flush"] is True
    assert delayed[1]["flush_reason"] == "idle_timeout"
    assert delayed[1]["expected_last_message_id"] == str(value.id)
    assert delayed[3] == value.ingested_at + timedelta(minutes=10)


@pytest.mark.asyncio
async def test_stale_idle_flush_does_not_segment_newer_pending_message():
    old, new = message(1), message(2)
    store = Store([old, new])
    segmenter = Segmenter()
    pipeline = SessionPipeline(lambda: Uow(store), segmenter)

    result = await pipeline.process(
        old.scope,
        force_flush=True,
        expected_last_message_id=old.id,
        expected_last_ingested_at=old.ingested_at,
    )

    assert result.topic_ids == ()
    assert result.pending_message_ids == (old.id, new.id)
    assert segmenter.calls == 0


@pytest.mark.asyncio
async def test_current_idle_flush_reaches_segmenter_as_force_flush():
    value = message()
    store = Store([value])
    segmenter = Segmenter()
    pipeline = SessionPipeline(lambda: Uow(store), segmenter)

    result = await pipeline.process(
        value.scope,
        force_flush=True,
        flush_reason="idle_timeout",
        expected_last_message_id=value.id,
        expected_last_ingested_at=value.ingested_at,
    )

    assert result.pending_message_ids == (value.id,)
    assert segmenter.calls == 1
    assert segmenter.last_request.force_flush is True
    assert segmenter.last_request.flush_reason == "idle_timeout"
