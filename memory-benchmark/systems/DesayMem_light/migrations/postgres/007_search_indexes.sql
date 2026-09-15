ALTER TABLE desaymem_light.memory_items
ADD COLUMN search_document TSVECTOR
GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED;

CREATE INDEX session_messages_scope_sequence_idx
ON desaymem_light.session_messages(
    tenant_id, user_id, vehicle_id, occupant_id, session_id, sequence_no
);

CREATE INDEX session_messages_pending_idx
ON desaymem_light.session_messages(buffer_status, ingested_at)
WHERE buffer_status = 'pending';

CREATE INDEX topic_segments_ready_idx
ON desaymem_light.topic_segments(status, created_at)
WHERE status IN ('ready', 'failed');

CREATE INDEX memory_items_scope_time_idx
ON desaymem_light.memory_items(
    tenant_id, user_id, vehicle_id, occupant_id, memory_type, status, occurred_at DESC
);

CREATE INDEX memory_items_content_hash_idx
ON desaymem_light.memory_items(
    tenant_id, user_id, vehicle_id, occupant_id, content_hash
);

CREATE INDEX memory_items_fts_idx
ON desaymem_light.memory_items USING GIN(search_document);

CREATE INDEX memory_items_embedding_hnsw_idx
ON desaymem_light.memory_items USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX memory_entities_embedding_hnsw_idx
ON desaymem_light.memory_entities USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX profile_items_embedding_hnsw_idx
ON desaymem_light.profile_items USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX profile_items_current_scope_idx
ON desaymem_light.profile_items(tenant_id, user_id, vehicle_id, occupant_id, updated_at DESC)
WHERE status = 'active';

CREATE INDEX profile_snapshots_current_idx
ON desaymem_light.profile_snapshots(
    tenant_id, user_id, vehicle_id, occupant_id, version DESC
);

CREATE INDEX memory_relations_target_idx
ON desaymem_light.memory_relations(target_memory_id, relation_type);

CREATE INDEX memory_entity_links_entity_idx
ON desaymem_light.memory_entity_links(entity_id, memory_id);

CREATE INDEX memory_tag_links_tag_idx
ON desaymem_light.memory_tag_links(tag_id, memory_id);

CREATE INDEX profile_item_evidence_event_idx
ON desaymem_light.profile_item_evidence(event_id, profile_item_id);

CREATE INDEX memory_jobs_claim_idx
ON desaymem_light.memory_jobs(status, next_run_at, created_at)
WHERE status IN ('ready', 'retry');

CREATE INDEX memory_audit_entity_idx
ON desaymem_light.memory_audit_events(entity_table, entity_id, created_at DESC);

CREATE INDEX llm_usage_created_idx
ON desaymem_light.llm_usage(created_at DESC, purpose, model);
