CREATE INDEX history_scope_memory_idx
ON history(tenant_id, user_id, vehicle_id, occupant_id, memory_id, created_at DESC);

CREATE INDEX messages_cache_scope_sequence_idx
ON messages_cache(tenant_id, user_id, vehicle_id, occupant_id, session_id, sequence_no DESC);

CREATE INDEX messages_cache_expiry_idx
ON messages_cache(expires_at)
WHERE expires_at IS NOT NULL;

CREATE INDEX json_outbox_pending_idx
ON json_outbox(id)
WHERE applied_at IS NULL;
