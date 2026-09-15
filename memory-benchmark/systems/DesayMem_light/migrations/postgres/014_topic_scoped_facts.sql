-- Facts describe occurrences inside one Topic. Identical actions in different
-- Topics must remain separate evidence for Cross-Event pattern discovery.
DROP INDEX IF EXISTS desaymem_light.memory_items_active_fact_hash_unique;

CREATE UNIQUE INDEX memory_items_topic_fact_hash_unique
ON desaymem_light.memory_items(topic_segment_id, content_hash)
WHERE memory_type = 'fact' AND status = 'active';
