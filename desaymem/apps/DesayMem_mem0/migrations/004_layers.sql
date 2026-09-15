-- DesayMem_mem0 migration 004
-- L2 episode pointers stay on memory_items (memory_type=episodic_memory).
-- L3 current beliefs live in a separate structured table — not HNSW-primary.
-- Apply after 003_bm25.sql. The API never creates these tables on request.

CREATE TABLE IF NOT EXISTS profile_beliefs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    occupant_id TEXT NOT NULL DEFAULT 'primary',
    subject TEXT NOT NULL DEFAULT 'User',
    attribute TEXT NOT NULL,
    value TEXT NOT NULL,
    conditions JSONB NOT NULL DEFAULT '{}'::jsonb,
    stability TEXT NOT NULL DEFAULT 'episode',
    status TEXT NOT NULL DEFAULT 'active',
    confidence REAL NOT NULL DEFAULT 0.5,
    support_count INTEGER NOT NULL DEFAULT 1,
    evidence_memory_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_episode_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    attribute_embedding VECTOR(1024) NOT NULL,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_belief_stability CHECK (stability IN ('episode', 'recurring', 'identity')),
    CONSTRAINT chk_belief_status CHECK (status IN ('active', 'superseded')),
    CONSTRAINT chk_belief_support CHECK (support_count >= 1),
    CONSTRAINT chk_belief_embedding_dims CHECK (TRUE)
);

CREATE INDEX IF NOT EXISTS idx_beliefs_tenant_user
    ON profile_beliefs (tenant_id, user_id);

CREATE INDEX IF NOT EXISTS idx_beliefs_tenant_user_status
    ON profile_beliefs (tenant_id, user_id, status);

CREATE INDEX IF NOT EXISTS idx_beliefs_tenant_user_occupant
    ON profile_beliefs (tenant_id, user_id, occupant_id);

CREATE INDEX IF NOT EXISTS idx_beliefs_attribute_hnsw
    ON profile_beliefs USING hnsw (attribute_embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS user_profile_snapshots (
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    narrative TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, user_id)
);
