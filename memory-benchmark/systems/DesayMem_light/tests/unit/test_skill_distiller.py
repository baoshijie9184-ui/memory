"""Skill / preference distillation against commute walkthrough."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from desaymem_light.application.vehicle_memory import VehicleMemoryFacade
from desaymem_light.domain.vehicle import OperationEventIn, TripIn


FIXTURES = Path(__file__).resolve().parents[2] / "docs" / "examples" / "vehicle_memory_v1"


@pytest.mark.asyncio
async def test_commute_skill_walkthrough_candidate():
    facade = VehicleMemoryFacade.from_registry_path(
        FIXTURES / "tool_registry.json",
        min_independent_successes=3,
        auto_distill=False,
    )
    walk = json.loads((FIXTURES / "commute_skill_walkthrough.json").read_text(encoding="utf-8"))
    for raw in json.loads((FIXTURES / "trips.json").read_text(encoding="utf-8"))["reports"]:
        if raw["trip_id"] in ("trip-0112", "trip-0113", "trip-0114-demo"):
            await facade.ingest_trip(TripIn.model_validate(raw))

    for episode in walk["episodes"]:
        base = datetime.fromisoformat(episode["context"]["timezone"]["observed_at"])
        for index, step in enumerate(episode["steps"]):
            await facade.ingest_operation(
                OperationEventIn(
                    phase="observed_action",
                    tenant_id="tenant-demo",
                    user_id=step["actor_user_id"],
                    vehicle_id=episode["vehicle_id"],
                    source="demo_observed",
                    source_event_id=step["operation_id"],
                    tool_name=step["tool_name"],
                    args=step.get("args") or {},
                    occurred_at=base + timedelta(seconds=index),
                    result_status="success",
                    result_source=step["result_source"],
                    trip_id=episode["trip_id"],
                    context=episode["context"],
                )
            )

    facade.distill("tenant-demo", "user-demo", "vehicle-demo")
    skills = facade.store.list_skills(tenant_id="tenant-demo", user_id="user-demo")
    assert skills
    multi = [s for s in skills if s.intent == "commute_routine"]
    assert multi
    best = max(multi, key=lambda s: s.support_count)
    by_tool = {step.tool_name: step for step in best.steps}
    assert by_tool["navigate"].support == 3
    assert by_tool["play_music"].support == 3
    assert by_tool["open_mail_ui"].support == 1
    assert by_tool["open_mail_ui"].optional is True
    assert best.executable is False
    assert best.status.value == "candidate"

    owner_ops = facade.store.list_operations(tenant_id="tenant-demo", user_id="user-demo")
    assert all(op.actor_user_id != "passenger-demo" for op in owner_ops)
    owner_music = [
        op
        for op in facade.store.list_operations(tenant_id="tenant-demo")
        if op.tool_name == "play_music" and op.actor_user_id == "user-demo"
    ]
    assert len(owner_music) == 3
    passenger_music = [
        op
        for op in facade.store.list_operations(tenant_id="tenant-demo")
        if op.tool_name == "play_music" and op.actor_user_id == "passenger-demo"
    ]
    assert len(passenger_music) == 1
