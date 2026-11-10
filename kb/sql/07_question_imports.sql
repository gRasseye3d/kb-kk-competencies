-- Stage 3: scoped, editable JSON import drafts; original payload remains immutable.
BEGIN;
ALTER TABLE question_imports ADD COLUMN department_id UUID REFERENCES departments(id) ON DELETE RESTRICT;
-- Pre-existing imports have no trusted department; leave them unscoped/invisible.
ALTER TABLE question_import_items ADD COLUMN edited_payload JSONB;
ALTER TABLE question_import_items ADD CONSTRAINT ck_import_edited_object
    CHECK (edited_payload IS NULL OR jsonb_typeof(edited_payload) = 'object');
CREATE INDEX ix_question_imports_department_created
    ON question_imports(department_id, created_at DESC, id DESC);
-- The base schema already has ix_question_import_items_import(import_id, status).
CREATE INDEX ix_question_import_items_external
    ON question_import_items(import_id, external_id);
COMMIT;
