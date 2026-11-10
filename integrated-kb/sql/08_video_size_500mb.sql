BEGIN;

ALTER TABLE video_uploads
    DROP CONSTRAINT video_uploads_byte_size_check;

ALTER TABLE video_uploads
    ADD CONSTRAINT video_uploads_byte_size_check
    CHECK (
        byte_size > 0
        AND byte_size <= 524288000
    );

COMMIT;
