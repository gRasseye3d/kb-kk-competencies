-- Conflunce: схема БД для MVP корпоративной вики + модуля компетенций.
-- Соответствует Conflunce_TechSpec_v4_Competencies_MVP.md. PostgreSQL 15+.
-- Только для НОВОЙ базы данных: это НЕ миграция существующей БД.
-- Все операции запуска/завершения проверки должны выполняться транзакционно
-- с блокировками и серверной проверкой описанных ниже инвариантов.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ===============================================================
-- 01. СОТРУДНИКИ, ПОДРАЗДЕЛЕНИЯ, РОЛИ И ГРУППЫ
-- ===============================================================

CREATE TABLE departments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id   UUID REFERENCES departments(id) ON DELETE RESTRICT,
    code        VARCHAR(50) NOT NULL UNIQUE,
    name        VARCHAR(200) NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_departments_not_self CHECK (parent_id IS DISTINCT FROM id)
);

-- users объединяет обе исходные таблицы users.
-- login (первая схема) = username (вторая схема), поэтому хранится один username.
CREATE TABLE users (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    department_id    UUID REFERENCES departments(id) ON DELETE SET NULL,
    email            VARCHAR(320) NOT NULL UNIQUE,
    username         VARCHAR(80) NOT NULL UNIQUE,
    display_name     VARCHAR(160) NOT NULL,
    full_name        VARCHAR(255),
    password_hash    TEXT,
    avatar_url       TEXT,
    external_subject VARCHAR(255) UNIQUE,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- В MVP ровно один руководитель на отдел. Сам руководитель может
-- одновременно принадлежать этому отделу как обычный user.
ALTER TABLE departments
    ADD COLUMN manager_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE TABLE roles (
    id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code  VARCHAR(50) NOT NULL UNIQUE,
    name  VARCHAR(100) NOT NULL
);

CREATE TABLE user_roles (
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id    UUID NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE user_groups (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(120) NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE group_members (
    group_id  UUID NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
    user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, user_id)
);

-- ===============================================================
-- 02. БАЗА ЗНАНИЙ: ПРОСТРАНСТВА, СТРАНИЦЫ, ПРАВА, ТЕГИ
-- ===============================================================

CREATE TABLE spaces (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_key   VARCHAR(40) NOT NULL UNIQUE,
    name        VARCHAR(200) NOT NULL,
    description TEXT,
    owner_id    UUID REFERENCES users(id) ON DELETE SET NULL,
    visibility  VARCHAR(16) NOT NULL DEFAULT 'private'
                CHECK (visibility IN ('private', 'internal')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE space_members (
    space_id  UUID NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role      VARCHAR(16) NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (space_id, user_id)
);

CREATE TABLE pages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id        UUID NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    parent_id       UUID,
    title           VARCHAR(500) NOT NULL,
    slug            VARCHAR(500),
    status          VARCHAR(16) NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'pending_review', 'rejected', 'published', 'archived')),
    current_version INTEGER NOT NULL DEFAULT 0 CHECK (current_version >= 0),
    position        INTEGER NOT NULL DEFAULT 0,
    created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ,
    UNIQUE (space_id, id),
    FOREIGN KEY (space_id, parent_id) REFERENCES pages(space_id, id) ON DELETE RESTRICT,
    CONSTRAINT ck_pages_not_self CHECK (parent_id IS DISTINCT FROM id)
);

CREATE TABLE page_revisions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id        UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    version        INTEGER NOT NULL CHECK (version > 0),
    title          VARCHAR(500) NOT NULL,
    content        JSONB NOT NULL,
    change_message TEXT,
    editor_id      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (page_id, version)
);

