"""Steady-state profile behavior and cross-event history recall exclusions."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from desaymem_light.application.profile_pipeline import ProfilePipeline
from desaymem_light.domain.enums import MemoryStatus, MemoryType
from desaymem_light.domain.models import (
    MemoryRecord,
    MemoryScope,
    ModelUsage,
    NumberedEvent,
    ProfileItemRecord,
    ProfileItemView,
    ProfileOperation,
    ProfileSnapshotView,
    ProfileUpdateRequest,
    ProfileUpdateResult,
)


def scope() -> MemoryScope:
    return MemoryScope(tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver")


def event_record(content: str, minute: int, memory_type: MemoryType = MemoryType.EVENT) -> MemoryRecord:
    now = datetime(2026, 1, 1, 8, minute, tzinfo=timezone.utc)
    return MemoryRecord(
        id=uuid4(), scope=scope(), memory_type=memory_type, content=content,
        content_hash=str(uuid4()), embedding=[0.2, 0.8], occurred_at=now, observed_at=now,
    )


def profile_item(attribute: str, value: str) -> ProfileItemRecord:
    now = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    return ProfileItemRecord(
        id=uuid4(), scope=scope(), attribute=attribute, value=value,
        normalized_hash=str(uuid4()), embedding=[0.5, 0.5], status=MemoryStatus.ACTIVE,
        first_observed_at=now, last_confirmed_at=now, valid_from=now,
        confirmation_count=1, model="m", prompt_version="v1",
    )


class Updater:
    def __init__(self, result):
        self.result = result

    async def update(self, request):
        return self.result


class Embedder:
    async def embed(self, texts, purpose):
        return type("Embedding", (), {"vectors": [[0.5, 0.5]], "model": "BGE-M3"})()


class Store:
    def __init__(self, *, snapshot=None, items=None):
        self.snapshot = snapshot
        self.items = items or []
        self.confirmed = []
        self.evidence = []
        self.snapshots = []
        self.checkpoint = None


class Profiles:
    def __init__(self, store): self.store = store
    async def current_snapshot(self, scope): return self.store.snapshot
    async def current_items(self, scope): return self.store.items
    async def find_active_by_hash(self, scope, hash):
        return self.store.items[-1] if self.store.items else None
    async def add_item(self, item): self.store.items.append(item)
    async def confirm_item(self, item_id, confirmed_at): self.store.confirmed.append(item_id)
    async def supersede_item(self, item_id, valid_to): pass
    async def add_evidence(self, item_id, event_id, role, via_cross_event_id=None):
        self.store.evidence.append((item_id, event_id, role))
    async def add_relation(self, source_id, target_id, relation_type, reason=None): pass
    async def create_snapshot(self, scope, summary, item_ids, model, prompt_version):
        view = ProfileSnapshotView(id=uuid4(), version=len(self.store.snapshots) + 2, summary=summary)
        self.store.snapshots.append(view)
        return view


class Memories:
    def __init__(self, events, cross_event): self.events = events; self.cross_event = cross_event
    async def get(self, memory_id, scope): return self.cross_event
    async def relation_targets(self, source_id, relation_type):
        return self.events if relation_type == "related_to" else []


class Checkpoints:
    def __init__(self, store): self.store = store
    async def get_cross_event(self, scope): return (None, None)
    async def set_profile(self, scope, occurred_at, event_id, snapshot_version):
        self.store.checkpoint = (occurred_at, event_id, snapshot_version)


class Uow:
    def __init__(self, store, memories):
        self.profiles = Profiles(store)
        self.memories = memories
        self.checkpoints = Checkpoints(store)

        class Recorder:
            def __init__(self): self.values = []
            async def record(self, value): self.values.append(value)
            async def append(self, value): self.values.append(value)

        self.usage = Recorder()
        self.audit = Recorder()
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    async def commit(self): return None


def build_pipeline(store, memories, result):
    return ProfilePipeline(
        lambda: Uow(store, memories), Updater(result), Embedder(), prompt_version="v1",
    )


@pytest.mark.asyncio
async def test_steady_confirm_skips_redundant_snapshot():
    """Pure CONFIRM with unchanged summary must not append a duplicate snapshot."""
    item = profile_item("music preference", "周杰伦的歌曲")
    cross_event = event_record("用户多次在通勤时播放周杰伦", 0, MemoryType.CROSS_EVENT)
    events = [event_record("通勤播放周杰伦", 10), event_record("通勤播放周杰伦", 20)]
    summary = "用户偏好在通勤时听周杰伦的歌曲。"
    store = Store(
        snapshot=ProfileSnapshotView(id=uuid4(), version=1, summary=summary),
        items=[item],
    )
    result = ProfileUpdateResult(
        operations=[
            ProfileOperation(
                action="CONFIRM", profile_item_number=0, evidence_event_numbers=[0, 1],
            )
        ],
        summary=summary,
        usage=ModelUsage(model="Qwen3-32B", input_tokens=10, output_tokens=5, latency_ms=1),
    )
    memories = Memories(events, cross_event)
    pipeline = build_pipeline(store, memories, result)

    run = await pipeline.process(scope(), uuid4(), cross_event.id)

    assert run.state == "confirmed"
    assert run.snapshot_version == 1
    assert store.snapshots == []            # no new snapshot
    assert store.confirmed == [item.id]     # confirmation still recorded
    assert len(store.evidence) == 2         # evidence still linked
    assert store.checkpoint[2] == 1         # checkpoint keeps version 1


@pytest.mark.asyncio
async def test_summary_change_creates_new_snapshot():
    """When the narrative changes, a new snapshot version is still created."""
    item = profile_item("music preference", "周杰伦的歌曲")
    cross_event = event_record("用户多次在通勤时播放周杰伦", 0, MemoryType.CROSS_EVENT)
    events = [event_record("通勤播放周杰伦", 10)]
    store = Store(
        snapshot=ProfileSnapshotView(id=uuid4(), version=1, summary="旧总结。"),
        items=[item],
    )
    result = ProfileUpdateResult(
        operations=[
            ProfileOperation(
                action="CONFIRM", profile_item_number=0, evidence_event_numbers=[0],
            )
        ],
        summary="用户偏好在通勤时听周杰伦的歌曲。",
        usage=ModelUsage(model="Qwen3-32B", input_tokens=10, output_tokens=5, latency_ms=1),
    )
    pipeline = build_pipeline(store, Memories(events, cross_event), result)

    run = await pipeline.process(scope(), uuid4(), cross_event.id)

    assert run.state == "updated"
    assert run.snapshot_version == 2
    assert len(store.snapshots) == 1


@pytest.mark.asyncio
async def test_add_operation_creates_new_snapshot():
    """ADD operations always produce a new snapshot, even with same summary."""
    cross_event = event_record("用户多次在通勤时播放周杰伦", 0, MemoryType.CROSS_EVENT)
    events = [event_record("通勤播放周杰伦", 10)]
    store = Store(
        snapshot=ProfileSnapshotView(id=uuid4(), version=1, summary="用户偏好周杰伦。"),
        items=[],
    )
    result = ProfileUpdateResult(
        operations=[
            ProfileOperation(
                action="ADD", attribute="music preference", value="周杰伦的歌曲",
                evidence_event_numbers=[0],
            )
        ],
        summary="用户偏好周杰伦。",
        usage=ModelUsage(model="Qwen3-32B", input_tokens=10, output_tokens=5, latency_ms=1),
    )
    pipeline = build_pipeline(store, Memories(events, cross_event), result)

    run = await pipeline.process(scope(), uuid4(), cross_event.id)

    assert run.state == "updated"
    assert run.snapshot_version == 2
    assert len(store.snapshots) == 1
