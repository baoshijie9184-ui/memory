CREATE TABLE desaymem_light.json_mirror_registry (
    table_schema TEXT NOT NULL DEFAULT 'desaymem_light',
    table_name TEXT NOT NULL,
    primary_key_columns JSONB NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (table_schema, table_name),
    CHECK (jsonb_typeof(primary_key_columns) = 'array')
);

CREATE TABLE desaymem_light.json_outbox (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    database_name TEXT NOT NULL DEFAULT 'postgres' CHECK (database_name = 'postgres'),
    table_schema TEXT NOT NULL,
    table_name TEXT NOT NULL,
    pk_json JSONB NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
    row_data JSONB NOT NULL,
    row_version BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error TEXT
);

CREATE TABLE desaymem_light.json_mirror_checkpoint (
    database_name TEXT NOT NULL,
    table_schema TEXT NOT NULL,
    table_name TEXT NOT NULL,
    last_outbox_id BIGINT NOT NULL DEFAULT 0,
    last_reconciled_at TIMESTAMPTZ,
    last_mismatch_count INTEGER NOT NULL DEFAULT 0 CHECK (last_mismatch_count >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (database_name, table_schema, table_name)
);

CREATE INDEX json_outbox_pending_idx
ON desaymem_light.json_outbox(id)
WHERE applied_at IS NULL;

CREATE OR REPLACE FUNCTION desaymem_light.capture_json_outbox()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    row_json JSONB;
    key_name TEXT;
    key_json JSONB := '{}'::jsonb;
    event_id BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        row_json := to_jsonb(OLD);
    ELSE
        row_json := to_jsonb(NEW);
    END IF;

    FOREACH key_name IN ARRAY TG_ARGV LOOP
        IF NOT row_json ? key_name THEN
            RAISE EXCEPTION 'JSON mirror key % missing from %.%', key_name, TG_TABLE_SCHEMA, TG_TABLE_NAME;
        END IF;
        key_json := key_json || jsonb_build_object(key_name, row_json -> key_name);
    END LOOP;

    INSERT INTO desaymem_light.json_outbox(
        table_schema, table_name, pk_json, operation, row_data
    ) VALUES (
        TG_TABLE_SCHEMA, TG_TABLE_NAME, key_json, TG_OP, row_json
    ) RETURNING id INTO event_id;

    UPDATE desaymem_light.json_outbox
    SET row_version = event_id
    WHERE id = event_id;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER json_mirror_registry_set_updated_at
BEFORE UPDATE ON desaymem_light.json_mirror_registry
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();

CREATE TRIGGER json_mirror_checkpoint_set_updated_at
BEFORE UPDATE ON desaymem_light.json_mirror_checkpoint
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();
