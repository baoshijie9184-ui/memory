-- Memory observability audit table.
-- Records lifecycle events for L1 (semantic), L2 (episodic), and L3 (profile)
-- memories. PostgreSQL-only; SQLite history.db continues to serve mem0-compat.
-- Safe to re-run: all objects use IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS memory_audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    memory_id TEXT,
    layer TEXT NOT NULL,
    object_type TEXT NOT NULL,
    event TEXT NOT NULL,
    old_data JSONB,
    new_data JSONB,
    reason TEXT,
    source TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_memory_audit_layer
        CHECK (layer IN ('L1', 'L2', 'L3')),
    CONSTRAINT chk_memory_audit_event
        CHECK (event IN (
            'ADD',
            'UPDATE',
            'CONFIRM',
            'COMPLETE',
            'SUPERSEDE',
            'COEXIST',
            'DELETE',
            'FORGET'
        ))
);

CREATE INDEX IF NOT EXISTS idx_memory_audit_user_time
ON memory_audit_events (
    tenant_id,
    user_id,
    created_at DESC,
    id DESC
);

CREATE INDEX IF NOT EXISTS idx_memory_audit_memory
ON memory_audit_events (
    tenant_id,
    user_id,
    memory_id,
    created_at DESC
);
