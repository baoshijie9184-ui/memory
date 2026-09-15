from desaymem.cli import _statements, default_migration_path, default_migrations_dir


def test_sql_split_keeps_create_statements():
    sql = default_migration_path().read_text(encoding="utf-8")
    stmts = _statements(sql)
    assert any("CREATE EXTENSION IF NOT EXISTS vector" in s for s in stmts)
    assert any("CREATE TABLE IF NOT EXISTS memory_items" in s for s in stmts)
    assert any("idx_memory_tenant_user" in s for s in stmts)
    sql_002 = default_migrations_dir().joinpath("002_session_entities.sql").read_text(encoding="utf-8")
    stmts_002 = _statements(sql_002)
    assert any("CREATE TABLE IF NOT EXISTS session_messages" in s for s in stmts_002)
    assert any("CREATE TABLE IF NOT EXISTS memory_entities" in s for s in stmts_002)
    assert any("memory_type" in s for s in stmts_002)
    sql_004 = default_migrations_dir().joinpath("004_layers.sql").read_text(encoding="utf-8")
    stmts_004 = _statements(sql_004)
    assert any("CREATE TABLE IF NOT EXISTS profile_beliefs" in s for s in stmts_004)
    assert any("CREATE TABLE IF NOT EXISTS user_profile_snapshots" in s for s in stmts_004)
    assert default_migration_path().exists()


# ── 006 migration: audit table ──────────────────────────────────────────────


def test_migration_006_splits_correctly():
    """006 must split into exactly 3 statements (1 table + 2 indexes)."""
    sql_006 = default_migrations_dir().joinpath("006_memory_observability.sql").read_text(encoding="utf-8")
    stmts = _statements(sql_006)
    assert len(stmts) == 3


def test_migration_006_no_phantom_sqlite_statement():
    """The semicolon inside a comment must not produce a phantom statement."""
    sql_006 = default_migrations_dir().joinpath("006_memory_observability.sql").read_text(encoding="utf-8")
    stmts = _statements(sql_006)
    for s in stmts:
        assert not s.startswith("SQLite"), f"Phantom statement from comment: {s[:80]}"


def test_migration_006_creates_audit_table():
    sql_006 = default_migrations_dir().joinpath("006_memory_observability.sql").read_text(encoding="utf-8")
    stmts = _statements(sql_006)
    assert any("CREATE TABLE IF NOT EXISTS memory_audit_events" in s for s in stmts)


def test_migration_006_creates_indexes():
    sql_006 = default_migrations_dir().joinpath("006_memory_observability.sql").read_text(encoding="utf-8")
    stmts = _statements(sql_006)
    assert any("idx_memory_audit_user_time" in s for s in stmts)
    assert any("idx_memory_audit_memory" in s for s in stmts)


def test_migration_006_uses_if_not_exists():
    """006 must be idempotent: all CREATE statements use IF NOT EXISTS."""
    sql_006 = default_migrations_dir().joinpath("006_memory_observability.sql").read_text(encoding="utf-8")
    stmts = _statements(sql_006)
    for s in stmts:
        assert "IF NOT EXISTS" in s, f"Statement lacks IF NOT EXISTS: {s[:80]}"


def test_migration_006_has_no_drop_or_truncate():
    """006 must not DROP or TRUNCATE anything."""
    sql_006 = default_migrations_dir().joinpath("006_memory_observability.sql").read_text(encoding="utf-8")
    upper = sql_006.upper()
    assert "DROP " not in upper
    assert "TRUNCATE" not in upper


def test_statements_strips_comment_with_semicolon():
    """The parser must strip comments before splitting on ';'."""
    sql = (
        "-- This comment has a ; semicolon inside\n"
        "CREATE TABLE IF NOT EXISTS t (id int);\n"
        "-- another comment; with semicolon\n"
        "CREATE INDEX IF NOT EXISTS idx ON t (id);\n"
    )
    stmts = _statements(sql)
    assert len(stmts) == 2
    assert "CREATE TABLE IF NOT EXISTS t" in stmts[0]
    assert "CREATE INDEX IF NOT EXISTS idx" in stmts[1]
