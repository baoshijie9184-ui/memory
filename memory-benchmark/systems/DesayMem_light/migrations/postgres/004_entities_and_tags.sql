CREATE TABLE desaymem_light.memory_entities (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    occupant_id TEXT NOT NULL,
    normalized_text TEXT NOT NULL CHECK (length(normalized_text) > 0),
    display_text TEXT NOT NULL CHECK (length(display_text) > 0),
    entity_type TEXT NOT NULL DEFAULT 'unknown',
    embedding VECTOR(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id, vehicle_id, occupant_id, normalized_text)
);

CREATE TABLE desaymem_light.memory_entity_links (
    memory_id UUID NOT NULL
        REFERENCES desaymem_light.memory_items(id) ON DELETE CASCADE,
    entity_id UUID NOT NULL
        REFERENCES desaymem_light.memory_entities(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (memory_id, entity_id)
);

CREATE TABLE desaymem_light.tag_definitions (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    normalized_name TEXT NOT NULL CHECK (length(normalized_name) > 0),
    display_name TEXT NOT NULL CHECK (length(display_name) > 0),
    source_type TEXT NOT NULL CHECK (source_type IN ('metadata', 'entity', 'user')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, normalized_name)
);

CREATE TABLE desaymem_light.memory_tag_links (
    memory_id UUID NOT NULL
        REFERENCES desaymem_light.memory_items(id) ON DELETE CASCADE,
    tag_id UUID NOT NULL
        REFERENCES desaymem_light.tag_definitions(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (memory_id, tag_id)
);

CREATE TRIGGER memory_entities_set_updated_at
BEFORE UPDATE ON desaymem_light.memory_entities
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();

CREATE TRIGGER tag_definitions_set_updated_at
BEFORE UPDATE ON desaymem_light.tag_definitions
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();
