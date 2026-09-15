"""Vehicle operation ingest regressions against synthetic fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from desaymem_light.application.vehicle_memory import VehicleMemoryFacade
from desaymem_light.domain.vehicle import (
    OperationEventIn,
    OperationPhase,
    ResultStatus,
)


FIXTURES = Path(__file__).resolve().parents[2] / "docs" / "examples" / "vehicle_memory_v1"


@pytest.fixture
def facade() -> VehicleMemoryFacade:
    return VehicleMemoryFacade.from_registry_path(
        FIXTURES / "tool_registry.json",
        min_independent_successes=3,
        auto_distill=True,
    )


@pytest.mark.asyncio
async def test_vehicle_operations_fixture_counts(facade: VehicleMemoryFacade):
    events = json.loads((FIXTURES / "vehicle_operations.json").read_text(encoding="utf-8"))["events"]
    for raw in events:
        await facade.ingest_operation(OperationEventIn.model_validate(raw))

    ops = facade.store.list_operations(tenant_id="tenant-demo")
    assert len(ops) == 9

    success = [o for o in ops if o.publicly_successful]
    failed = [o for o in ops if o.status == ResultStatus.FAILED]
    pending = [o for o in ops if o.status == ResultStatus.PENDING]
    assert len(success) == 7
    assert len(failed) == 1
    assert len(pending) == 1
    assert failed[0].failure_stage == "dispatch"
    assert failed[0].operation_id == "op-window-failed"
    assert pending[0].operation_id == "op-fragrance-pending"

    out_of_order = facade.store.get_command("tenant-demo", "op-ac-out-of-order")
    assert out_of_order is not None
    assert out_of_order.publicly_successful is True
    assert out_of_order.requested_at is not None


@pytest.mark.asyncio
async def test_result_before_request_not_public_until_linked(facade: VehicleMemoryFacade):
    await facade.ingest_operation(
        OperationEventIn(
            phase=OperationPhase.RESULT,
            operation_id="op-early",
            tenant_id="tenant-demo",
            vehicle_id="vehicle-demo",
            source="mobile_app",
            result_status=ResultStatus.SUCCESS,
            result_source="vehicle_ack",
            actual_result={"temperature_c": 22},
            result_at="2026-01-12T07:25:04+08:00",
            vehicle_completed_at="2026-01-12T07:25:03+08:00",
        )
    )
    early = facade.store.get_command("tenant-demo", "op-early")
    assert early is not None
    assert early.publicly_successful is False

    await facade.ingest_operation(
        OperationEventIn(
            phase=OperationPhase.REQUESTED,
            operation_id="op-early",
            tenant_id="tenant-demo",
            user_id="user-demo",
            vehicle_id="vehicle-demo",
            source="mobile_app",
            tool_name="set_climate",
            args={"temperature_c": 22},
            requested_at="2026-01-12T07:25:00+08:00",
        )
    )
    linked = facade.store.get_command("tenant-demo", "op-early")
    assert linked is not None
    assert linked.publicly_successful is True


@pytest.mark.asyncio
async def test_identical_result_replay_is_duplicate(facade: VehicleMemoryFacade):
    payload = {
        "phase": "requested",
        "operation_id": "op-dup",
        "tenant_id": "tenant-demo",
        "user_id": "user-demo",
        "vehicle_id": "vehicle-demo",
        "source": "mobile_app",
        "tool_name": "set_climate",
        "args": {"temperature_c": 22},
        "requested_at": "2026-01-12T07:25:00+08:00",
    }
    first = await facade.ingest_operation(OperationEventIn.model_validate(payload))
    second = await facade.ingest_operation(OperationEventIn.model_validate(payload))
    assert first.status == "accepted"
    assert second.status == "duplicate"


@pytest.mark.asyncio
async def test_operation_id_cannot_cross_vehicle(facade: VehicleMemoryFacade):
    request = OperationEventIn(
        phase="requested", operation_id="shared-id", tenant_id="t1",
        user_id="u1", vehicle_id="v1", source="app", tool_name="set_climate",
        args={"temperature_c": 22}, requested_at="2026-01-12T07:25:00+08:00",
    )
    assert (await facade.ingest_operation(request)).status == "accepted"
    result = OperationEventIn(
        phase="result", operation_id="shared-id", tenant_id="t1",
        vehicle_id="v2", source="app", result_status="success",
        result_source="vehicle_ack", result_at="2026-01-12T07:25:03+08:00",
    )
    assert (await facade.ingest_operation(result)).status == "conflict"
    assert facade.store.get_command("t1", "shared-id").status == ResultStatus.PENDING
    assert len(facade.store.source_events) == 1


@pytest.mark.asyncio
async def test_observed_replay_with_changed_payload_conflicts(facade: VehicleMemoryFacade):
    event = OperationEventIn(
        phase="observed_action", tenant_id="t1", vehicle_id="v1",
        source="vehicle", source_event_id="e1", tool_name="set_climate",
        args={"temperature_c": 22}, occurred_at="2026-01-12T07:25:00+08:00",
        result_source="vehicle_ack",
    )
    assert (await facade.ingest_operation(event)).status == "accepted"
    changed = event.model_copy(update={"args": {"temperature_c": 23}})
    assert (await facade.ingest_operation(changed)).status == "conflict"
    assert len(facade.store.operations) == 1
