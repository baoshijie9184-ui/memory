-- A source message has at most one L0 projection.  Keep the newest projection
-- if an older deployment produced duplicates before adding the constraint.
DELETE FROM desaymem_light.short_term_memories AS older
USING desaymem_light.short_term_memories AS newer
WHERE older.message_id = newer.message_id
  AND (older.created_at, older.id) < (newer.created_at, newer.id);

ALTER TABLE desaymem_light.short_term_memories
ADD CONSTRAINT short_term_memories_message_id_unique UNIQUE (message_id);
