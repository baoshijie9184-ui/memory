import json
import sqlite3
from pathlib import Path

from desaymem_light.adapters.migrations import discover_migrations
from desaymem_light.adapters.sqlite.migrations import apply_sqlite_migrations
from desaymem_light.schema import LATEST_POSTGRES_SCHEMA_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[2]
POSTGRES_MIGRATIONS = PROJECT_ROOT / "migrations" / "postgres"
SQLITE_MIGRATIONS = PROJECT_ROOT / "migrations" / "sqlite"


def test_migration_sequences_are_contiguous() -> None:
    assert [item.version for item in discover_migrations(POSTGRES_MIGRATIONS)] == list(
        range(1, LATEST_POSTGRES_SCHEMA_VERSION + 1)
    )
    assert [item.version for item in discover_migrations(SQLITE_MIGRATIONS)] == list(range(1, 5))


def test_postgres_registers_every_business_table_for_json_mirror() -> None:
    registration_sql = (POSTGRES_MIGRATIONS / "009_json_mirror_registration.sql").read_text("utf-8")
    expected = {
        "session_messages",
        "topic_segments",
        "topic_segment_messages",
        "memory_items",
        "memory_evidence",
        "memory_relations",
        "memory_entities",
        "memory_entity_links",
        "tag_definitions",
        "memory_tag_links",
        "profile_items",
        "profile_item_evidence",
        "profile_item_relations",
        "profile_snapshots",
        "profile_snapshot_items",
        "memory_jobs",
        "cross_event_checkpoints",
        "profile_checkpoints",
        "memory_audit_events",
        "llm_usage",
    }
    for table in expected:
        assert f"('{table}'," in registration_sql
        assert f"ON desaymem_light.{table}" in registration_sql
    assert registration_sql.count("CREATE TRIGGER json_mirror_outbox") == len(expected)


def test_profile_evidence_migration_uses_stable_id_for_full_provenance() -> None:
    sql = (POSTGRES_MIGRATIONS / "016_profile_evidence_identity.sql").read_text("utf-8")
    assert "ADD COLUMN id UUID" in sql
    assert "via_cross_event_id, evidence_role" in sql
    assert "NULLS NOT DISTINCT" in sql
    assert "capture_json_outbox('id')" in sql
    assert "'DELETE'" in sql
    assert "jsonb_build_object(" in sql
    assert "SET id = id" in sql


def test_idle_timeout_is_an_allowed_topic_boundary() -> None:
    sql = (POSTGRES_MIGRATIONS / "017_idle_timeout_topic_boundary.sql").read_text("utf-8")
    assert "'idle_timeout'" in sql
    assert "'message_limit'" in sql


def test_topic_fact_links_are_backfilled_and_json_mirrored() -> None:
    sql = (POSTGRES_MIGRATIONS / "018_topic_fact_links.sql").read_text("utf-8")
    assert "CREATE TABLE desaymem_light.topic_fact_links" in sql
    assert "UNIQUE (topic_segment_id, fact_id)" in sql
    assert "'ADD'" in sql
    assert "'topic_fact_links', '[\"id\"]'" in sql
    assert "capture_json_outbox('id')" in sql


def test_sqlite_migrations_and_history_mirror_lifecycle() -> None:
    connection = sqlite3.connect(":memory:")
    applied = apply_sqlite_migrations(connection, SQLITE_MIGRATIONS)
    assert [item.version for item in applied] == [1, 2, 3, 4]
    assert apply_sqlite_migrations(connection, SQLITE_MIGRATIONS) == []

    values = (
        "history-1",
        "tenant-1",
        "user-1",
        "vehicle-1",
        "driver",
        "session-1",
        "memory-1",
        None,
        "likes music",
        "ADD",
        "topic",
        "topic-1",
        "2026-09-09T00:00:00Z",
        "2026-09-09T00:00:00Z",
        0,
        None,
        "user",
    )
    connection.execute(
        "INSERT INTO history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values,
    )
    connection.execute("UPDATE history SET new_memory='likes jazz' WHERE id='history-1'")
    connection.execute("DELETE FROM history WHERE id='history-1'")
    operations = connection.execute("SELECT operation, row_data, row_version FROM json_outbox ORDER BY id").fetchall()

    assert [row[0] for row in operations] == ["INSERT", "UPDATE", "DELETE"]
    assert json.loads(operations[1][1])["new_memory"] == "likes jazz"
    assert all(row[2] > 0 for row in operations)


def test_sqlite_json_contains_every_history_column() -> None:
    connection = sqlite3.connect(":memory:")
    apply_sqlite_migrations(connection, SQLITE_MIGRATIONS)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(history)")}
    trigger_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='history_json_mirror_insert'"
    ).fetchone()[0]
    for column in columns:
        assert f"'{column}', NEW.{column}" in trigger_sql


def test_profile_vehicle_memory_links_bridge_distillates_to_profile() -> None:
    sql = (POSTGRES_MIGRATIONS / "023_profile_vehicle_memory_links.sql").read_text("utf-8")
    assert "CREATE TABLE desaymem_light.profile_vehicle_memory_links" in sql
    assert "REFERENCES desaymem_light.profile_items(id)" in sql
    assert "REFERENCES desaymem_light.conditional_preferences(id)" in sql
    assert "REFERENCES desaymem_light.skill_items(id)" in sql
    assert "profile_vehicle_link_one_target" in sql
    assert "capture_json_outbox('id')" in sql
