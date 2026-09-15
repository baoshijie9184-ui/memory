"""Vehicle memory search answer vs suggestion contracts."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from desaymem_light.application.vehicle_memory import VehicleMemoryFacade
from desaymem_light.domain.vehicle import (
    ConditionalPreference,
    OperationEventIn,
    PreferenceStatus,
    SkillItem,
    SkillStatus,
    SkillStep,
    VehicleSearchRequest,
)


FIXTURES = Path(__file__).resolve().parents[2] / "docs" / "examples" / "vehicle_memory_v1"


@pytest.fixture
def facade() -> VehicleMemoryFacade:
    return VehicleMemoryFacade(auto_distill=False)


@pytest.mark.asyncio
async def test_suggestion_does_not_mark_candidate_executable(facade: VehicleMemoryFacade):
    await facade.ingest_operation(
        OperationEventIn(
            phase="requested",
            operation_id="op-1",
            tenant_id="t1",
            user_id="u1",
            vehicle_id="v1",
            source="app",
            tool_name="set_climate",
            args={"temperature_c": 22},
            requested_at="2026-01-12T07:25:00+08:00",
        )
    )
    await facade.ingest_operation(
        OperationEventIn(
            phase="result",
            operation_id="op-1",
            tenant_id="t1",
            vehicle_id="v1",
            source="app",
            result_status="success",
            result_source="vehicle_ack",
            actual_result={"temperature_c": 22},
            result_at="2026-01-12T07:25:03+08:00",
            vehicle_completed_at="2026-01-12T07:25:02+08:00",
        )
    )
    facade.store.upsert_skill(
        SkillItem(
            id=uuid4(),
            tenant_id="t1",
            user_id="u1",
            vehicle_id="v1",
            family_id="demo",
            intent="set_climate",
            summary="candidate only",
            steps=[SkillStep(tool_name="set_climate", args_template={"temperature_c": 22})],
            status=SkillStatus.CANDIDATE,
            support_count=3,
            executable=False,
        )
    )
    result = await facade.search(
        VehicleSearchRequest(
            request_id="r1",
            tenant_id="t1",
            user_id="u1",
            vehicle_id="v1",
            purpose="suggestion",
        )
    )
    skill_matches = [m for m in result.matches if m.match_type == "skill"]
    assert skill_matches
    assert all(m.executable is False for m in skill_matches)
    assert all(m.notes == "candidate_not_executable" for m in skill_matches)


@pytest.mark.asyncio
async def test_answer_returns_historical_operations():
    facade = VehicleMemoryFacade.from_registry_path(
        FIXTURES / "tool_registry.json",
        auto_distill=False,
    )
    await facade.ingest_operation(
        OperationEventIn(
            phase="requested",
            operation_id="op-a",
            tenant_id="t1",
            user_id="u1",
            vehicle_id="v1",
            source="app",
            tool_name="set_climate",
            args={"temperature_c": 22},
            requested_at="2026-01-12T07:25:00+08:00",
        )
    )
    await facade.ingest_operation(
        OperationEventIn(
            phase="result",
            operation_id="op-a",
            tenant_id="t1",
            vehicle_id="v1",
            source="app",
            result_status="success",
            result_source="vehicle_ack",
            actual_result={"temperature_c": 22},
            result_at="2026-01-12T07:25:03+08:00",
            vehicle_completed_at="2026-01-12T07:25:02+08:00",
        )
    )
    result = await facade.search(
        VehicleSearchRequest(
            request_id="r2",
            tenant_id="t1",
            user_id="u1",
            purpose="answer",
            tool_name="set_climate",
        )
    )
    assert result.status == "matched"
    assert any(m.match_type == "operation" for m in result.matches)


@pytest.mark.asyncio
async def test_observed_preference_not_active_for_suggestion(facade: VehicleMemoryFacade):
    facade.store.upsert_preference(
        ConditionalPreference(
            tenant_id="t1",
            user_id="u1",
            vehicle_id="v1",
            family_id="f1",
            attribute="climate.temperature_c",
            value_json=22,
            conditions={"season": "winter"},
            source_kind="observed_operation",
            status=PreferenceStatus.OBSERVED,
        )
    )
    result = await facade.search(
        VehicleSearchRequest(
            request_id="r3",
            tenant_id="t1",
            user_id="u1",
            purpose="suggestion",
            context={
                "season": {
                    "value": "winter",
                    "source": "x",
                    "observed_at": "2026-01-12T07:00:00+08:00",
                }
            },
        )
    )
    assert not any(m.match_type == "preference" for m in result.matches)


@pytest.mark.asyncio
async def test_unattributed_trip_not_returned_as_personal_history(facade: VehicleMemoryFacade):
    from desaymem_light.domain.vehicle import TripIn

    await facade.ingest_trip(TripIn(
        trip_id="trip-1", tenant_id="t1", vehicle_id="v1", source="vehicle",
        started_at="2026-01-12T07:00:00+08:00", status="completed",
        destination_label="private destination",
    ))
    result = await facade.search(VehicleSearchRequest(
        request_id="r4", tenant_id="t1", user_id="u1", purpose="answer",
    ))
    assert result.status == "no_match"


def test_upsert_preference_dedupes_by_family_version():
    """Repeated distillation passes must not duplicate the same logical preference.

    Mirrors the Postgres UNIQUE (tenant_id, family_id, version) constraint from
    migration 021; the in-memory store previously keyed by fresh uuid4 id and
    appended one row per triggering event.
    """
    from desaymem_light.adapters.memory.vehicle_store import InMemoryVehicleStore

    store = InMemoryVehicleStore()

    def make(i: int) -> ConditionalPreference:
        return ConditionalPreference(
            tenant_id="t1",
            user_id="u1",
            family_id="f1",
            attribute="climate.temperature_c",
            value_json=22,
            conditions={"season": "winter"},
            source_kind="observed_operation",
            status=PreferenceStatus.OBSERVED,
            evidence_operation_ids=[uuid4() for _ in range(i + 1)],
        )

    for i in range(5):
        store.upsert_preference(make(i))
    rows = store.list_preferences(tenant_id="t1", user_id="u1")
    assert len(rows) == 1
    assert len(rows[0].evidence_operation_ids) == 5  # latest evidence wins

    # same family but new version appends (versioned history preserved)
    bumped = make(0)
    bumped.version = 2
    store.upsert_preference(bumped)
    assert len(store.list_preferences(tenant_id="t1", user_id="u1")) == 2

    # another family is a distinct preference
    other = make(0)
    other.family_id = "f2"
    store.upsert_preference(other)
    assert len(store.list_preferences(tenant_id="t1", user_id="u1")) == 3
