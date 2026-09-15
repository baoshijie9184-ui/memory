CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE history (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    occupant_id TEXT NOT NULL,
    session_id TEXT,
    memory_id TEXT NOT NULL,
    old_memory TEXT,
    new_memory TEXT,
    event TEXT NOT NULL,
    source_type TEXT,
    source_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0, 1)),
    actor_id TEXT,
    role TEXT
);

CREATE TABLE messages_cache (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    occupant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    name TEXT,
    occurred_at TEXT NOT NULL,
    cached_at TEXT NOT NULL,
    expires_at TEXT,
    UNIQUE (tenant_id, user_id, vehicle_id, occupant_id, session_id, sequence_no)
);
