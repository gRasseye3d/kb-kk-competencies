-- Follow-up to 04_testing.sql: an active reservation has no known release time.
BEGIN;
DO $$
DECLARE c record;
BEGIN
  FOR c IN SELECT conname, pg_get_constraintdef(oid) AS definition
           FROM pg_constraint WHERE conrelid='attempt_competencies'::regclass AND contype='c'
  LOOP
    IF (c.definition LIKE '%exclusion_reason%' AND c.definition LIKE '%available_at%')
       OR (c.definition LIKE '%exclusion_reason%' AND c.definition LIKE '%cooldown_30d%'
           AND c.definition NOT LIKE '%available_at%') THEN
      EXECUTE format('ALTER TABLE attempt_competencies DROP CONSTRAINT %I', c.conname);
    END IF;
  END LOOP;
END $$;
-- Recover exclusions produced by 04 for concurrent active attempts, when
-- there was no real cooldown at the time of the excluded attempt's start.
UPDATE attempt_competencies ac SET exclusion_reason='reserved_active', available_at=NULL
FROM test_attempts a JOIN test_assignments ta ON ta.id=a.assignment_id
WHERE ac.attempt_id=a.id AND ac.state='excluded' AND ac.exclusion_reason='cooldown_30d'
  AND NOT EXISTS (SELECT 1 FROM assessment_cooldowns cd
       WHERE cd.user_id=ta.user_id AND cd.competency_id=ac.competency_id
         AND cd.last_completed_at<=a.started_at AND cd.available_at>a.started_at)
  AND EXISTS (SELECT 1 FROM test_competency_reservations r
       WHERE r.user_id=ta.user_id AND r.competency_id=ac.competency_id
         AND r.attempt_id<>a.id AND r.reserved_at<=a.started_at
         AND (r.released_at IS NULL OR r.released_at>=a.started_at));
ALTER TABLE attempt_competencies ADD CONSTRAINT ck_attempt_competency_exclusion_v2 CHECK (
  (state='included' AND exclusion_reason IS NULL AND available_at IS NULL)
  OR (state='excluded' AND assessed_level IS NULL AND correct_count IS NULL AND
    ((exclusion_reason='cooldown_30d' AND available_at IS NOT NULL)
      OR (exclusion_reason='reserved_active' AND available_at IS NULL)))
);
COMMIT;
