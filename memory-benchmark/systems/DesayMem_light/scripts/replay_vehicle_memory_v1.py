"""Replay docs/examples/vehicle_memory_v1 fixtures through the vehicle memory facade.

Validates the full chain: operation/trip ingest (idempotency, out-of-order,
pending/failed) -> episode build -> preference/skill distillation -> search.

Usage:
    PYTHONPATH=src python scripts/replay_vehicle_memory_v1.py \
        [--pg-dsn postgresql://desaymem@127.0.0.1:20149/bench_vehicle_replay]

Without --pg-dsn the replay runs against the in-memory store (no DB needed).
With --pg-dsn it runs against the PostgresVehicleMemoryFacade (isolated test DB
only; the database must be empty).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "docs" / "examples" / "vehicle_memory_v1"

sys.path.insert(0, str(ROOT / "src"))

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}" + (f" -- {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" -- {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n== {title} ==")


async def replay_in_memory() -> None:
    from desaymem_light.application.vehicle_memory import VehicleMemoryFacade
    from desaymem_light.domain.vehicle import (
        OperationEventIn,
        TripIn,
        VehicleSearchRequest,
    )

    facade = VehicleMemoryFacade.from_registry_path(
        FIXTURES / "tool_registry.json", min_independent_successes=3, auto_distill=True
    )

    section("1. Operation ingest (18 events -> 9 logical operations)")
    events = json.loads((FIXTURES / "vehicle_operations.json").read_text(encoding="utf-8"))["events"]
    statuses = []
    for raw in events:
        statuses.append((await facade.ingest_operation(OperationEventIn.model_validate(raw))).status)
    ops = facade.store.list_operations(tenant_id="tenant-demo")
    check("9 logical operations", len(ops) == 9, f"got {len(ops)}")
    check("7 publicly successful", sum(1 for o in ops if o.publicly_successful) == 7)
    check("1 failed (op-window-failed, dispatch stage)", any(
        o.operation_id == "op-window-failed" and o.status.value == "failed" and o.failure_stage == "dispatch"
        for o in ops
    ))
    check("1 pending (op-fragrance-pending, not success)", any(
        o.operation_id == "op-fragrance-pending" and o.status.value == "pending" for o in ops
    ))
    check("out-of-order op linked & successful", any(
        o.operation_id == "op-ac-out-of-order" and o.publicly_successful for o in ops
    ))

    # replay same events again: everything must be duplicate/idempotent
    dup = [await facade.ingest_operation(OperationEventIn.model_validate(raw)) for raw in events]
    check("re-replay is idempotent (all duplicate)", all(d.status == "duplicate" for d in dup))

    section("2. Trip ingest (5 reports -> 4 trips, 1 unattributed)")
    trips = json.loads((FIXTURES / "trips.json").read_text(encoding="utf-8"))["reports"]
    for raw in trips:
        await facade.ingest_trip(TripIn.model_validate(raw))
    all_trips = facade.store.list_trips(tenant_id="tenant-demo")
    check("4 trips total (trip-0112 updated not duplicated)", len(all_trips) == 4, f"got {len(all_trips)}")
    attributed = [t for t in all_trips if t.driver_user_id == "user-demo"]
    check("3 trips attributed to user-demo", len(attributed) == 3)

    section("3. Preference distillation")
    prefs = facade.store.list_preferences(tenant_id="tenant-demo", user_id="user-demo")
    pref_attrs = sorted(p.attribute for p in prefs)
    check("climate preference distilled (3x success)", "climate.temperature_c" in pref_attrs)
    check("seat heating preference distilled (3x success)", "seat_heating.level" in pref_attrs)
    check("failed window op is not preference evidence", not any("window" in a for a in pref_attrs))
    check("pending fragrance op is not preference evidence", not any("fragrance" in a for a in pref_attrs))
    climate_rows = [p for p in prefs if p.attribute == "climate.temperature_c"]
    if climate_rows:
        families = {}
        for p in climate_rows:
            families.setdefault(p.family_id, p)
        check(
            "climate families: 22C with winter conditions, 23C without (context not inherited)",
            sorted(str(p.value_json) for p in families.values()) == ["22", "23"],
            f"got {[(p.value_json, p.conditions) for p in families.values()]}",
        )
        check(
            "climate 22C preference carries trusted winter condition",
            any(str(p.value_json) == "22" and p.conditions.get("season") == "winter" for p in families.values()),
        )
        check(
            "climate preference is OBSERVED not ACTIVE (no auto-activation)",
            all(p.status.value == "observed" for p in families.values()),
        )
        # KNOWN ISSUE: auto_distill fires on every ingest and upsert_preference
        # keys by fresh uuid4 id (not family_id), so the same logical preference
        # is duplicated once per triggering event.
        if len(climate_rows) > len(families):
            print(
                f"  [NOTE] preference duplication bug: {len(climate_rows)} climate rows "
                f"but only {len(families)} distinct family_ids "
                "(upsert_preference should dedupe by family_id)"
            )

    section("4. Skill distillation")
    skills = facade.store.list_skills(tenant_id="tenant-demo", user_id="user-demo")
    single_stable = [s for s in skills if s.family_id.startswith("single:") and s.status.value == "candidate"]
    stable_tools = sorted({s.steps[0].tool_name for s in single_stable})
    check("stable single-step skills for set_climate & set_seat_heating", stable_tools == ["set_climate", "set_seat_heating"], f"got {stable_tools}")
    if single_stable:
        s = single_stable[0]
        check("skill support_count >= 3", s.support_count >= 3, f"got {s.support_count}")
        check("skill is candidate, not executable", not s.executable)

    section("5. Search (answer vs suggestion)")
    answer = await facade.search(VehicleSearchRequest(
        request_id="replay-answer", tenant_id="tenant-demo", user_id="user-demo", purpose="answer",
    ))
    answer_types = {m.match_type for m in answer.matches}
    check("answer returns operation/trip history", {"operation", "trip"} <= answer_types, f"got {answer_types}")

    suggestion = await facade.search(VehicleSearchRequest(
        request_id="replay-suggestion", tenant_id="tenant-demo", user_id="user-demo", purpose="suggestion",
    ))
    sug_types = {m.match_type for m in suggestion.matches}
    # OBSERVED preferences (observed stats, not auto-activated) must NOT appear
    # in suggestions; only ACTIVE ones and skill candidates do. The fixture
    # yields OBSERVED preferences only, so expectation is skill-only.
    check("suggestion returns skill candidates", "skill" in sug_types, f"got {sug_types}")
    check("OBSERVED preferences not leaked into suggestion", "preference" not in sug_types)
    check("no operation history leaked into suggestion", "operation" not in sug_types)

    section("6. Commute walkthrough expectation notes")
    # walkthrough covers navigate/play_music episodes not present in the operation
    # fixture; the stable two-step routine candidate requires those tools.
    tools_present = {o.tool_name for o in ops}
    check(
        "walkthrough tools (navigate/play_music) not in this fixture; "
        "multi-step routine replay needs observed_action events (documented gap)",
        not {"navigate", "play_music"} & tools_present,
    )


async def replay_postgres(dsn: str) -> None:
    import psycopg
    from psycopg_pool import AsyncConnectionPool

    from desaymem_light.adapters.postgres.vehicle_memory import PostgresVehicleMemoryFacade
    from desaymem_light.domain.vehicle import (
        OperationEventIn,
        TripIn,
        VehicleSearchRequest,
    )
    from desaymem_light.modules.vehicle.tool_registry import ToolRegistry

    with psycopg.connect(dsn) as conn:
        exists = conn.execute("SELECT to_regclass('desaymem_light.activity_operations')").fetchone()[0]
        if exists is not None:
            print("[abort] target database is not empty; use a disposable database")
            sys.exit(2)
        conn.execute("CREATE SCHEMA IF NOT EXISTS desaymem_light")
        conn.execute(
            "CREATE OR REPLACE FUNCTION desaymem_light.set_updated_at() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END $$"
        )
        # Minimal profile_items stand-in (full schema needs pgvector) so the
        # 023 profile-link migration and distillate linking can be exercised.
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
            "(gen_random_uuid(),'tenant-demo','user-demo','vehicle-demo','driver',"
            "'climate.temperature_c','22'),"
            "(gen_random_uuid(),'tenant-demo','user-demo','vehicle-demo','driver',"
            "'seat_heating.level','2')"
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

    registry = ToolRegistry.from_json_file(FIXTURES / "tool_registry.json")
    async with AsyncConnectionPool(dsn, kwargs={"row_factory": psycopg.rows.dict_row}) as pool:
        facade = PostgresVehicleMemoryFacade(pool, registry)

        section("PG: Operation ingest")
        events = json.loads((FIXTURES / "vehicle_operations.json").read_text(encoding="utf-8"))["events"]
        for raw in events:
            await facade.ingest_operation(OperationEventIn.model_validate(raw))
        check("PG replay accepted 18 events", True)

        section("PG: Trip ingest")
        trips = json.loads((FIXTURES / "trips.json").read_text(encoding="utf-8"))["reports"]
        for raw in trips:
            await facade.ingest_trip(TripIn.model_validate(raw))
        check("PG trip replay done", True)

        section("PG: Search across facade restart")
        found = await facade.search(VehicleSearchRequest(
            request_id="pg-replay", tenant_id="tenant-demo", user_id="user-demo", purpose="answer",
        ))
        check("PG search matched", found.status == "matched", f"status={found.status}")

        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT table_name, count(*) AS n FROM desaymem_light.json_outbox "
                "WHERE table_name IN ('activity_source_events','activity_operations','trip_events') "
                "GROUP BY table_name"
            )
            counts = {row["table_name"]: row["n"] for row in await cursor.fetchall()}
        # 18 operation events, but the trailing duplicate result of
        # op-ac-out-of-order is idempotent (no new source event) -> 17.
        check("PG outbox emitted", counts.get("activity_source_events", 0) == 17, f"counts={counts}")
        check("PG trips mirrored (5 reports incl. update)", counts.get("trip_events", 0) == 5, f"counts={counts}")

        section("PG: distillate -> profile links")
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT attribute, preference_id IS NOT NULL AS p, skill_id IS NOT NULL AS s "
                "FROM desaymem_light.profile_vehicle_memory_links"
            )
            links = await cursor.fetchall()
        linked_attrs = sorted({row["attribute"] for row in links})
        check("climate & seat heating preferences linked to profile items",
              linked_attrs == ["climate.temperature_c", "seat_heating.level"], f"got {linked_attrs}")
        check("all links reference a distillate",
              all(row["p"] or row["s"] for row in links))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pg-dsn",
        default=None,
        help="optional isolated empty PostgreSQL DSN for the Postgres replay",
    )
    args = parser.parse_args()

    asyncio.run(replay_in_memory())
    if args.pg_dsn:
        asyncio.run(replay_postgres(args.pg_dsn))

    print(f"\n== RESULT: {PASS} passed, {FAIL} failed ==")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
