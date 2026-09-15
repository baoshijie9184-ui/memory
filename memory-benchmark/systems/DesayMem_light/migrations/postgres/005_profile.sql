CREATE TABLE desaymem_light.profile_items (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    occupant_id TEXT NOT NULL,
    attribute TEXT NOT NULL CHECK (length(attribute) > 0),
    value TEXT NOT NULL CHECK (length(value) > 0),
    normalized_hash TEXT NOT NULL,
    embedding VECTOR(1024) NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded', 'deleted')),
    first_observed_at TIMESTAMPTZ NOT NULL,
    last_confirmed_at TIMESTAMPTZ NOT NULL,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    confirmation_count INTEGER NOT NULL DEFAULT 1 CHECK (confirmation_count >= 1),
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE UNIQUE INDEX profile_items_active_value_unique
ON desaymem_light.profile_items(
    tenant_id, user_id, vehicle_id, occupant_id, normalized_hash
)
WHERE status = 'active';

CREATE TABLE desaymem_light.profile_item_evidence (
    profile_item_id UUID NOT NULL
        REFERENCES desaymem_light.profile_items(id) ON DELETE CASCADE,
    event_id UUID NOT NULL
        REFERENCES desaymem_light.memory_items(id) ON DELETE RESTRICT,
    via_cross_event_id UUID
        REFERENCES desaymem_light.memory_items(id) ON DELETE SET NULL,
    evidence_role TEXT NOT NULL CHECK (evidence_role IN ('support', 'contradict')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_item_id, event_id, evidence_role)
);

CREATE TABLE desaymem_light.profile_item_relations (
    id UUID PRIMARY KEY,
    source_profile_item_id UUID NOT NULL
        REFERENCES desaymem_light.profile_items(id) ON DELETE CASCADE,
    target_profile_item_id UUID NOT NULL
        REFERENCES desaymem_light.profile_items(id) ON DELETE RESTRICT,
    relation_type TEXT NOT NULL CHECK (relation_type IN ('supersedes', 'coexists_with')),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (source_profile_item_id <> target_profile_item_id),
    UNIQUE (source_profile_item_id, target_profile_item_id, relation_type)
);

CREATE TABLE desaymem_light.profile_snapshots (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    occupant_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    summary TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id, vehicle_id, occupant_id, version)
);

CREATE TABLE desaymem_light.profile_snapshot_items (
    snapshot_id UUID NOT NULL
        REFERENCES desaymem_light.profile_snapshots(id) ON DELETE CASCADE,
    profile_item_id UUID NOT NULL
        REFERENCES desaymem_light.profile_items(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (snapshot_id, ordinal),
    UNIQUE (snapshot_id, profile_item_id)
);

CREATE TRIGGER profile_items_set_updated_at
BEFORE UPDATE ON desaymem_light.profile_items
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();
