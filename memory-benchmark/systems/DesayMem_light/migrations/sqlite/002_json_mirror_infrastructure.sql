CREATE TABLE json_mirror_registry (
    table_name TEXT PRIMARY KEY,
    primary_key_columns TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (json_valid(primary_key_columns))
);

CREATE TABLE json_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    database_name TEXT NOT NULL DEFAULT 'sqlite' CHECK (database_name = 'sqlite'),
    table_name TEXT NOT NULL,
    pk_json TEXT NOT NULL CHECK (json_valid(pk_json)),
    operation TEXT NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
    row_data TEXT NOT NULL CHECK (json_valid(row_data)),
    row_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    applied_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error TEXT
);

CREATE TABLE json_mirror_checkpoint (
    database_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    last_outbox_id INTEGER NOT NULL DEFAULT 0,
    last_reconciled_at TEXT,
    last_mismatch_count INTEGER NOT NULL DEFAULT 0 CHECK (last_mismatch_count >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (database_name, table_name)
);

INSERT INTO json_mirror_registry(
    table_name, primary_key_columns, created_at, updated_at
) VALUES
    ('history', '["id"]', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('messages_cache', '["id"]', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
