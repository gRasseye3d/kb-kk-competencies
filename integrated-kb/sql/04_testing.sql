BEGIN;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES users(id) ON DELETE RESTRICT;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS bank_department_id UUID REFERENCES departments(id) ON DELETE RESTRICT;
CREATE TABLE IF NOT EXISTS test_competency_reservations (
 user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 competency_id UUID NOT NULL REFERENCES competencies(id) ON DELETE RESTRICT,
 attempt_id UUID NOT NULL REFERENCES test_attempts(id) ON DELETE RESTRICT,
 reserved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 released_at TIMESTAMPTZ,
 PRIMARY KEY (attempt_id, competency_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_test_active_reservation
 ON test_competency_reservations(user_id,competency_id) WHERE released_at IS NULL;
COMMIT;
