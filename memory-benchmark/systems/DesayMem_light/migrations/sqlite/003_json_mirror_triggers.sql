CREATE TRIGGER history_json_mirror_insert
AFTER INSERT ON history
BEGIN
    INSERT INTO json_outbox(table_name, pk_json, operation, row_data, created_at)
    VALUES (
        'history',
        json_object('id', NEW.id),
        'INSERT',
        json_object(
            'id', NEW.id, 'tenant_id', NEW.tenant_id, 'user_id', NEW.user_id,
            'vehicle_id', NEW.vehicle_id, 'occupant_id', NEW.occupant_id,
            'session_id', NEW.session_id, 'memory_id', NEW.memory_id,
            'old_memory', NEW.old_memory, 'new_memory', NEW.new_memory,
            'event', NEW.event, 'source_type', NEW.source_type, 'source_id', NEW.source_id,
            'created_at', NEW.created_at, 'updated_at', NEW.updated_at,
            'is_deleted', NEW.is_deleted, 'actor_id', NEW.actor_id, 'role', NEW.role
        ),
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    );
    UPDATE json_outbox SET row_version = id WHERE id = last_insert_rowid();
END;

CREATE TRIGGER history_json_mirror_update
AFTER UPDATE ON history
BEGIN
    INSERT INTO json_outbox(table_name, pk_json, operation, row_data, created_at)
    VALUES (
        'history',
        json_object('id', NEW.id),
        'UPDATE',
        json_object(
            'id', NEW.id, 'tenant_id', NEW.tenant_id, 'user_id', NEW.user_id,
            'vehicle_id', NEW.vehicle_id, 'occupant_id', NEW.occupant_id,
            'session_id', NEW.session_id, 'memory_id', NEW.memory_id,
            'old_memory', NEW.old_memory, 'new_memory', NEW.new_memory,
            'event', NEW.event, 'source_type', NEW.source_type, 'source_id', NEW.source_id,
            'created_at', NEW.created_at, 'updated_at', NEW.updated_at,
            'is_deleted', NEW.is_deleted, 'actor_id', NEW.actor_id, 'role', NEW.role
        ),
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    );
    UPDATE json_outbox SET row_version = id WHERE id = last_insert_rowid();
END;

CREATE TRIGGER history_json_mirror_delete
AFTER DELETE ON history
BEGIN
    INSERT INTO json_outbox(table_name, pk_json, operation, row_data, created_at)
    VALUES (
        'history',
        json_object('id', OLD.id),
        'DELETE',
        json_object(
            'id', OLD.id, 'tenant_id', OLD.tenant_id, 'user_id', OLD.user_id,
            'vehicle_id', OLD.vehicle_id, 'occupant_id', OLD.occupant_id,
            'session_id', OLD.session_id, 'memory_id', OLD.memory_id,
            'old_memory', OLD.old_memory, 'new_memory', OLD.new_memory,
            'event', OLD.event, 'source_type', OLD.source_type, 'source_id', OLD.source_id,
            'created_at', OLD.created_at, 'updated_at', OLD.updated_at,
            'is_deleted', OLD.is_deleted, 'actor_id', OLD.actor_id, 'role', OLD.role
        ),
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    );
    UPDATE json_outbox SET row_version = id WHERE id = last_insert_rowid();
END;

CREATE TRIGGER messages_cache_json_mirror_insert
AFTER INSERT ON messages_cache
BEGIN
    INSERT INTO json_outbox(table_name, pk_json, operation, row_data, created_at)
    VALUES (
        'messages_cache',
        json_object('id', NEW.id),
        'INSERT',
        json_object(
            'id', NEW.id, 'tenant_id', NEW.tenant_id, 'user_id', NEW.user_id,
            'vehicle_id', NEW.vehicle_id, 'occupant_id', NEW.occupant_id,
            'session_id', NEW.session_id, 'sequence_no', NEW.sequence_no,
            'role', NEW.role, 'content', NEW.content, 'name', NEW.name,
            'occurred_at', NEW.occurred_at, 'cached_at', NEW.cached_at,
            'expires_at', NEW.expires_at
        ),
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    );
    UPDATE json_outbox SET row_version = id WHERE id = last_insert_rowid();
END;

CREATE TRIGGER messages_cache_json_mirror_update
AFTER UPDATE ON messages_cache
BEGIN
    INSERT INTO json_outbox(table_name, pk_json, operation, row_data, created_at)
    VALUES (
        'messages_cache',
        json_object('id', NEW.id),
        'UPDATE',
        json_object(
            'id', NEW.id, 'tenant_id', NEW.tenant_id, 'user_id', NEW.user_id,
            'vehicle_id', NEW.vehicle_id, 'occupant_id', NEW.occupant_id,
            'session_id', NEW.session_id, 'sequence_no', NEW.sequence_no,
            'role', NEW.role, 'content', NEW.content, 'name', NEW.name,
            'occurred_at', NEW.occurred_at, 'cached_at', NEW.cached_at,
            'expires_at', NEW.expires_at
        ),
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    );
    UPDATE json_outbox SET row_version = id WHERE id = last_insert_rowid();
END;

CREATE TRIGGER messages_cache_json_mirror_delete
AFTER DELETE ON messages_cache
BEGIN
    INSERT INTO json_outbox(table_name, pk_json, operation, row_data, created_at)
    VALUES (
        'messages_cache',
        json_object('id', OLD.id),
        'DELETE',
        json_object(
            'id', OLD.id, 'tenant_id', OLD.tenant_id, 'user_id', OLD.user_id,
            'vehicle_id', OLD.vehicle_id, 'occupant_id', OLD.occupant_id,
            'session_id', OLD.session_id, 'sequence_no', OLD.sequence_no,
            'role', OLD.role, 'content', OLD.content, 'name', OLD.name,
            'occurred_at', OLD.occurred_at, 'cached_at', OLD.cached_at,
            'expires_at', OLD.expires_at
        ),
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    );
    UPDATE json_outbox SET row_version = id WHERE id = last_insert_rowid();
END;
