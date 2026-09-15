CREATE TABLE desaymem_light.memory_items (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT,
    occupant_id TEXT NOT NULL,
    session_id TEXT,
    topic_segment_id UUID
        REFERENCES desaymem_light.topic_segments(id) ON DELETE RESTRICT,
    memory_type TEXT NOT NULL CHECK (memory_type IN ('fact', 'event', 'cross_event')),
    content TEXT NOT NULL CHECK (length(content) > 0),
    content_hash TEXT NOT NULL,
    embedding VECTOR(1024) NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded', 'stale', 'deleted')),
    occurred_at TIMESTAMPTZ,
    occurred_end TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    model TEXT,
    prompt_version TEXT,
    derivation_key TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (occurred_end IS NULL OR occurred_at IS NULL OR occurred_end >= occurred_at),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE UNIQUE INDEX memory_items_one_event_per_topic
ON desaymem_light.memory_items(topic_segment_id)
WHERE memory_type = 'event';

CREATE UNIQUE INDEX memory_items_derivation_key_unique
ON desaymem_light.memory_items(derivation_key)
WHERE derivation_key IS NOT NULL;

CREATE UNIQUE INDEX memory_items_active_fact_hash_unique
ON desaymem_light.memory_items(
    tenant_id, user_id, vehicle_id, occupant_id, content_hash
) NULLS NOT DISTINCT
WHERE memory_type = 'fact' AND status = 'active';

CREATE TABLE desaymem_light.memory_evidence (
    id UUID PRIMARY KEY,
    memory_id UUID NOT NULL
        REFERENCES desaymem_light.memory_items(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL CHECK (source_type IN ('message', 'topic')),
    source_id UUID NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (memory_id, source_type, source_id)
);

CREATE TABLE desaymem_light.memory_relations (
    id UUID PRIMARY KEY,
    source_memory_id UUID NOT NULL
        REFERENCES desaymem_light.memory_items(id) ON DELETE CASCADE,
    target_memory_id UUID NOT NULL
        REFERENCES desaymem_light.memory_items(id) ON DELETE RESTRICT,
    relation_type TEXT NOT NULL
        CHECK (relation_type IN ('contains', 'summarizes', 'related_to', 'supersedes')),
    reason TEXT,
    ordinal INTEGER CHECK (ordinal IS NULL OR ordinal >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (source_memory_id <> target_memory_id),
    UNIQUE (source_memory_id, target_memory_id, relation_type)
);

CREATE TRIGGER memory_items_set_updated_at
BEFORE UPDATE ON desaymem_light.memory_items
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();
