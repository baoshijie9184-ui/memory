CREATE TABLE desaymem_light.session_messages (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    occupant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    sequence_no BIGINT NOT NULL CHECK (sequence_no >= 0),
    request_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL CHECK (length(content) > 0),
    content_tokens INTEGER NOT NULL CHECK (content_tokens > 0),
    occurred_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    buffer_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (buffer_status IN ('pending', 'segmented', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, request_id),
    UNIQUE (tenant_id, user_id, vehicle_id, occupant_id, session_id, sequence_no)
);

CREATE TABLE desaymem_light.topic_segments (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    occupant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    start_sequence_no BIGINT NOT NULL CHECK (start_sequence_no >= 0),
    end_sequence_no BIGINT NOT NULL CHECK (end_sequence_no >= start_sequence_no),
    content TEXT NOT NULL CHECK (length(content) > 0),
    token_count INTEGER NOT NULL CHECK (token_count > 0),
    boundary_reason TEXT NOT NULL
        CHECK (boundary_reason IN ('semantic', 'token_limit', 'session_end', 'manual')),
    boundary_score REAL,
    status TEXT NOT NULL DEFAULT 'ready'
        CHECK (status IN ('ready', 'processing', 'completed', 'failed')),
    event_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (event_status IN ('pending', 'completed', 'skipped_no_fact', 'failed')),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    extract_model TEXT,
    prompt_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE desaymem_light.topic_segment_messages (
    topic_segment_id UUID NOT NULL
        REFERENCES desaymem_light.topic_segments(id) ON DELETE CASCADE,
    message_id UUID NOT NULL UNIQUE
        REFERENCES desaymem_light.session_messages(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (topic_segment_id, ordinal)
);

CREATE TRIGGER session_messages_set_updated_at
BEFORE UPDATE ON desaymem_light.session_messages
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();

CREATE TRIGGER topic_segments_set_updated_at
BEFORE UPDATE ON desaymem_light.topic_segments
FOR EACH ROW EXECUTE FUNCTION desaymem_light.set_updated_at();
