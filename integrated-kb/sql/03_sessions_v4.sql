-- Additive migration for a fresh docs/schema_conflunce_v4.sql database.
BEGIN;
CREATE TABLE IF NOT EXISTS sessions (
    token CHAR(64) PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token VARCHAR(128) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_sessions_expiry ON sessions(expires_at);
COMMIT;
