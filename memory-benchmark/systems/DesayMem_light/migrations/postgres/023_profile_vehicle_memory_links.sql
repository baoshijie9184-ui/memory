-- P2: bridge vehicle-memory distillates (preferences/skills) to the
-- conversation-track user profile (profile_items). Profile items are produced
-- by the conversation pipeline only (they require embeddings and
-- memory_items evidence), so vehicle-track distillates link to them instead
-- of creating profile rows directly.

CREATE TABLE desaymem_light.profile_vehicle_memory_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT,
    profile_item_id UUID NOT NULL
        REFERENCES desaymem_light.profile_items(id) ON DELETE CASCADE,
    preference_id UUID
        REFERENCES desaymem_light.conditional_preferences(id) ON DELETE CASCADE,
    skill_id UUID
        REFERENCES desaymem_light.skill_items(id) ON DELETE CASCADE,
    link_kind TEXT NOT NULL
        CHECK (link_kind IN ('supports', 'contradicts', 'derived_from')),
    attribute TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT profile_vehicle_link_one_target CHECK (
        ((preference_id IS NOT NULL)::int + (skill_id IS NOT NULL)::int) = 1
    ),
    UNIQUE (profile_item_id, preference_id),
    UNIQUE (profile_item_id, skill_id)
);

CREATE INDEX profile_vehicle_links_lookup_idx
ON desaymem_light.profile_vehicle_memory_links(tenant_id, user_id, attribute);

CREATE INDEX profile_vehicle_links_profile_idx
ON desaymem_light.profile_vehicle_memory_links(profile_item_id);

INSERT INTO desaymem_light.json_mirror_registry(table_name, primary_key_columns) VALUES
    ('profile_vehicle_memory_links', '["id"]')
ON CONFLICT (table_schema, table_name) DO UPDATE SET
    primary_key_columns = EXCLUDED.primary_key_columns,
    enabled = true,
    schema_version = EXCLUDED.schema_version;

CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE
ON desaymem_light.profile_vehicle_memory_links
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
