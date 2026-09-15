-- Correct the composite JSON Mirror key without rewriting an applied migration.
DROP TRIGGER IF EXISTS json_mirror_outbox ON desaymem_light.episode_operations;

CREATE TRIGGER json_mirror_outbox AFTER INSERT OR UPDATE OR DELETE
ON desaymem_light.episode_operations
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('episode_id', 'operation_id');
