ALTER TABLE desaymem_light.profile_item_evidence
ADD COLUMN id UUID;

UPDATE desaymem_light.profile_item_evidence
SET id = gen_random_uuid()
WHERE id IS NULL;

ALTER TABLE desaymem_light.profile_item_evidence
ALTER COLUMN id SET DEFAULT gen_random_uuid(),
ALTER COLUMN id SET NOT NULL;

ALTER TABLE desaymem_light.profile_item_evidence
DROP CONSTRAINT profile_item_evidence_pkey;

ALTER TABLE desaymem_light.profile_item_evidence
ADD CONSTRAINT profile_item_evidence_pkey PRIMARY KEY (id);

CREATE UNIQUE INDEX profile_item_evidence_provenance_unique
ON desaymem_light.profile_item_evidence(
    profile_item_id, event_id, via_cross_event_id, evidence_role
) NULLS NOT DISTINCT;

-- Remove rows projected with the legacy composite key. These DELETE events
-- precede the new-key UPDATE events below, so the normal mirror worker can
-- migrate the JSON file without an out-of-band filesystem rewrite.
WITH legacy_deletes AS (
    INSERT INTO desaymem_light.json_outbox(
        table_schema, table_name, pk_json, operation, row_data
    )
    SELECT
        'desaymem_light',
        'profile_item_evidence',
        jsonb_build_object(
            'profile_item_id', profile_item_id,
            'event_id', event_id,
            'evidence_role', evidence_role
        ),
        'DELETE',
        to_jsonb(evidence)
    FROM desaymem_light.profile_item_evidence AS evidence
    RETURNING id
)
UPDATE desaymem_light.json_outbox AS outbox
SET row_version = outbox.id
FROM legacy_deletes
WHERE outbox.id = legacy_deletes.id;

UPDATE desaymem_light.json_mirror_registry
SET primary_key_columns = '["id"]'::jsonb,
    schema_version = 1,
    enabled = true
WHERE table_schema = 'desaymem_light'
  AND table_name = 'profile_item_evidence';

DROP TRIGGER IF EXISTS json_mirror_outbox
ON desaymem_light.profile_item_evidence;

CREATE TRIGGER json_mirror_outbox
AFTER INSERT OR UPDATE OR DELETE ON desaymem_light.profile_item_evidence
FOR EACH ROW EXECUTE FUNCTION desaymem_light.capture_json_outbox('id');

-- Re-emit every existing row with its stable UUID key. The old trigger has
-- already been replaced, so these are new-key UPDATE events.
UPDATE desaymem_light.profile_item_evidence
SET id = id;
