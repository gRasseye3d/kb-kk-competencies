-- Additive migration; apply before enabling login throttling.
BEGIN;
CREATE TABLE IF NOT EXISTS login_rate_limits (
    scope TEXT NOT NULL CHECK (scope IN ('account', 'ip')),
    key_hash CHAR(64) NOT NULL,
    window_started TIMESTAMPTZ NOT NULL,
    attempts INTEGER NOT NULL CHECK (attempts > 0),
    PRIMARY KEY (scope, key_hash)
);
CREATE INDEX IF NOT EXISTS ix_login_rate_limits_started ON login_rate_limits(window_started);
COMMIT;
