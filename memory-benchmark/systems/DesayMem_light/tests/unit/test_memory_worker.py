from uuid import uuid4

import pytest

from desaymem_light.domain.models import JobRecord, MemoryScope, SessionScope
from desaymem_light.workers.memory_worker import MemoryJobDispatcher


class Calls:
    def __init__(self):
        self.values = []


class SessionPipeline:
    def __init__(self, calls): self.calls = calls
    async def process(self, scope, **options): self.calls.values.append(("session", scope, options))


class MemoryPipeline:
    def __init__(self, calls): self.calls = calls
    async def process_fact(self, topic_id, request_id):
        self.calls.values.append(("fact", topic_id, request_id))
    async def process_event(self, topic_id, request_id):
        self.calls.values.append(("event", topic_id, request_id))


class InsightPipeline:
    def __init__(self, calls): self.calls = calls
    async def process_cross_event(self, scope, request_id, force=False):
        self.calls.values.append(("cross", scope, request_id, force))


class ProfilePipeline:
    def __init__(self, calls): self.calls = calls
    async def process(self, scope, request_id, cross_event_id):
        self.calls.values.append(("profile", scope, request_id, cross_event_id))


def dispatcher(calls):
    return MemoryJobDispatcher(
        session_pipeline=SessionPipeline(calls),
        memory_pipeline=MemoryPipeline(calls),
        insight_pipeline=InsightPipeline(calls),
        profile_pipeline=ProfilePipeline(calls),
    )


def job(job_type, payload):
    return JobRecord(
        id=uuid4(), job_type=job_type, payload=payload, attempts=1, max_attempts=3,
    )


@pytest.mark.asyncio
async def test_dispatcher_preserves_ids_across_the_memory_chain():
    calls = Calls()
    target = dispatcher(calls)
    topic_id = uuid4()
    cross_event_id = uuid4()
    session_scope = SessionScope(
        tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver", session_id="s"
    )
    memory_scope = MemoryScope(**session_scope.model_dump(exclude={"session_id"}))
    jobs = [
        job("session_segment", {"scope": session_scope.model_dump(mode="json")}),
        job("fact_extract", {"topic_id": str(topic_id)}),
        job("event_build", {"topic_id": str(topic_id)}),
        job("cross_event", {"scope": memory_scope.model_dump(mode="json"), "force": True}),
        job("profile_update", {
            "scope": memory_scope.model_dump(mode="json"),
            "cross_event_id": str(cross_event_id),
        }),
    ]

    for value in jobs:
        await target.dispatch(value)

    assert calls.values[0] == ("session", session_scope, {
        "force_flush": False,
        "flush_reason": "manual",
        "expected_last_message_id": None,
        "expected_last_ingested_at": None,
    })
    assert calls.values[1] == ("fact", topic_id, jobs[1].id)
    assert calls.values[2] == ("event", topic_id, jobs[2].id)
    assert calls.values[3] == ("cross", memory_scope, jobs[3].id, True)
    assert calls.values[4] == ("profile", memory_scope, jobs[4].id, cross_event_id)