CREATE TABLE page_permissions (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id    UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    user_id    UUID REFERENCES users(id) ON DELETE CASCADE,
    group_id   UUID REFERENCES user_groups(id) ON DELETE CASCADE,
    permission VARCHAR(12) NOT NULL CHECK (permission IN ('view', 'edit', 'admin')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_page_permission_subject CHECK (num_nonnulls(user_id, group_id) = 1)
);

CREATE TABLE comments (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id    UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    parent_id  UUID,
    author_id  UUID REFERENCES users(id) ON DELETE SET NULL,
    body       JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    UNIQUE (page_id, id),
    FOREIGN KEY (page_id, parent_id) REFERENCES comments(page_id, id) ON DELETE CASCADE,
    CONSTRAINT ck_comments_not_self CHECK (parent_id IS DISTINCT FROM id)
);

CREATE TABLE attachments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id         UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    uploaded_by     UUID REFERENCES users(id) ON DELETE SET NULL,
    filename        TEXT NOT NULL,
    media_type      VARCHAR(255),
    byte_size       BIGINT NOT NULL CHECK (byte_size >= 0),
    -- Все байты хранятся внутри PostgreSQL, а не через внешний storage_key.
    encryption_scheme VARCHAR(32) NOT NULL DEFAULT 'pgcrypto-chunked'
      CHECK (encryption_scheme IN ('pgcrypto-chunked')),
    encryption_key_id VARCHAR(120) NOT NULL,
    checksum_sha256 CHAR(64),
    version_no      INTEGER NOT NULL DEFAULT 1 CHECK (version_no > 0),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE labels (
    id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE page_labels (
    page_id  UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    label_id UUID NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
    PRIMARY KEY (page_id, label_id)
);

CREATE TABLE page_favorites (
    page_id    UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (page_id, user_id)
);

CREATE TABLE page_reactions (
    page_id    UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reaction   VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (page_id, user_id, reaction)
);

CREATE TABLE page_links (
    source_page_id UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    target_page_id UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    PRIMARY KEY (source_page_id, target_page_id)
);

-- ===============================================================
-- 03. КОМПЕТЕНЦИИ: КАТЕГОРИИ, КАРТЫ, ТЕКУЩИЙ УРОВЕНЬ, ТЕГИ
-- ===============================================================

-- Общая competency_categories с полями обеих исходных схем.
CREATE TABLE competency_categories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id   UUID REFERENCES competency_categories(id) ON DELETE RESTRICT,
    code        VARCHAR(50) NOT NULL UNIQUE,
    name        VARCHAR(200) NOT NULL,
    description TEXT,
    position    INTEGER NOT NULL DEFAULT 0,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT ck_categories_not_self CHECK (parent_id IS DISTINCT FROM id)
);

-- Общая competencies с полями обеих исходных схем.
CREATE TABLE competencies (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id         UUID NOT NULL REFERENCES competency_categories(id) ON DELETE RESTRICT,
    code                VARCHAR(80) NOT NULL UNIQUE,
    name                VARCHAR(255) NOT NULL,
    description         TEXT,
    assessment_criteria TEXT,
    max_level           SMALLINT NOT NULL DEFAULT 3 CHECK (max_level = 3),
    position            INTEGER NOT NULL DEFAULT 0,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE competency_labels (
    competency_id UUID NOT NULL REFERENCES competencies(id) ON DELETE CASCADE,
    label_id      UUID NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
    PRIMARY KEY (competency_id, label_id)
);

CREATE TABLE competency_profiles (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    department_id UUID NOT NULL REFERENCES departments(id) ON DELETE RESTRICT,
    name          VARCHAR(200) NOT NULL,
    version       INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    status        VARCHAR(24) NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft', 'pending_approval', 'approved', 'returned', 'archived')),
    prepared_by   UUID REFERENCES users(id) ON DELETE SET NULL,
    submitted_at  TIMESTAMPTZ,
    approved_by   UUID REFERENCES users(id) ON DELETE SET NULL,
    approved_at   TIMESTAMPTZ,
    review_note   TEXT,
    is_active     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (department_id, name, version),
    CHECK (status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
    CHECK (NOT is_active OR status = 'approved')
);

CREATE TABLE profile_competencies (
    profile_id    UUID NOT NULL REFERENCES competency_profiles(id) ON DELETE CASCADE,
    competency_id UUID NOT NULL REFERENCES competencies(id) ON DELETE RESTRICT,
    target_level  SMALLINT NOT NULL CHECK (target_level BETWEEN 0 AND 3),
    weight        NUMERIC(5,2) NOT NULL DEFAULT 1 CHECK (weight > 0 AND weight <= 100),
    PRIMARY KEY (profile_id, competency_id)
);

CREATE TABLE employee_competencies (
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    competency_id UUID NOT NULL REFERENCES competencies(id) ON DELETE RESTRICT,
    -- Пустая запись означает «не оценено»; 0 — реальная оценка.
    current_level SMALLINT NOT NULL CHECK (current_level BETWEEN 0 AND 3),
    last_level    SMALLINT NOT NULL CHECK (last_level BETWEEN 0 AND 3),
    last_assessed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    assessed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    notes         TEXT,
    PRIMARY KEY (user_id, competency_id),
    CHECK(current_level >= last_level)
);

CREATE TABLE competency_scales (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(120) NOT NULL,
    min_score   INTEGER NOT NULL DEFAULT 0,
    max_score   INTEGER NOT NULL DEFAULT 3,
    description TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT ck_scale_range CHECK (min_score = 0 AND max_score = 3)
);

-- ===============================================================
-- 04. ТЕСТЫ, ВОПРОСЫ, ВАРИАНТЫ, ПОПЫТКИ И ОТВЕТЫ
-- ===============================================================

CREATE TABLE questions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    competency_id UUID NOT NULL REFERENCES competencies(id) ON DELETE RESTRICT,
    body          TEXT NOT NULL CHECK (length(btrim(body)) > 0),
    question_type VARCHAR(16) NOT NULL CHECK (question_type IN ('single', 'multiple')),
    difficulty    SMALLINT NOT NULL DEFAULT 3 CHECK (difficulty BETWEEN 1 AND 5),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    moderation_status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (moderation_status IN ('pending','approved','rejected')),
    reviewed_by   UUID REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at   TIMESTAMPTZ,
    rejection_reason TEXT,
    import_item_id UUID UNIQUE,
    source_page_id UUID REFERENCES pages(id) ON DELETE SET NULL,
    source_revision_id UUID REFERENCES page_revisions(id) ON DELETE SET NULL,
    explanation   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_reviewed_approved CHECK
       (moderation_status <> 'approved' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);

CREATE TABLE answer_options (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    position    SMALLINT NOT NULL CHECK (position > 0),
    body        TEXT NOT NULL CHECK (length(btrim(body)) > 0),
    is_correct  BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (question_id, position),
    UNIQUE (question_id, id)
);

CREATE TABLE tests (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title            VARCHAR(250) NOT NULL,
    description      TEXT,
    scope            VARCHAR(16) NOT NULL CHECK (scope IN ('general', 'department')),
    department_id    UUID REFERENCES departments(id) ON DELETE RESTRICT,
    status           VARCHAR(16) NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft', 'published', 'archived')),
    duration_minutes INTEGER CHECK (duration_minutes > 0),
    version          INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    passing_percent  NUMERIC(5,2) NOT NULL DEFAULT 70
                     CHECK (passing_percent BETWEEN 0 AND 100),
    created_by       UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_test_scope CHECK (
        (scope = 'general' AND department_id IS NULL) OR
        (scope = 'department' AND department_id IS NOT NULL)
    )
);

CREATE TABLE test_questions (
    test_id     UUID NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    question_id UUID NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
    block_competency_id UUID NOT NULL,
    position    INTEGER NOT NULL CHECK (position > 0),
    points      NUMERIC(7,2) NOT NULL DEFAULT 1 CHECK (points > 0),
    PRIMARY KEY (test_id, question_id),
    UNIQUE (test_id, position)
);

CREATE TABLE test_assignments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    test_id     UUID NOT NULL REFERENCES tests(id) ON DELETE RESTRICT,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    assigned_by UUID REFERENCES users(id) ON DELETE SET NULL,
    assignment_type VARCHAR(20) NOT NULL DEFAULT 'manager_assigned'
       CHECK (assignment_type IN ('manager_assigned','self_assigned')),
    purpose VARCHAR(16) NOT NULL DEFAULT 'primary'
       CHECK (purpose IN ('primary','confirmation')),
    status VARCHAR(16) NOT NULL DEFAULT 'assigned'
       CHECK (status IN ('assigned','in_progress','completed','expired','cancelled')),
    test_version INTEGER NOT NULL DEFAULT 1 CHECK (test_version > 0),
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    due_at      TIMESTAMPTZ,
    CHECK (due_at IS NULL OR due_at >= assigned_at),
    CHECK (assignment_type <> 'self_assigned' OR assigned_by IS NULL)
);

CREATE TABLE test_attempts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assignment_id UUID NOT NULL REFERENCES test_assignments(id) ON DELETE RESTRICT,
    status        VARCHAR(16) NOT NULL DEFAULT 'in_progress'
                  CHECK (status IN ('in_progress', 'submitted', 'graded')),
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    score_percent NUMERIC(5,2) CHECK (score_percent BETWEEN 0 AND 100),
    immutable_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK (jsonb_typeof(immutable_snapshot) = 'object'),
    UNIQUE (assignment_id),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE attempt_answers (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id     UUID NOT NULL REFERENCES test_attempts(id) ON DELETE RESTRICT,
    question_id    UUID NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
    is_correct     BOOLEAN,
    awarded_points NUMERIC(7,2) CHECK (awarded_points >= 0),
    answered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (attempt_id, question_id),
    UNIQUE (id, question_id)
);

CREATE TABLE attempt_selected_options (
    attempt_answer_id UUID NOT NULL,
    question_id       UUID NOT NULL,
    option_id         UUID NOT NULL,
    PRIMARY KEY (attempt_answer_id, option_id),
    FOREIGN KEY (attempt_answer_id, question_id)
        REFERENCES attempt_answers(id, question_id) ON DELETE CASCADE,
    FOREIGN KEY (question_id, option_id)
        REFERENCES answer_options(question_id, id) ON DELETE RESTRICT
);

-- ===============================================================
-- 05. СОБЕСЕДОВАНИЯ И ОБЕ МОДЕЛИ ОЦЕНОК
-- ===============================================================

CREATE TABLE interviews (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id    UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    interviewer_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    scheduled_at   TIMESTAMPTZ NOT NULL,
    conducted_at   TIMESTAMPTZ,
    status         VARCHAR(16) NOT NULL DEFAULT 'scheduled'
                   CHECK (status IN ('scheduled', 'completed', 'cancelled')),
    summary        TEXT,
    CHECK (employee_id <> interviewer_id)
);

CREATE TABLE interview_ratings (
    interview_id  UUID NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
    competency_id UUID NOT NULL REFERENCES competencies(id) ON DELETE RESTRICT,
    level         SMALLINT NOT NULL CHECK (level BETWEEN 0 AND 3),
    notes         TEXT,
    PRIMARY KEY (interview_id, competency_id)
);

-- Из загруженной схемы: одна сессия оценивания множества компетенций.
CREATE TABLE competency_assessments (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    specialist_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    evaluator_id  UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    scale_id      UUID NOT NULL REFERENCES competency_scales(id) ON DELETE RESTRICT,
    status        VARCHAR(20) NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft', 'completed')),
    assessed_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    comment       TEXT
);

CREATE TABLE competency_scores (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id UUID NOT NULL REFERENCES competency_assessments(id) ON DELETE CASCADE,
    competency_id UUID NOT NULL REFERENCES competencies(id) ON DELETE RESTRICT,
    score         INTEGER NOT NULL CHECK (score BETWEEN 0 AND 3),
    comment       TEXT,
    UNIQUE (assessment_id, competency_id)
);

-- Из первой схемы: журнал отдельных оценок компетенции из разных источников.
-- В оригинале тоже назывался competency_assessments, но это ДРУГАЯ сущность,
-- поэтому переименован в competency_assessment_events без потери столбцов.
CREATE TABLE competency_assessment_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    competency_id   UUID NOT NULL REFERENCES competencies(id) ON DELETE RESTRICT,
    source_type     VARCHAR(16) NOT NULL
                    CHECK (source_type IN ('manual', 'test', 'interview')),
    test_attempt_id UUID REFERENCES test_attempts(id) ON DELETE RESTRICT,
    interview_id    UUID,
    level           SMALLINT NOT NULL CHECK (level BETWEEN 0 AND 3),
    assessed_by     UUID REFERENCES users(id) ON DELETE SET NULL,
    assessed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes           TEXT,
    is_confirmation BOOLEAN NOT NULL DEFAULT FALSE,
    previous_event_id UUID REFERENCES competency_assessment_events(id) ON DELETE RESTRICT,
    FOREIGN KEY (interview_id, competency_id)
        REFERENCES interview_ratings(interview_id, competency_id) ON DELETE RESTRICT,
    CONSTRAINT ck_confirmation_level CHECK (NOT is_confirmation OR level = 3),
    CONSTRAINT ck_assessment_event_source CHECK (
        (source_type = 'manual' AND test_attempt_id IS NULL AND interview_id IS NULL) OR
        (source_type = 'test' AND test_attempt_id IS NOT NULL AND interview_id IS NULL) OR
        (source_type = 'interview' AND test_attempt_id IS NULL AND interview_id IS NOT NULL)
    )
);


-- ===============================================================
-- 06. ДОПОЛНЕНИЯ ПО СОГЛАСОВАННОМУ ТЗ v4
-- ===============================================================

-- Одна утверждённая активная карта на подразделение.
CREATE UNIQUE INDEX ux_department_active_profile
  ON competency_profiles(department_id) WHERE is_active;

CREATE TABLE department_kk_responsibles (
    department_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    appointed_by UUID REFERENCES users(id) ON DELETE SET NULL,
    appointed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (department_id, user_id)
);

CREATE TABLE employee_additional_competencies (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    competency_id UUID NOT NULL REFERENCES competencies(id) ON DELETE RESTRICT,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY(user_id, competency_id)
);

-- Импорт сохраняет неизменённый JSON, даже если его структура не соответствует
-- предлагаемому контракту. Вопросы попадают в общий банк только после проверки.
CREATE TABLE question_imports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    uploaded_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    filename TEXT NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'uploaded'
       CHECK (status IN ('uploaded','validated','processed','failed')),
    error_summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(payload) IN ('array','object'))
);

