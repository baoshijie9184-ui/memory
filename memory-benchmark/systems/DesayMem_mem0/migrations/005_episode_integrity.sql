-- Keep at most one open L2 episode for each tenant/user/occupant scope.
-- Existing duplicate active episodes must be reconciled before applying.

CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_active_episode_scope
    ON memory_items (tenant_id, user_id, occupant_id)
    WHERE memory_type = 'episodic_memory'
      AND COALESCE(metadata->>'episode_status', 'active') = 'active';
