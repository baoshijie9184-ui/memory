-- L0 short-term memory: per-user rolling window of embedded messages,
-- searchable immediately after ingest (vehicle cockpit scenario).
CREATE TABLE desaymem_light.short_term_memories (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT,
    occupant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    message_id UUID NOT NULL
        REFERENCES desaymem_light.session_messages(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL CHECK (length(content) > 0),
    embedding VECTOR(1024) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX short_term_memories_scope_idx
ON desaymem_light.short_term_memories(
    tenant_id, user_id, occupant_id, created_at DESC
);

CREATE INDEX short_term_memories_hnsw_idx
ON desaymem_light.short_term_memories USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE TRIGGER short_term_memories_set_updated_at
BEFORE UPDATE ON desaymem_light.short_term_memories
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();

-- JSON mirror registration (frontend left panel observes writes in real time)
INSERT INTO desaymem_light.json_mirror_registry(table_name, primary_key_columns) VALUES
    ('short_term_memories', '["id"]')
ON CONFLICT (table_schema, table_name) DO UPDATE SET
    primary_key_columns = EXCLUDED.primary_key_columns,
    enabled = true,
    schema_version = EXCLUDED.schema_version;

CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE
ON desaymem_light.short_term_memories
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
