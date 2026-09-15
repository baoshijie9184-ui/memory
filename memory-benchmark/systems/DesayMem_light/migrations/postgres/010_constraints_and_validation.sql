CREATE OR REPLACE VIEW desaymem_light.json_mirror_registration_issues AS
SELECT
    registry.table_schema,
    registry.table_name,
    registry.enabled,
    CASE
        WHEN tables.table_name IS NULL THEN 'table_missing'
        WHEN triggers.trigger_name IS NULL THEN 'trigger_missing'
        ELSE NULL
    END AS issue
FROM desaymem_light.json_mirror_registry AS registry
LEFT JOIN information_schema.tables AS tables
    ON tables.table_schema = registry.table_schema
   AND tables.table_name = registry.table_name
LEFT JOIN information_schema.triggers AS triggers
    ON triggers.event_object_schema = registry.table_schema
   AND triggers.event_object_table = registry.table_name
   AND triggers.trigger_name = 'json_mirror_outbox'
WHERE registry.enabled
  AND (tables.table_name IS NULL OR triggers.trigger_name IS NULL);

CREATE OR REPLACE FUNCTION desaymem_light.assert_json_mirror_ready()
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    problem_count INTEGER;
BEGIN
    SELECT count(*) INTO problem_count
    FROM desaymem_light.json_mirror_registration_issues;

    IF problem_count > 0 THEN
        RAISE EXCEPTION 'JSON mirror registration has % issue(s)', problem_count;
    END IF;
END;
$$;

SELECT desaymem_light.assert_json_mirror_ready();