CREATE TABLE question_import_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    import_id UUID NOT NULL REFERENCES question_imports(id) ON DELETE RESTRICT,
    external_id TEXT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    payload JSONB NOT NULL,
    validation_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
       CHECK (status IN ('pending','approved','rejected','invalid')),
    reviewed_by UUID REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMPTZ,
    rejection_reason TEXT,
    UNIQUE(import_id, ordinal),
    CHECK (jsonb_typeof(validation_errors) = 'array')
);

ALTER TABLE questions ADD CONSTRAINT fk_questions_import_item
 FOREIGN KEY (import_item_id) REFERENCES question_import_items(id) ON DELETE SET NULL;

CREATE TABLE question_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id UUID NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK (version > 0),
    body TEXT NOT NULL,
    question_type VARCHAR(16) NOT NULL CHECK (question_type IN ('single','multiple')),
    answer_key_snapshot JSONB NOT NULL,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(question_id, version),
    UNIQUE(question_id, id)
);

CREATE TABLE test_blocks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    test_id UUID NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    title VARCHAR(250) NOT NULL,
    position INTEGER NOT NULL CHECK (position > 0),
    UNIQUE(test_id, id),
    UNIQUE(test_id, position)
);

CREATE TABLE test_block_competencies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    test_id UUID NOT NULL,
    block_id UUID NOT NULL,
    competency_id UUID NOT NULL REFERENCES competencies(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (position > 0),
    UNIQUE(test_id, id),
    UNIQUE(test_id, competency_id),
    UNIQUE(block_id, position),
    UNIQUE(id, competency_id),
    FOREIGN KEY(test_id, block_id) REFERENCES test_blocks(test_id,id) ON DELETE CASCADE
);

