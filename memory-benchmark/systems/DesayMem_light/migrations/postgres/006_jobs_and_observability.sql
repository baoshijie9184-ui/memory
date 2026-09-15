CREATE TABLE desaymem_light.memory_jobs (
    id UUID PRIMARY KEY,
    job_type TEXT NOT NULL CHECK (
        job_type IN ('session_segment', 'fact_extract', 'event_build', 'cross_event', 'profile_update', 'sqlite_project')
    ),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    occupant_id TEXT NOT NULL,
    session_id TEXT,
    plugin_name TEXT NOT NULL,
    plugin_version TEXT NOT NULL,
    payload_schema_version INTEGER NOT NULL DEFAULT 1 CHECK (payload_schema_version >= 1),
    idempotency_key TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'ready'
        CHECK (status IN ('ready', 'running', 'completed', 'retry', 'dead')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts >= 1),
    next_run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at TIMESTAMPTZ,
    locked_by TEXT,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    UNIQUE (job_type, idempotency_key)
);

CREATE TABLE desaymem_light.cross_event_checkpoints (
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    occupant_id TEXT NOT NULL,
    last_event_occurred_at TIMESTAMPTZ,
    last_event_id UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id, vehicle_id, occupant_id)
);

CREATE TABLE desaymem_light.profile_checkpoints (
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    occupant_id TEXT NOT NULL,
    last_event_occurred_at TIMESTAMPTZ,
    last_event_id UUID,
    last_snapshot_version INTEGER CHECK (last_snapshot_version IS NULL OR last_snapshot_version >= 1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id, vehicle_id, occupant_id)
);

CREATE TABLE desaymem_light.memory_audit_events (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT,
    occupant_id TEXT NOT NULL,
    entity_table TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    operation TEXT NOT NULL
        CHECK (operation IN ('ADD', 'UPDATE', 'CONFIRM', 'COEXIST', 'SUPERSEDE', 'DELETE', 'NOOP')),
    before_data JSONB,
    after_data JSONB,
    source_topic_id UUID,
    request_id UUID,
    job_id UUID,
    model TEXT,
    prompt_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE desaymem_light.llm_usage (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    occupant_id TEXT NOT NULL,
    request_id UUID,
    job_id UUID,
    purpose TEXT NOT NULL CHECK (purpose IN ('fact_extract', 'cross_event', 'profile_update')),
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER memory_jobs_set_updated_at
BEFORE UPDATE ON desaymem_light.memory_jobs
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();

CREATE TRIGGER cross_event_checkpoints_set_updated_at
BEFORE UPDATE ON desaymem_light.cross_event_checkpoints
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();

CREATE TRIGGER profile_checkpoints_set_updated_at
BEFORE UPDATE ON desaymem_light.profile_checkpoints
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();
