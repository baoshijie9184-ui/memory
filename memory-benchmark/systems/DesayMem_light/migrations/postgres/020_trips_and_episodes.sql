-- P1/P2: trips and episode grouping for skill evidence.
CREATE TABLE desaymem_light.trip_events (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    trip_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    source TEXT NOT NULL,
    driver_user_id TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    status TEXT NOT NULL
        CHECK (status IN ('in_progress', 'completed', 'aborted', 'unknown')),
    destination_label TEXT,
    origin_label TEXT,
    context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    version INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, trip_id)
);

CREATE INDEX trip_events_vehicle_time_idx
ON desaymem_light.trip_events(tenant_id, vehicle_id, started_at DESC);

CREATE TRIGGER trip_events_set_updated_at
BEFORE UPDATE ON desaymem_light.trip_events
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();

CREATE TABLE desaymem_light.activity_episodes (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    vehicle_id TEXT NOT NULL,
    trip_id TEXT,
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    boundary_kind TEXT NOT NULL,
    link_reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX activity_episodes_scope_idx
ON desaymem_light.activity_episodes(tenant_id, user_id, vehicle_id, start_at DESC);

CREATE TABLE desaymem_light.episode_operations (
    episode_id UUID NOT NULL
        REFERENCES desaymem_light.activity_episodes(id) ON DELETE CASCADE,
    operation_id UUID NOT NULL
        REFERENCES desaymem_light.activity_operations(id) ON DELETE CASCADE,
    ordinal INT NOT NULL,
    actor_user_id TEXT,
    inclusion_reason TEXT NOT NULL,
    tool_name TEXT,
    PRIMARY KEY (episode_id, operation_id)
);

CREATE TRIGGER activity_episodes_set_updated_at
BEFORE UPDATE ON desaymem_light.activity_episodes
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();

INSERT INTO desaymem_light.json_mirror_registry(table_name, primary_key_columns) VALUES
    ('trip_events', '["id"]'),
    ('activity_episodes', '["id"]'),
    ('episode_operations', '["episode_id","operation_id"]')
ON CONFLICT (table_schema, table_name) DO UPDATE SET
    primary_key_columns = EXCLUDED.primary_key_columns,
    enabled = true,
    schema_version = EXCLUDED.schema_version;

CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE
ON desaymem_light.trip_events
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');

CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE
ON desaymem_light.activity_episodes
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');

CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE
ON desaymem_light.episode_operations
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('episode_id,operation_id');
