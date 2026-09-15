-- P1: structured vehicle operation source events and aggregate rows.
CREATE TABLE desaymem_light.activity_source_events (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    source TEXT NOT NULL,
    source_event_id TEXT,
    dedup_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    phase TEXT NOT NULL,
    disposition TEXT NOT NULL DEFAULT 'accepted'
        CHECK (disposition IN ('accepted', 'duplicate', 'conflict', 'rejected')),
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX activity_source_events_accepted_dedup_idx
ON desaymem_light.activity_source_events(tenant_id, dedup_key)
WHERE disposition = 'accepted';

CREATE INDEX activity_source_events_tenant_time_idx
ON desaymem_light.activity_source_events(tenant_id, received_at DESC);

CREATE TABLE desaymem_light.activity_operations (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    operation_kind TEXT NOT NULL DEFAULT 'command'
        CHECK (operation_kind IN ('command', 'observed_action')),
    operation_id TEXT,
    source TEXT NOT NULL,
    source_event_id TEXT,
    requester_user_id TEXT,
    actor_user_id TEXT,
    vehicle_id TEXT NOT NULL,
    tool_name TEXT,
    schema_version INT NOT NULL DEFAULT 1,
    requested_args_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    actual_args_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    requested_at TIMESTAMPTZ,
    result_at TIMESTAMPTZ,
    vehicle_completed_at TIMESTAMPTZ,
    occurred_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('success', 'failed', 'pending', 'unknown', 'cancelled', 'conflict', 'unmatched_result')),
    validation_status TEXT NOT NULL DEFAULT 'unverified'
        CHECK (validation_status IN ('verified', 'unverified', 'conflict')),
    result_source TEXT,
    failure_stage TEXT,
    error_code TEXT,
    trip_id TEXT,
    interaction_id TEXT,
    context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    publicly_successful BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX activity_operations_command_uidx
ON desaymem_light.activity_operations(tenant_id, operation_id)
WHERE operation_kind = 'command' AND operation_id IS NOT NULL;

CREATE UNIQUE INDEX activity_operations_observed_uidx
ON desaymem_light.activity_operations(tenant_id, source, source_event_id)
WHERE operation_kind = 'observed_action' AND source_event_id IS NOT NULL;

CREATE INDEX activity_operations_scope_time_idx
ON desaymem_light.activity_operations(tenant_id, vehicle_id, requested_at DESC NULLS LAST);

CREATE TRIGGER activity_operations_set_updated_at
BEFORE UPDATE ON desaymem_light.activity_operations
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();

INSERT INTO desaymem_light.json_mirror_registry(table_name, primary_key_columns) VALUES
    ('activity_source_events', '["id"]'),
    ('activity_operations', '["id"]')
ON CONFLICT (table_schema, table_name) DO UPDATE SET
    primary_key_columns = EXCLUDED.primary_key_columns,
    enabled = true,
    schema_version = EXCLUDED.schema_version;

CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE
ON desaymem_light.activity_source_events
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');

CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE
ON desaymem_light.activity_operations
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
