-- DesayMem_mem0 migration 002
-- Align with Mem0 OSS add pipeline pieces that v1 originally skipped:
--   * memory_type on memory_items (procedural_memory vs semantic_memory)
--   * session_messages: last-k conversation history per tenant+user+session
--   * memory_entities: entity store with linked_memory_ids
-- Apply after 001_initial.sql. The API never creates these tables on request.

ALTER TABLE memory_items
    ADD COLUMN IF NOT EXISTS memory_type TEXT NOT NULL DEFAULT 'semantic_memory';

CREATE INDEX IF NOT EXISTS idx_memory_tenant_user_type
    ON memory_items (tenant_id, user_id, memory_type);

CREATE TABLE IF NOT EXISTS session_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_scope TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_messages_scope_created
    ON session_messages (session_scope, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_session_messages_tenant_user
    ON session_messages (tenant_id, user_id);

CREATE TABLE IF NOT EXISTS memory_entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    entity_text TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    embedding VECTOR(1024) NOT NULL,
    linked_memory_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_entity_tenant_user_norm UNIQUE (tenant_id, user_id, normalized_text)
);

CREATE INDEX IF NOT EXISTS idx_entity_tenant_user
    ON memory_entities (tenant_id, user_id);

CREATE INDEX IF NOT EXISTS idx_entity_embedding_hnsw
    ON memory_entities USING hnsw (embedding vector_cosine_ops);
