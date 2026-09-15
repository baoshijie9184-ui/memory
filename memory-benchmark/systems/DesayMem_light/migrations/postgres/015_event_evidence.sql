ALTER TABLE desaymem_light.memory_evidence
DROP CONSTRAINT memory_evidence_source_type_check;

ALTER TABLE desaymem_light.memory_evidence
ADD CONSTRAINT memory_evidence_source_type_check
CHECK (source_type IN ('message', 'topic', 'event'));
