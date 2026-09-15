from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from desaymem_light.application.insight_pipeline import InsightPipeline
from desaymem_light.domain.enums import MemoryType
from desaymem_light.domain.models import (
    CrossEventResult,
    MemoryRecord,
    MemoryScope,
    ModelUsage,
)


class Counter:
    def count(self, text):
        return len(text)


class Embedder:
    dimensions = 2

    def __init__(self):
        self.calls = 0

    async def embed(self, texts, purpose):
        self.calls += 1
        return type("Embedding", (), {"vectors": [[0.5, 0.5]], "model": "BGE-M3"})()


class Synthesizer:
    def __init__(self):
        self.calls = 0

    async def synthesize(self, request):
        self.calls += 1
        return CrossEventResult(
            should_create=True,
            summary="用户工作日通勤时常听周杰伦。",
            supporting_event_numbers=[],
            usage=ModelUsage(
                model="Qwen3-32B", input_tokens=30, output_tokens=10, latency_ms=2
            ),
        )


class Store:
    def __init__(self, events):
        self.events = events
        self.memories = []
        self.relations = []
        self.evidence = []
        self.tags = {}
        self.saved_tags = []
        self.checkpoint = (None, None)
        self.jobs = []
        self.job_payloads = []
        self.scheduled_jobs = []
        self.usage = []
        self.audit = []


class Memories:
    def __init__(self, store): self.store = store
    async def events_after(self, scope, after_time, after_id, limit): return self.store.events[:limit]
    async def related_events(self, scope, embedding, time_from, time_to, exclude_ids, limit): return []
    async def add_many(self, values): self.store.memories.extend(values)
    async def find_by_derivation_key(self, scope, key):
        return next((m for m in self.store.memories if m.derivation_key == key), None)
    async def add_relation(self, source_id, target_id, relation_type, ordinal=None):
        self.store.relations.append((source_id, target_id, relation_type, ordinal))
    async def add_evidence(self, memory_id, source_type, source_id):
        self.store.evidence.append((memory_id, source_type, source_id))
    async def tags_for_memories(self, memory_ids):
        return {memory_id: self.store.tags.get(memory_id, []) for memory_id in memory_ids}
    async def add_tags(self, memory_id, tenant_id, tags, source_id):
        self.store.saved_tags.append((memory_id, tags, source_id))


class Checkpoints:
    def __init__(self, store): self.store = store
    async def get_cross_event(self, scope): return self.store.checkpoint
    async def set_cross_event(self, scope, occurred_at, event_id):
        self.store.checkpoint = (occurred_at, event_id)


class Jobs:
    def __init__(self, store): self.store = store
    async def enqueue(self, job_type, payload, idempotency_key, *, next_run_at=None):
        self.store.job_payloads.append((job_type, payload))
        if next_run_at is None:
            self.store.jobs.append((job_type, idempotency_key))
        else:
            self.store.scheduled_jobs.append((job_type, idempotency_key, next_run_at))
        return uuid4()


class Recorder:
    def __init__(self, target): self.target = target
    async def record(self, value): self.target.append(value)
    async def append(self, value): self.target.append(value)


class Uow:
    def __init__(self, store):
        self.memories = Memories(store)
        self.checkpoints = Checkpoints(store)
        self.jobs = Jobs(store)
        self.usage = Recorder(store.usage)
        self.audit = Recorder(store.audit)
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    async def commit(self): return None


def event(scope, content, minute):
    now = datetime(2026, 1, 1, 8, minute, tzinfo=timezone.utc)
    return MemoryRecord(
        id=uuid4(), scope=scope, memory_type=MemoryType.EVENT, content=content,
        content_hash=str(uuid4()), embedding=[0.2, 0.8], occurred_at=now,
        observed_at=now,
    )