ALTER TABLE test_questions
 ADD CONSTRAINT fk_test_questions_block_competency
 FOREIGN KEY (test_id,block_competency_id)
 REFERENCES test_block_competencies(test_id,id) ON DELETE CASCADE;

-- Привязываем вопрос к его компетенции через составной FK.
ALTER TABLE questions ADD CONSTRAINT uq_questions_id_comp UNIQUE (id,competency_id);
ALTER TABLE test_questions ADD COLUMN competency_id UUID NOT NULL;
ALTER TABLE test_questions ADD CONSTRAINT fk_tq_question_comp
 FOREIGN KEY (question_id,competency_id)
 REFERENCES questions(id,competency_id) ON DELETE RESTRICT;
ALTER TABLE test_questions ADD CONSTRAINT fk_tq_block_comp
 FOREIGN KEY (block_competency_id,competency_id)
 REFERENCES test_block_competencies(id,competency_id) ON DELETE RESTRICT;

-- Версия опубликованного теста. При редактировании опубликованного теста
-- создаётся новая версия; назначение ссылается на конкретный снимок.
CREATE TABLE test_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    test_id UUID NOT NULL REFERENCES tests(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK (version > 0),
    frozen_structure JSONB NOT NULL,
    published_by UUID REFERENCES users(id) ON DELETE SET NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(test_id, version),
    UNIQUE(test_id, id),
    CHECK(jsonb_typeof(frozen_structure) = 'object')
);
ALTER TABLE test_assignments
 ADD CONSTRAINT fk_test_assignment_version
 FOREIGN KEY(test_id,test_version) REFERENCES test_versions(test_id,version) ON DELETE RESTRICT;

