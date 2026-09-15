INSERT INTO desaymem_light.json_mirror_registry(table_name, primary_key_columns) VALUES
    ('session_messages', '["id"]'),
    ('topic_segments', '["id"]'),
    ('topic_segment_messages', '["topic_segment_id", "ordinal"]'),
    ('memory_items', '["id"]'),
    ('memory_evidence', '["id"]'),
    ('memory_relations', '["id"]'),
    ('memory_entities', '["id"]'),
    ('memory_entity_links', '["memory_id", "entity_id"]'),
    ('tag_definitions', '["id"]'),
    ('memory_tag_links', '["memory_id", "tag_id"]'),
    ('profile_items', '["id"]'),
    ('profile_item_evidence', '["profile_item_id", "event_id", "evidence_role"]'),
    ('profile_item_relations', '["id"]'),
    ('profile_snapshots', '["id"]'),
    ('profile_snapshot_items', '["snapshot_id", "ordinal"]'),
    ('memory_jobs', '["id"]'),
    ('cross_event_checkpoints', '["tenant_id", "user_id", "vehicle_id", "occupant_id"]'),
    ('profile_checkpoints', '["tenant_id", "user_id", "vehicle_id", "occupant_id"]'),
    ('memory_audit_events', '["id"]'),
    ('llm_usage', '["id"]')
ON CONFLICT (table_schema, table_name) DO UPDATE SET
    primary_key_columns = EXCLUDED.primary_key_columns,
    enabled = true,
    schema_version = EXCLUDED.schema_version;

CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.session_messages
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.topic_segments
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.topic_segment_messages
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('topic_segment_id', 'ordinal');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.memory_items
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.memory_evidence
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.memory_relations
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.memory_entities
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.memory_entity_links
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('memory_id', 'entity_id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.tag_definitions
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.memory_tag_links
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('memory_id', 'tag_id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.profile_items
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.profile_item_evidence
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('profile_item_id', 'event_id', 'evidence_role');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.profile_item_relations
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.profile_snapshots
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.profile_snapshot_items
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('snapshot_id', 'ordinal');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.memory_jobs
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.cross_event_checkpoints
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('tenant_id', 'user_id', 'vehicle_id', 'occupant_id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.profile_checkpoints
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('tenant_id', 'user_id', 'vehicle_id', 'occupant_id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.memory_audit_events
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.llm_usage
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');
