"""Offline contract checks for the PostgreSQL vehicle adapter."""

from pathlib import Path
from uuid import uuid4

from desaymem_light.adapters.postgres.vehicle_memory import _operation_from_row, _trip_from_row


def test_operation_row_ignores_database_only_columns():
    row = {
        "id": uuid4(), "tenant_id": "t1", "source": "app", "vehicle_id": "v1",
        "operation_kind": "command", "status": "pending",
        "validation_status": "unverified", "requested_args_json": {"level": 2},
        "actual_args_json": {}, "context_json": {}, "created_at": None,
    }
    item = _operation_from_row(row)
    assert item.requested_args == {"level": 2}
    assert item.vehicle_id == "v1"


def test_trip_row_ignores_database_only_columns():
    row = {
        "id": uuid4(), "tenant_id": "t1", "trip_id": "trip-1",
        "vehicle_id": "v1", "source": "vehicle", "status": "completed",
        "started_at": "2026-01-12T07:00:00+08:00", "context_json": {},
        "created_at": None,
    }
    assert _trip_from_row(row).trip_id == "trip-1"


def test_episode_mirror_fix_uses_two_primary_key_arguments():
    path = Path(__file__).resolve().parents[2] / "migrations/postgres/022_episode_operation_mirror_key.sql"
    sql = path.read_text(encoding="utf-8")
    assert "capture_json_outbox('episode_id', 'operation_id')" in sql
