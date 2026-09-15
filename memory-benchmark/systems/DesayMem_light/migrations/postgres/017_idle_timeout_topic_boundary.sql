ALTER TABLE desaymem_light.topic_segments
DROP CONSTRAINT IF EXISTS topic_segments_boundary_reason_check;

ALTER TABLE desaymem_light.topic_segments
ADD CONSTRAINT topic_segments_boundary_reason_check
CHECK (
    boundary_reason IN (
        'semantic',
        'token_limit',
        'message_limit',
        'session_end',
        'manual',
        'idle_timeout'
    )
);
