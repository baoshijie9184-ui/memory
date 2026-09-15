-- P2: conditional preferences and skill candidates.
CREATE TABLE desaymem_light.conditional_preferences (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    vehicle_id TEXT,
    family_id TEXT NOT NULL,
    version INT NOT NULL DEFAULT 1,
    attribute TEXT NOT NULL,
    value_json JSONB NOT NULL,
    conditions_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_kind TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('observed', 'candidate', 'active', 'superseded')),
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, family_id, version)
);

CREATE INDEX conditional_preferences_lookup_idx
ON desaymem_light.conditional_preferences(tenant_id, user_id, attribute, status);

CREATE TABLE desaymem_light.conditional_preference_evidence (
    id UUID PRIMARY KEY,
    preference_id UUID NOT NULL
        REFERENCES desaymem_light.conditional_preferences(id) ON DELETE CASCADE,
    operation_id UUID
        REFERENCES desaymem_light.activity_operations(id) ON DELETE CASCADE,
    message_id UUID,
    fact_id UUID,
    evidence_role TEXT NOT NULL DEFAULT 'support',
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT conditional_preference_evidence_one_source CHECK (
        ((operation_id IS NOT NULL)::int + (message_id IS NOT NULL)::int + (fact_id IS NOT NULL)::int) = 1
    )
);

CREATE TABLE desaymem_light.skill_items (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,
    vehicle_id TEXT,
    family_id TEXT NOT NULL,
    version INT NOT NULL DEFAULT 1,
    intent TEXT NOT NULL,
    summary TEXT NOT NULL,
    steps_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    conditions_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'published', 'suspended')),
    support_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, family_id, version)
);

CREATE INDEX skill_items_lookup_idx
ON desaymem_light.skill_items(tenant_id, user_id, status, intent);

CREATE TABLE desaymem_light.skill_evidence (
    id UUID PRIMARY KEY,
    skill_id UUID NOT NULL
        REFERENCES desaymem_light.skill_items(id) ON DELETE CASCADE,
    episode_id UUID
        REFERENCES desaymem_light.activity_episodes(id) ON DELETE CASCADE,
    operation_id UUID
        REFERENCES desaymem_light.activity_operations(id) ON DELETE CASCADE,
    evidence_role TEXT NOT NULL DEFAULT 'support',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT skill_evidence_one_source CHECK (
        ((episode_id IS NOT NULL)::int + (operation_id IS NOT NULL)::int) = 1
    )
);

CREATE TRIGGER conditional_preferences_set_updated_at
BEFORE UPDATE ON desaymem_light.conditional_preferences
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();

CREATE TRIGGER skill_items_set_updated_at
BEFORE UPDATE ON desaymem_light.skill_items
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();

INSERT INTO desaymem_light.json_mirror_registry(table_name, primary_key_columns) VALUES
    ('conditional_preferences', '["id"]'),
    ('conditional_preference_evidence', '["id"]'),
    ('skill_items', '["id"]'),
    ('skill_evidence', '["id"]')
ON CONFLICT (table_schema, table_name) DO UPDATE SET
    primary_key_columns = EXCLUDED.primary_key_columns,
    enabled = true,
    schema_version = EXCLUDED.schema_version;

CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE
ON desaymem_light.conditional_preferences
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');

CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE
ON desaymem_light.conditional_preference_evidence
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');

CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE
ON desaymem_light.skill_items
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');

CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE
ON desaymem_light.skill_evidence
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