-- Экземпляр прохождения: included/excluded определяются под блокировкой
-- транзакции; ни один исключённый вопрос не участвует в оценивании.
CREATE TABLE attempt_competencies (
    attempt_id UUID NOT NULL REFERENCES test_attempts(id) ON DELETE CASCADE,
    competency_id UUID NOT NULL REFERENCES competencies(id) ON DELETE RESTRICT,
    block_id UUID NOT NULL REFERENCES test_blocks(id) ON DELETE RESTRICT,
    state VARCHAR(16) NOT NULL CHECK (state IN ('included','excluded')),
    exclusion_reason VARCHAR(32) CHECK(exclusion_reason IN ('cooldown_30d')),
    available_at TIMESTAMPTZ,
    correct_count INTEGER CHECK(correct_count >= 0),
    question_count INTEGER CHECK(question_count >= 2),
    assessed_level SMALLINT CHECK(assessed_level BETWEEN 0 AND 3),
    PRIMARY KEY(attempt_id,competency_id),
    CHECK ((state='included' AND exclusion_reason IS NULL AND available_at IS NULL)
        OR (state='excluded' AND exclusion_reason IS NOT NULL AND available_at IS NOT NULL
           AND assessed_level IS NULL AND correct_count IS NULL)),
    CHECK (correct_count IS NULL OR question_count IS NULL OR correct_count <= question_count)
);