@pytest.mark.asyncio
async def test_cross_event_waits_for_batch_without_llm_call():
    scope = MemoryScope(tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver")
    store = Store([event(scope, "通勤播放周杰伦", 0)])
    synth, embed = Synthesizer(), Embedder()
    pipeline = InsightPipeline(lambda: Uow(store), synth, embed, Counter(), trigger_count=2)

    result = await pipeline.process_cross_event(scope, uuid4())

    assert result.state == "pending"
    assert synth.calls == embed.calls == 0
    assert store.checkpoint == (None, None)


@pytest.mark.asyncio
async def test_cross_event_persists_trace_and_advances_checkpoint_once():
    scope = MemoryScope(tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver")
    events = [event(scope, "上班通勤播放周杰伦", 0), event(scope, "下班通勤播放周杰伦", 30)]
    store = Store(events)
    store.tags = {
        events[0].id: ["音乐", "通勤", "周杰伦"],
        events[1].id: ["音乐", "通勤"],
    }
    synth, embed = Synthesizer(), Embedder()
    pipeline = InsightPipeline(lambda: Uow(store), synth, embed, Counter(), trigger_count=2)

    result = await pipeline.process_cross_event(scope, uuid4())

    assert result.state == "created"
    assert synth.calls == embed.calls == 1
    assert store.memories[0].memory_type == MemoryType.CROSS_EVENT
    assert [relation[1] for relation in store.relations if relation[2] == "summarizes"] == [
        item.id for item in events
    ]
    assert [relation for relation in store.relations if relation[2] == "related_to"] == []
    assert store.checkpoint == (events[-1].occurred_at, events[-1].id)
    assert store.jobs == [("profile_update", f"profile:{store.memories[0].id}:v1")]
    assert store.job_payloads[0][1]["cross_event_id"] == str(store.memories[0].id)
    assert len(store.usage) == len(store.audit) == 1
    assert store.memories[0].metadata["trigger_type"] == "count"
    assert store.memories[0].metadata["support_event_ids"] == []
    assert store.memories[0].metadata["covered_event_ids"] == [str(item.id) for item in events]
    assert store.memories[0].metadata["seed_event_ids"] == []
    assert [item[1:] for item in store.evidence] == [
        ("event", events[0].id), ("event", events[1].id),
    ]
    assert set(store.saved_tags[0][1][:2]) == {"音乐", "通勤"}
    assert store.saved_tags[0][1][2] == "周杰伦"


@pytest.mark.asyncio
async def test_cross_event_time_fallback_runs_with_two_old_events():
    scope = MemoryScope(tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver")
    old = datetime.now(timezone.utc) - timedelta(minutes=11)
    events = [event(scope, "通勤播放周杰伦", 0), event(scope, "通勤播放陶喆", 1)]
    events = [value.model_copy(update={"observed_at": old}) for value in events]
    store = Store(events)
    synth, embed = Synthesizer(), Embedder()
    pipeline = InsightPipeline(
        lambda: Uow(store), synth, embed, Counter(), trigger_count=10,
        time_trigger_minutes=10, time_trigger_min_events=2,
    )

    result = await pipeline.process_cross_event(scope, uuid4())

    assert result.state == "created"
    assert result.consumed_events == 2
    assert synth.calls == 1
    assert store.memories[0].metadata["trigger_type"] == "time"


@pytest.mark.asyncio
async def test_cross_event_schedules_one_delayed_check_for_recent_events():
    scope = MemoryScope(tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver")
    now = datetime.now(timezone.utc)
    events = [event(scope, "通勤播放周杰伦", 0), event(scope, "通勤播放陶喆", 1)]
    events = [value.model_copy(update={"observed_at": now}) for value in events]
    store = Store(events)
    pipeline = InsightPipeline(
        lambda: Uow(store), Synthesizer(), Embedder(), Counter(), trigger_count=10,
        time_trigger_minutes=10, time_trigger_min_events=2,
    )

    result = await pipeline.process_cross_event(scope, uuid4())

    assert result.state == "pending"
    assert len(store.scheduled_jobs) == 1
    assert store.scheduled_jobs[0][0] == "cross_event"
    assert store.scheduled_jobs[0][2] >= now + timedelta(minutes=10)
    assert store.checkpoint == (None, None)


@pytest.mark.asyncio
async def test_cross_event_checkpoint_only_advances_over_packed_seed_prefix():
    scope = MemoryScope(tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver")
    events = [
        event(scope, "第一个很长的事件", 0),
        event(scope, "第二个很长的事件", 1),
        event(scope, "第三个留给下批次", 2),
    ]
    store = Store(events)
    pipeline = InsightPipeline(
        lambda: Uow(store), Synthesizer(), Embedder(), Counter(),
        trigger_count=3, max_input_tokens=1,
    )

    result = await pipeline.process_cross_event(scope, uuid4())

    assert result.state == "created"
    assert result.consumed_events == 2
    assert store.checkpoint == (events[1].occurred_at, events[1].id)
    assert store.memories[0].metadata["covered_event_ids"] == [
        str(events[0].id), str(events[1].id),
    ]
    assert str(events[2].id) not in store.memories[0].metadata["covered_event_ids"]
