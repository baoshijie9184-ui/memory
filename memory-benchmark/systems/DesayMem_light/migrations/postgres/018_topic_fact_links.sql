CREATE TABLE desaymem_light.topic_fact_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic_segment_id UUID NOT NULL
        REFERENCES desaymem_light.topic_segments(id) ON DELETE CASCADE,
    fact_id UUID NOT NULL
        REFERENCES desaymem_light.memory_items(id) ON DELETE RESTRICT,
    operation TEXT NOT NULL
        CHECK (operation IN ('ADD', 'CONFIRM', 'COEXIST', 'SUPERSEDE')),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (topic_segment_id, fact_id),
    UNIQUE (topic_segment_id, ordinal)
);

-- Existing facts were created by their own Topic under the previous ADD-only flow.
INSERT INTO desaymem_light.topic_fact_links(
    topic_segment_id, fact_id, operation, ordinal
)
SELECT
    memory.topic_segment_id,
    memory.id,
    'ADD',
    row_number() OVER (
        PARTITION BY memory.topic_segment_id
        ORDER BY memory.occurred_at NULLS LAST, memory.created_at, memory.id
    ) - 1
FROM desaymem_light.memory_items AS memory
WHERE memory.memory_type = 'fact'
  AND memory.topic_segment_id IS NOT NULL
ON CONFLICT (topic_segment_id, fact_id) DO NOTHING;

INSERT INTO desaymem_light.json_mirror_registry(
    table_name, primary_key_columns
) VALUES (
    'topic_fact_links', '["id"]'
)
ON CONFLICT (table_schema, table_name) DO UPDATE SET
    primary_key_columns = EXCLUDED.primary_key_columns,
    enabled = true,
    schema_version = EXCLUDED.schema_version;

CREATE TRIGGER json_mirror_outbox
AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.topic_fact_links
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