CREATE TABLE attempt_questions (
    attempt_id UUID NOT NULL REFERENCES test_attempts(id) ON DELETE CASCADE,
    question_id UUID NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
    competency_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    position INTEGER NOT NULL CHECK(position > 0),
    -- Frozen options must include correct flags; never return answer key to client.
    frozen_question JSONB NOT NULL,
    PRIMARY KEY(attempt_id,question_id),
    UNIQUE(attempt_id,position),
    FOREIGN KEY(attempt_id,competency_id)
       REFERENCES attempt_competencies(attempt_id,competency_id) ON DELETE RESTRICT,
    FOREIGN KEY(question_id,revision_id)
       REFERENCES question_revisions(question_id,id) ON DELETE RESTRICT,
    CHECK (jsonb_typeof(frozen_question) = 'object')
);

ALTER TABLE attempt_answers ADD CONSTRAINT fk_answer_attempt_question
 FOREIGN KEY(attempt_id,question_id)
 REFERENCES attempt_questions(attempt_id,question_id) ON DELETE RESTRICT;

-- Не допускаем фиксацию результатов по компетенции, которой нет в попытке.
CREATE TABLE test_competency_results (
    attempt_id UUID NOT NULL,
    competency_id UUID NOT NULL,
    correct_count INTEGER NOT NULL CHECK(correct_count >= 0),
    total_count INTEGER NOT NULL CHECK(total_count >= 2),
    level SMALLINT NOT NULL CHECK(level BETWEEN 0 AND 3),
    completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(attempt_id,competency_id),
    FOREIGN KEY(attempt_id,competency_id)
      REFERENCES attempt_competencies(attempt_id,competency_id) ON DELETE RESTRICT,
    CHECK(correct_count <= total_count),
    CHECK (
        (level = 0 AND correct_count = 0) OR
        (level = 1 AND correct_count > 0 AND correct_count < total_count) OR
        (level IN (2,3) AND correct_count = total_count)
    )
);

