BEGIN;

CREATE TABLE IF NOT EXISTS video_uploads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    uploaded_by UUID NOT NULL
        REFERENCES users(id) ON DELETE RESTRICT,

    page_id UUID
        REFERENCES pages(id) ON DELETE SET NULL,

    filename TEXT NOT NULL,

    storage_name TEXT NOT NULL UNIQUE,

    media_type VARCHAR(32) NOT NULL
        CHECK (media_type IN ('video/mp4', 'video/webm')),

    byte_size BIGINT NOT NULL
        CHECK (byte_size > 0 AND byte_size <= 209715200),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT video_storage_name_format
        CHECK (
            storage_name ~
            '^[0-9a-f]{32}\.(mp4|webm)$'
        )
);

CREATE INDEX IF NOT EXISTS ix_video_uploads_page
    ON video_uploads(page_id);

CREATE INDEX IF NOT EXISTS ix_video_uploads_owner
    ON video_uploads(uploaded_by);

COMMIT;
