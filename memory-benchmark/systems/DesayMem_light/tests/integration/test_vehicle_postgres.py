"""Opt-in P1 PostgreSQL integration test (requires an isolated empty test DB).

Set DESAYMEM_TEST_PG_DSN to a disposable database. Never point it at production.
"""

from __future__ import annotations

import os
import asyncio
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from desaymem_light.adapters.postgres.vehicle_memory import PostgresVehicleMemoryFacade
from desaymem_light.domain.vehicle import OperationEventIn, TripIn, VehicleSearchRequest
from desaymem_light.modules.vehicle.tool_registry import ToolRegistry


DSN = os.getenv("DESAYMEM_TEST_PG_DSN")
ROOT = Path(__file__).resolve().parents[2]
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(scope="module")
def prepared_database():
    if not DSN:
        pytest.skip("set DESAYMEM_TEST_PG_DSN to an isolated empty PostgreSQL database")
    with psycopg.connect(DSN) as conn:
        exists = conn.execute("SELECT to_regclass('desaymem_light.activity_operations')").fetchone()[0]
        if exists is not None:
            pytest.skip("integration test requires an empty disposable database")
        conn.execute("CREATE SCHEMA IF NOT EXISTS desaymem_light")
        conn.execute(
            "CREATE OR REPLACE FUNCTION desaymem_light.set_updated_at() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END $$"
        )
        # 023 links reference profile_items; the full profile schema needs
        # pgvector, so create a minimal stand-in BEFORE running 023.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS desaymem_light.profile_items ("
            "id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, "
            "vehicle_id TEXT NOT NULL, occupant_id TEXT NOT NULL, "
            "attribute TEXT NOT NULL, value TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'active')"
        )
        conn.execute(
            "INSERT INTO desaymem_light.profile_items "
            "(id,tenant_id,user_id,vehicle_id,occupant_id,attribute,value) VALUES "
            "(gen_random_uuid(),'test-tenant','test-user','test-vehicle','driver',"
            "'climate.temperature_c','22')"
        )
        for name in (
            "008_json_mirror_infrastructure.sql",
            "019_activity_operations.sql",
            "020_trips_and_episodes.sql",
            "021_preferences_and_skills.sql",
            "022_episode_operation_mirror_key.sql",
            "023_profile_vehicle_memory_links.sql",
        ):
            conn.execute((ROOT / "migrations/postgres" / name).read_text(encoding="utf-8"))
    return DSN


@pytest.mark.asyncio
async def test_operation_and_trip_survive_new_facade_and_emit_outbox(prepared_database):
    registry = ToolRegistry.from_json_file(
        ROOT / "docs/examples/vehicle_memory_v1/tool_registry.json"
    )
    async with AsyncConnectionPool(prepared_database, kwargs={"row_factory": psycopg.rows.dict_row}) as pool:
        first = PostgresVehicleMemoryFacade(pool, registry)
        request = OperationEventIn(
            phase="requested", operation_id="pg-op-1", tenant_id="test-tenant",
            user_id="test-user", vehicle_id="test-vehicle", source="app",
            tool_name="set_climate", args={"temperature_c": 22},
            requested_at="2026-01-12T07:25:00+08:00",
        )
        result = OperationEventIn(
            phase="result", operation_id="pg-op-1", tenant_id="test-tenant",
            vehicle_id="test-vehicle", source="app", result_status="success",
            result_source="vehicle_ack", actual_result={"temperature_c": 22},
            result_at="2026-01-12T07:25:03+08:00",
        )
        assert (await first.ingest_operation(request)).status == "accepted"
        assert (await first.ingest_operation(result)).status == "accepted"
        assert (await first.ingest_operation(result)).status == "duplicate"
        assert (await first.ingest_trip(TripIn(
            trip_id="pg-trip-1", tenant_id="test-tenant", vehicle_id="test-vehicle",
            driver_user_id="test-user", source="vehicle",
            started_at="2026-01-12T07:20:00+08:00", status="completed",
        ))).status == "accepted"

        second = PostgresVehicleMemoryFacade(pool, registry)
        found = await second.search(VehicleSearchRequest(
            request_id="pg-search-1", tenant_id="test-tenant", user_id="test-user",
        ))
        # Preferences are now distilled & persisted on ingest, so answer search
        # also returns the distilled climate preference (OBSERVED status).
        found_types = {match.match_type for match in found.matches}
        assert {"operation", "trip"} <= found_types
        assert any(
            m.match_type == "preference"
            and m.item.get("attribute") == "climate.temperature_c"
            and m.item.get("status") == "observed"
            for m in found.matches
        )
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT count(*) AS n FROM desaymem_light.conditional_preferences "
                "WHERE tenant_id='test-tenant'"
            )
            assert (await cursor.fetchone())["n"] >= 1
            cursor = await conn.execute(
                "SELECT count(*) AS n FROM desaymem_light.conditional_preference_evidence"
            )
            assert (await cursor.fetchone())["n"] >= 1
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT table_name, count(*) AS n FROM desaymem_light.json_outbox "
                "WHERE table_name IN ('activity_source_events','activity_operations','trip_events') "
                "GROUP BY table_name"
            )
            counts = {row["table_name"]: row["n"] for row in await cursor.fetchall()}
        assert counts["activity_source_events"] == 2
        assert counts["activity_operations"] == 2
        assert counts["trip_events"] == 1
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT link_kind, attribute, profile_item_id IS NOT NULL AS has_profile "
                "FROM desaymem_light.profile_vehicle_memory_links"
            )
            links = await cursor.fetchall()
        assert links, "expected distillate->profile links"
        assert all(row["link_kind"] == "supports" for row in links)
        assert any(row["attribute"] == "climate.temperature_c" for row in links)
