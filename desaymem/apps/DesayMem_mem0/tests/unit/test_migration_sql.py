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
