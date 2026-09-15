ALTER TABLE desaymem_light.json_outbox
    ADD COLUMN locked_at TIMESTAMPTZ,
    ADD COLUMN locked_by TEXT;

CREATE INDEX json_outbox_claimable_idx
ON desaymem_light.json_outbox(id, locked_at)
WHERE applied_at IS NULL;