-- Собеседование назначается руководителем, с составом компетенций и дедлайном.
ALTER TABLE interviews ADD COLUMN assigned_by UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE interviews ADD COLUMN assigned_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE interviews ADD COLUMN due_at TIMESTAMPTZ;
ALTER TABLE interviews ADD COLUMN purpose VARCHAR(16) NOT NULL DEFAULT 'primary'
 CHECK(purpose IN ('primary','confirmation'));
ALTER TABLE interviews ADD CONSTRAINT ck_interview_due
 CHECK(due_at IS NULL OR due_at >= assigned_at);

CREATE TABLE interview_competencies (
    interview_id UUID NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
    competency_id UUID NOT NULL REFERENCES competencies(id) ON DELETE RESTRICT,
    PRIMARY KEY(interview_id, competency_id)
);
ALTER TABLE interview_ratings ADD CONSTRAINT fk_interview_selected_competency
 FOREIGN KEY(interview_id, competency_id)
 REFERENCES interview_competencies(interview_id,competency_id) ON DELETE RESTRICT;

-- Важное правило: данные о проверке не стираются при отмене назначения.
CREATE TABLE assessment_cooldowns (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    competency_id UUID NOT NULL REFERENCES competencies(id) ON DELETE RESTRICT,
    last_completed_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id,competency_id),
    CHECK (available_at >= last_completed_at + INTERVAL '30 days')
);

-- Подтверждение уровня 3 должно ссылаться на ранее полученный уровень 2.
ALTER TABLE competency_assessment_events
 ADD CONSTRAINT ck_confirmation_basis CHECK
 (NOT is_confirmation OR previous_event_id IS NOT NULL);

-- Часть исходных таблиц оценок оставлена для совместимости модели,
-- но источником истины служат competency_assessment_events +
-- employee_competencies; двойная запись результатов запрещена бизнес-слоем.

-- Шаблоны, согласование страниц и аудит из базового ТЗ.
CREATE TABLE templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(200) NOT NULL,
    template_type VARCHAR(32) NOT NULL DEFAULT 'page',
    content JSONB NOT NULL,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE page_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    revision_id UUID NOT NULL REFERENCES page_revisions(id) ON DELETE RESTRICT,
    submitted_by UUID REFERENCES users(id) ON DELETE SET NULL,
    reviewed_by UUID REFERENCES users(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
      CHECK(status IN ('pending','approved','rejected')),
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at TIMESTAMPTZ,
    comment TEXT
);

CREATE TABLE audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(120) NOT NULL,
    object_type VARCHAR(80) NOT NULL,
    object_id UUID,
    ip_address INET,
    event_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Для медиа используется независимое шифрование каждого чанка с pgcrypto.
-- Пароль/ключ НЕ хранить в БД; для Range запросов выбирать чанки по смещению.
CREATE TABLE attachment_chunks (
    attachment_id UUID NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    chunk_no INTEGER NOT NULL CHECK (chunk_no >= 0),
    byte_offset BIGINT NOT NULL CHECK (byte_offset >= 0),
    plain_length INTEGER NOT NULL CHECK (plain_length > 0),
    encrypted_data BYTEA NOT NULL,
    PRIMARY KEY (attachment_id,chunk_no),
    UNIQUE (attachment_id,byte_offset)
);

