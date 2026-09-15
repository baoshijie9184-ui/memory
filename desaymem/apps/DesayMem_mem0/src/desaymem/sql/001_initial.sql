-- DesayMem_mem0 initial schema
-- PostgreSQL 16 + pgvector
-- Default embedding dimension is 1024 (BGE-M3). If EMBEDDING_DIMS differs,
-- update the VECTOR(...) type below and re-apply on a fresh database.
-- The API never creates or alters this schema on first request.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS memory_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL DEFAULT '',
    occupant_id TEXT NOT NULL DEFAULT 'primary',
    session_id TEXT NOT NULL DEFAULT '',
    scene TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'conversation',
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding VECTOR(1024) NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_dims INTEGER NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_memory_tenant_user_hash UNIQUE (tenant_id, user_id, content_hash),
    CONSTRAINT chk_memory_embedding_dims CHECK (embedding_dims = 1024)
);

CREATE INDEX IF NOT EXISTS idx_memory_tenant_user
    ON memory_items (tenant_id, user_id);

CREATE INDEX IF NOT EXISTS idx_memory_tenant_user_created
    ON memory_items (tenant_id, user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_vehicle
    ON memory_items (vehicle_id);

CREATE INDEX IF NOT EXISTS idx_memory_session
    ON memory_items (session_id);

CREATE INDEX IF NOT EXISTS idx_memory_scene
    ON memory_items (scene);

CREATE INDEX IF NOT EXISTS idx_memory_source
    ON memory_items (source);

CREATE INDEX IF NOT EXISTS idx_memory_metadata
    ON memory_items USING gin (metadata);

CREATE INDEX IF NOT EXISTS idx_memory_embedding_hnsw
    ON memory_items USING hnsw (embedding vector_cosine_ops);