-- ===============================================================
-- 07. ИНДЕКСЫ
-- ===============================================================
CREATE INDEX ix_departments_parent ON departments(parent_id);
CREATE INDEX ix_departments_manager ON departments(manager_id);
CREATE INDEX ix_users_department ON users(department_id,is_active);
CREATE INDEX ix_pages_parent ON pages(space_id,parent_id,position);
CREATE INDEX ix_page_revisions_page ON page_revisions(page_id,created_at DESC);
CREATE INDEX ix_page_permissions_user ON page_permissions(user_id) WHERE user_id IS NOT NULL;
CREATE INDEX ix_page_permissions_group ON page_permissions(group_id) WHERE group_id IS NOT NULL;
CREATE INDEX ix_page_labels_label ON page_labels(label_id);
CREATE INDEX ix_attachment_chunks_range ON attachment_chunks(attachment_id,byte_offset);
CREATE INDEX ix_competencies_category ON competencies(category_id);
CREATE INDEX ix_profile_competencies_comp ON profile_competencies(competency_id);
CREATE INDEX ix_employee_competencies_comp ON employee_competencies(competency_id);
CREATE INDEX ix_additional_competencies_comp ON employee_additional_competencies(competency_id);
CREATE INDEX ix_questions_comp_status ON questions(competency_id,moderation_status);
CREATE INDEX ix_question_import_items_import ON question_import_items(import_id,status);
CREATE INDEX ix_question_revisions_question ON question_revisions(question_id,version DESC);
CREATE INDEX ix_test_blocks_test ON test_blocks(test_id,position);
CREATE INDEX ix_tbc_competency ON test_block_competencies(competency_id);
CREATE INDEX ix_tq_block_comp ON test_questions(block_competency_id);
CREATE INDEX ix_test_assignments_user ON test_assignments(user_id,assigned_at DESC);
CREATE INDEX ix_test_attempts_status ON test_attempts(status,started_at DESC);
CREATE INDEX ix_attempt_answers_wrong ON attempt_answers(attempt_id) WHERE is_correct IS FALSE;
CREATE INDEX ix_attempt_questions_comp ON attempt_questions(attempt_id,competency_id);
CREATE INDEX ix_interviews_employee ON interviews(employee_id,scheduled_at DESC);
CREATE INDEX ix_interviews_interviewer ON interviews(interviewer_id,scheduled_at DESC);
CREATE INDEX ix_events_user_comp_date ON competency_assessment_events(user_id,competency_id,assessed_at DESC);
CREATE INDEX ix_events_attempt ON competency_assessment_events(test_attempt_id) WHERE test_attempt_id IS NOT NULL;
CREATE INDEX ix_events_interview ON competency_assessment_events(interview_id) WHERE interview_id IS NOT NULL;
CREATE INDEX ix_audit_log_actor_time ON audit_log(actor_id,occurred_at DESC);
CREATE INDEX ix_audit_log_object_time ON audit_log(object_type,object_id,occurred_at DESC);
-- Для ILIKE %строка% нужен pg_trgm; стандартный B-tree такой поиск не ускоряет.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX ix_pages_title_trgm ON pages USING GIN(title gin_trgm_ops);
CREATE INDEX ix_labels_name ON labels(name);

-- ===============================================================
-- 08. ПРЕДСТАВЛЕНИЯ
-- ===============================================================
CREATE VIEW v_employee_errors AS
SELECT a.user_id,a.test_id,t.id AS attempt_id,aa.question_id,
       q.competency_id,aa.answered_at,aa.awarded_points
FROM attempt_answers aa
JOIN test_attempts t ON t.id = aa.attempt_id
JOIN test_assignments a ON a.id=t.assignment_id
JOIN questions q ON q.id=aa.question_id
WHERE aa.is_correct IS FALSE;

CREATE VIEW v_test_results AS
SELECT a.user_id,a.test_id,t.id AS attempt_id,
       t.status,t.started_at,t.finished_at,t.score_percent,
       CASE WHEN t.score_percent IS NULL THEN NULL
            ELSE t.score_percent >= tests.passing_percent END AS passed
FROM test_attempts t
JOIN test_assignments a ON a.id=t.assignment_id
JOIN tests ON tests.id=a.test_id;

CREATE VIEW v_department_competency_summary AS
SELECT u.department_id,ec.competency_id,ec.current_level,
       count(*)::BIGINT AS employee_count
FROM employee_competencies ec
JOIN users u ON u.id=ec.user_id
WHERE u.department_id IS NOT NULL AND u.is_active
GROUP BY u.department_id,ec.competency_id,ec.current_level;

COMMIT;
