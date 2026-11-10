--
-- PostgreSQL database dump
--

\restrict EzjBD3hgv4Zb8bpWmz9GChXRT6H2QyNYu4dkyOzUGwHnPmFvPXpGhqAqi6hMVci

-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: answer_options; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.answer_options (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    question_id uuid NOT NULL,
    "position" smallint NOT NULL,
    body text NOT NULL,
    is_correct boolean DEFAULT false NOT NULL,
    CONSTRAINT answer_options_body_check CHECK ((length(btrim(body)) > 0)),
    CONSTRAINT answer_options_position_check CHECK (("position" > 0))
);


--
-- Name: assessment_cooldowns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assessment_cooldowns (
    user_id uuid NOT NULL,
    competency_id uuid NOT NULL,
    last_completed_at timestamp with time zone NOT NULL,
    available_at timestamp with time zone NOT NULL,
    CONSTRAINT assessment_cooldowns_check CHECK ((available_at >= (last_completed_at + '30 days'::interval)))
);


--
-- Name: attachment_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.attachment_chunks (
    attachment_id uuid NOT NULL,
    chunk_no integer NOT NULL,
    byte_offset bigint NOT NULL,
    plain_length integer NOT NULL,
    encrypted_data bytea NOT NULL,
    CONSTRAINT attachment_chunks_byte_offset_check CHECK ((byte_offset >= 0)),
    CONSTRAINT attachment_chunks_chunk_no_check CHECK ((chunk_no >= 0)),
    CONSTRAINT attachment_chunks_plain_length_check CHECK ((plain_length > 0))
);


--
-- Name: attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.attachments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    page_id uuid NOT NULL,
    uploaded_by uuid,
    filename text NOT NULL,
    media_type character varying(255),
    byte_size bigint NOT NULL,
    encryption_scheme character varying(32) DEFAULT 'pgcrypto-chunked'::character varying NOT NULL,
    encryption_key_id character varying(120) NOT NULL,
    checksum_sha256 character(64),
    version_no integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT attachments_byte_size_check CHECK ((byte_size >= 0)),
    CONSTRAINT attachments_encryption_scheme_check CHECK (((encryption_scheme)::text = 'pgcrypto-chunked'::text)),
    CONSTRAINT attachments_version_no_check CHECK ((version_no > 0))
);


--
-- Name: attempt_answers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.attempt_answers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    attempt_id uuid NOT NULL,
    question_id uuid NOT NULL,
    is_correct boolean,
    awarded_points numeric(7,2),
    answered_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT attempt_answers_awarded_points_check CHECK ((awarded_points >= (0)::numeric))
);


--
-- Name: attempt_competencies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.attempt_competencies (
    attempt_id uuid NOT NULL,
    competency_id uuid NOT NULL,
    block_id uuid NOT NULL,
    state character varying(16) NOT NULL,
    exclusion_reason character varying(32),
    available_at timestamp with time zone,
    correct_count integer,
    question_count integer,
    assessed_level smallint,
    CONSTRAINT attempt_competencies_assessed_level_check CHECK (((assessed_level >= 0) AND (assessed_level <= 3))),
    CONSTRAINT attempt_competencies_check1 CHECK (((correct_count IS NULL) OR (question_count IS NULL) OR (correct_count <= question_count))),
    CONSTRAINT attempt_competencies_correct_count_check CHECK ((correct_count >= 0)),
    CONSTRAINT attempt_competencies_question_count_check CHECK ((question_count >= 2)),
    CONSTRAINT attempt_competencies_state_check CHECK (((state)::text = ANY (ARRAY[('included'::character varying)::text, ('excluded'::character varying)::text]))),
    CONSTRAINT ck_attempt_competency_exclusion_v2 CHECK (((((state)::text = 'included'::text) AND (exclusion_reason IS NULL) AND (available_at IS NULL)) OR (((state)::text = 'excluded'::text) AND (assessed_level IS NULL) AND (correct_count IS NULL) AND ((((exclusion_reason)::text = 'cooldown_30d'::text) AND (available_at IS NOT NULL)) OR (((exclusion_reason)::text = 'reserved_active'::text) AND (available_at IS NULL))))))
);


--
-- Name: attempt_questions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.attempt_questions (
    attempt_id uuid NOT NULL,
    question_id uuid NOT NULL,
    competency_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    "position" integer NOT NULL,
    frozen_question jsonb NOT NULL,
    CONSTRAINT attempt_questions_frozen_question_check CHECK ((jsonb_typeof(frozen_question) = 'object'::text)),
    CONSTRAINT attempt_questions_position_check CHECK (("position" > 0))
);


--
-- Name: attempt_selected_options; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.attempt_selected_options (
    attempt_answer_id uuid NOT NULL,
    question_id uuid NOT NULL,
    option_id uuid NOT NULL
);


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    actor_id uuid,
    action character varying(120) NOT NULL,
    object_type character varying(80) NOT NULL,
    object_id uuid,
    ip_address inet,
    event_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: comments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.comments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    page_id uuid NOT NULL,
    parent_id uuid,
    author_id uuid,
    body jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT ck_comments_not_self CHECK ((parent_id IS DISTINCT FROM id))
);


--
-- Name: competencies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.competencies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    category_id uuid NOT NULL,
    code character varying(80) NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    assessment_criteria text,
    max_level smallint DEFAULT 3 NOT NULL,
    "position" integer DEFAULT 0 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT competencies_max_level_check CHECK ((max_level = 3))
);


--
-- Name: competency_assessment_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.competency_assessment_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    competency_id uuid NOT NULL,
    source_type character varying(16) NOT NULL,
    test_attempt_id uuid,
    interview_id uuid,
    level smallint NOT NULL,
    assessed_by uuid,
    assessed_at timestamp with time zone DEFAULT now() NOT NULL,
    notes text,
    is_confirmation boolean DEFAULT false NOT NULL,
    previous_event_id uuid,
    CONSTRAINT ck_assessment_event_source CHECK (((((source_type)::text = 'manual'::text) AND (test_attempt_id IS NULL) AND (interview_id IS NULL)) OR (((source_type)::text = 'test'::text) AND (test_attempt_id IS NOT NULL) AND (interview_id IS NULL)) OR (((source_type)::text = 'interview'::text) AND (test_attempt_id IS NULL) AND (interview_id IS NOT NULL)))),
    CONSTRAINT ck_confirmation_basis CHECK (((NOT is_confirmation) OR (previous_event_id IS NOT NULL))),
    CONSTRAINT ck_confirmation_level CHECK (((NOT is_confirmation) OR (level = 3))),
    CONSTRAINT competency_assessment_events_level_check CHECK (((level >= 0) AND (level <= 3))),
    CONSTRAINT competency_assessment_events_source_type_check CHECK (((source_type)::text = ANY (ARRAY[('manual'::character varying)::text, ('test'::character varying)::text, ('interview'::character varying)::text])))
);


--
-- Name: competency_assessments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.competency_assessments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    specialist_id uuid NOT NULL,
    evaluator_id uuid NOT NULL,
    scale_id uuid NOT NULL,
    status character varying(20) DEFAULT 'draft'::character varying NOT NULL,
    assessed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    comment text,
    CONSTRAINT competency_assessments_status_check CHECK (((status)::text = ANY (ARRAY[('draft'::character varying)::text, ('completed'::character varying)::text])))
);


--
-- Name: competency_categories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.competency_categories (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    parent_id uuid,
    code character varying(50) NOT NULL,
    name character varying(200) NOT NULL,
    description text,
    "position" integer DEFAULT 0 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    CONSTRAINT ck_categories_not_self CHECK ((parent_id IS DISTINCT FROM id))
);


--
-- Name: competency_labels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.competency_labels (
    competency_id uuid NOT NULL,
    label_id uuid NOT NULL
);


--
-- Name: competency_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.competency_profiles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    department_id uuid NOT NULL,
    name character varying(200) NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    status character varying(24) DEFAULT 'draft'::character varying NOT NULL,
    prepared_by uuid,
    submitted_at timestamp with time zone,
    approved_by uuid,
    approved_at timestamp with time zone,
    review_note text,
    is_active boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT competency_profiles_check CHECK ((((status)::text <> 'approved'::text) OR ((approved_by IS NOT NULL) AND (approved_at IS NOT NULL)))),
    CONSTRAINT competency_profiles_check1 CHECK (((NOT is_active) OR ((status)::text = 'approved'::text))),
    CONSTRAINT competency_profiles_status_check CHECK (((status)::text = ANY (ARRAY[('draft'::character varying)::text, ('pending_approval'::character varying)::text, ('approved'::character varying)::text, ('returned'::character varying)::text, ('archived'::character varying)::text]))),
    CONSTRAINT competency_profiles_version_check CHECK ((version > 0))
);


--
-- Name: competency_scales; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.competency_scales (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(120) NOT NULL,
    min_score integer DEFAULT 0 NOT NULL,
    max_score integer DEFAULT 3 NOT NULL,
    description text,
    is_active boolean DEFAULT true NOT NULL,
    CONSTRAINT ck_scale_range CHECK (((min_score = 0) AND (max_score = 3)))
);


--
-- Name: competency_scores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.competency_scores (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    assessment_id uuid NOT NULL,
    competency_id uuid NOT NULL,
    score integer NOT NULL,
    comment text,
    CONSTRAINT competency_scores_score_check CHECK (((score >= 0) AND (score <= 3)))
);


--
-- Name: department_kk_responsibles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.department_kk_responsibles (
    department_id uuid NOT NULL,
    user_id uuid NOT NULL,
    appointed_by uuid,
    appointed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: departments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.departments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    parent_id uuid,
    code character varying(50) NOT NULL,
    name character varying(200) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    manager_id uuid,
    CONSTRAINT ck_departments_not_self CHECK ((parent_id IS DISTINCT FROM id))
);


--
-- Name: employee_additional_competencies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.employee_additional_competencies (
    user_id uuid NOT NULL,
    competency_id uuid NOT NULL,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    added_by uuid NOT NULL,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: employee_competencies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.employee_competencies (
    user_id uuid NOT NULL,
    competency_id uuid NOT NULL,
    current_level smallint NOT NULL,
    last_level smallint NOT NULL,
    last_assessed_at timestamp with time zone DEFAULT now() NOT NULL,
    assessed_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    notes text,
    CONSTRAINT employee_competencies_check CHECK ((current_level >= last_level)),
    CONSTRAINT employee_competencies_current_level_check CHECK (((current_level >= 0) AND (current_level <= 3))),
    CONSTRAINT employee_competencies_last_level_check CHECK (((last_level >= 0) AND (last_level <= 3)))
);


--
-- Name: group_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.group_members (
    group_id uuid NOT NULL,
    user_id uuid NOT NULL,
    joined_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: interview_competencies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interview_competencies (
    interview_id uuid NOT NULL,
    competency_id uuid NOT NULL
);


--
-- Name: interview_ratings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interview_ratings (
    interview_id uuid NOT NULL,
    competency_id uuid NOT NULL,
    level smallint NOT NULL,
    notes text,
    CONSTRAINT interview_ratings_level_check CHECK (((level >= 0) AND (level <= 3)))
);


--
-- Name: interviews; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interviews (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    employee_id uuid NOT NULL,
    interviewer_id uuid NOT NULL,
    scheduled_at timestamp with time zone NOT NULL,
    conducted_at timestamp with time zone,
    status character varying(16) DEFAULT 'scheduled'::character varying NOT NULL,
    summary text,
    assigned_by uuid,
    assigned_at timestamp with time zone DEFAULT now() NOT NULL,
    due_at timestamp with time zone,
    purpose character varying(16) DEFAULT 'primary'::character varying NOT NULL,
    CONSTRAINT ck_interview_due CHECK (((due_at IS NULL) OR (due_at >= assigned_at))),
    CONSTRAINT interviews_check CHECK ((employee_id <> interviewer_id)),
    CONSTRAINT interviews_purpose_check CHECK (((purpose)::text = ANY (ARRAY[('primary'::character varying)::text, ('confirmation'::character varying)::text]))),
    CONSTRAINT interviews_status_check CHECK (((status)::text = ANY (ARRAY[('scheduled'::character varying)::text, ('completed'::character varying)::text, ('cancelled'::character varying)::text])))
);


--
-- Name: labels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.labels (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL
);


--
-- Name: login_rate_limits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.login_rate_limits (
    scope text NOT NULL,
    key_hash character(64) NOT NULL,
    window_started timestamp with time zone NOT NULL,
    attempts integer NOT NULL,
    CONSTRAINT login_rate_limits_attempts_check CHECK ((attempts > 0)),
    CONSTRAINT login_rate_limits_scope_check CHECK ((scope = ANY (ARRAY['account'::text, 'ip'::text])))
);


--
-- Name: page_approvals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.page_approvals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    page_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    submitted_by uuid,
    reviewed_by uuid,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    submitted_at timestamp with time zone DEFAULT now() NOT NULL,
    reviewed_at timestamp with time zone,
    comment text,
    CONSTRAINT page_approvals_status_check CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('approved'::character varying)::text, ('rejected'::character varying)::text])))
);


--
-- Name: page_favorites; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.page_favorites (
    page_id uuid NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: page_labels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.page_labels (
    page_id uuid NOT NULL,
    label_id uuid NOT NULL
);


--
-- Name: page_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.page_links (
    source_page_id uuid NOT NULL,
    target_page_id uuid NOT NULL
);


--
-- Name: page_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.page_permissions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    page_id uuid NOT NULL,
    user_id uuid,
    group_id uuid,
    permission character varying(12) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_page_permission_subject CHECK ((num_nonnulls(user_id, group_id) = 1)),
    CONSTRAINT page_permissions_permission_check CHECK (((permission)::text = ANY (ARRAY[('view'::character varying)::text, ('edit'::character varying)::text, ('admin'::character varying)::text])))
);


--
-- Name: page_reactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.page_reactions (
    page_id uuid NOT NULL,
    user_id uuid NOT NULL,
    reaction character varying(32) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: page_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.page_revisions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    page_id uuid NOT NULL,
    version integer NOT NULL,
    title character varying(500) NOT NULL,
    content jsonb NOT NULL,
    change_message text,
    editor_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT page_revisions_version_check CHECK ((version > 0))
);


--
-- Name: pages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    space_id uuid NOT NULL,
    parent_id uuid,
    title character varying(500) NOT NULL,
    slug character varying(500),
    status character varying(16) DEFAULT 'draft'::character varying NOT NULL,
    current_version integer DEFAULT 0 NOT NULL,
    "position" integer DEFAULT 0 NOT NULL,
    created_by uuid,
    updated_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT ck_pages_not_self CHECK ((parent_id IS DISTINCT FROM id)),
    CONSTRAINT pages_current_version_check CHECK ((current_version >= 0)),
    CONSTRAINT pages_status_check CHECK (((status)::text = ANY (ARRAY[('draft'::character varying)::text, ('pending_review'::character varying)::text, ('rejected'::character varying)::text, ('published'::character varying)::text, ('archived'::character varying)::text])))
);


--
-- Name: profile_competencies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.profile_competencies (
    profile_id uuid NOT NULL,
    competency_id uuid NOT NULL,
    target_level smallint NOT NULL,
    weight numeric(5,2) DEFAULT 1 NOT NULL,
    CONSTRAINT profile_competencies_target_level_check CHECK (((target_level >= 0) AND (target_level <= 3))),
    CONSTRAINT profile_competencies_weight_check CHECK (((weight > (0)::numeric) AND (weight <= (100)::numeric)))
);


--
-- Name: question_import_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.question_import_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    import_id uuid NOT NULL,
    external_id text,
    ordinal integer NOT NULL,
    payload jsonb NOT NULL,
    validation_errors jsonb DEFAULT '[]'::jsonb NOT NULL,
    status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    reviewed_by uuid,
    reviewed_at timestamp with time zone,
    rejection_reason text,
    edited_payload jsonb,
    CONSTRAINT ck_import_edited_object CHECK (((edited_payload IS NULL) OR (jsonb_typeof(edited_payload) = 'object'::text))),
    CONSTRAINT question_import_items_ordinal_check CHECK ((ordinal >= 0)),
    CONSTRAINT question_import_items_status_check CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('approved'::character varying)::text, ('rejected'::character varying)::text, ('invalid'::character varying)::text]))),
    CONSTRAINT question_import_items_validation_errors_check CHECK ((jsonb_typeof(validation_errors) = 'array'::text))
);


--
-- Name: question_imports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.question_imports (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    uploaded_by uuid NOT NULL,
    filename text NOT NULL,
    payload jsonb NOT NULL,
    status character varying(16) DEFAULT 'uploaded'::character varying NOT NULL,
    error_summary text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    department_id uuid,
    CONSTRAINT question_imports_payload_check CHECK ((jsonb_typeof(payload) = ANY (ARRAY['array'::text, 'object'::text]))),
    CONSTRAINT question_imports_status_check CHECK (((status)::text = ANY (ARRAY[('uploaded'::character varying)::text, ('validated'::character varying)::text, ('processed'::character varying)::text, ('failed'::character varying)::text])))
);


--
-- Name: question_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.question_revisions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    question_id uuid NOT NULL,
    version integer NOT NULL,
    body text NOT NULL,
    question_type character varying(16) NOT NULL,
    answer_key_snapshot jsonb NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT question_revisions_question_type_check CHECK (((question_type)::text = ANY (ARRAY[('single'::character varying)::text, ('multiple'::character varying)::text]))),
    CONSTRAINT question_revisions_version_check CHECK ((version > 0))
);


--
-- Name: questions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.questions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    competency_id uuid NOT NULL,
    body text NOT NULL,
    question_type character varying(16) NOT NULL,
    difficulty smallint DEFAULT 3 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    moderation_status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    reviewed_by uuid,
    reviewed_at timestamp with time zone,
    rejection_reason text,
    import_item_id uuid,
    source_page_id uuid,
    source_revision_id uuid,
    explanation text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid,
    bank_department_id uuid,
    CONSTRAINT ck_reviewed_approved CHECK ((((moderation_status)::text <> 'approved'::text) OR ((reviewed_by IS NOT NULL) AND (reviewed_at IS NOT NULL)))),
    CONSTRAINT questions_body_check CHECK ((length(btrim(body)) > 0)),
    CONSTRAINT questions_difficulty_check CHECK (((difficulty >= 1) AND (difficulty <= 5))),
    CONSTRAINT questions_moderation_status_check CHECK (((moderation_status)::text = ANY (ARRAY[('pending'::character varying)::text, ('approved'::character varying)::text, ('rejected'::character varying)::text]))),
    CONSTRAINT questions_question_type_check CHECK (((question_type)::text = ANY (ARRAY[('single'::character varying)::text, ('multiple'::character varying)::text])))
);


--
-- Name: roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.roles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code character varying(50) NOT NULL,
    name character varying(100) NOT NULL
);


--
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    token character(64) NOT NULL,
    user_id uuid NOT NULL,
    csrf_token character varying(128) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: space_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.space_members (
    space_id uuid NOT NULL,
    user_id uuid NOT NULL,
    role character varying(16) NOT NULL,
    joined_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT space_members_role_check CHECK (((role)::text = ANY (ARRAY[('admin'::character varying)::text, ('editor'::character varying)::text, ('viewer'::character varying)::text])))
);


--
-- Name: spaces; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.spaces (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    space_key character varying(40) NOT NULL,
    name character varying(200) NOT NULL,
    description text,
    owner_id uuid,
    visibility character varying(16) DEFAULT 'private'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT spaces_visibility_check CHECK (((visibility)::text = ANY (ARRAY[('private'::character varying)::text, ('internal'::character varying)::text])))
);


--
-- Name: templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.templates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(200) NOT NULL,
    template_type character varying(32) DEFAULT 'page'::character varying NOT NULL,
    content jsonb NOT NULL,
    created_by uuid,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: test_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.test_assignments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    test_id uuid NOT NULL,
    user_id uuid NOT NULL,
    assigned_by uuid,
    assignment_type character varying(20) DEFAULT 'manager_assigned'::character varying NOT NULL,
    purpose character varying(16) DEFAULT 'primary'::character varying NOT NULL,
    status character varying(16) DEFAULT 'assigned'::character varying NOT NULL,
    test_version integer DEFAULT 1 NOT NULL,
    assigned_at timestamp with time zone DEFAULT now() NOT NULL,
    due_at timestamp with time zone,
    CONSTRAINT test_assignments_assignment_type_check CHECK (((assignment_type)::text = ANY (ARRAY[('manager_assigned'::character varying)::text, ('self_assigned'::character varying)::text]))),
    CONSTRAINT test_assignments_check CHECK (((due_at IS NULL) OR (due_at >= assigned_at))),
    CONSTRAINT test_assignments_check1 CHECK ((((assignment_type)::text <> 'self_assigned'::text) OR (assigned_by IS NULL))),
    CONSTRAINT test_assignments_purpose_check CHECK (((purpose)::text = ANY (ARRAY[('primary'::character varying)::text, ('confirmation'::character varying)::text]))),
    CONSTRAINT test_assignments_status_check CHECK (((status)::text = ANY (ARRAY[('assigned'::character varying)::text, ('in_progress'::character varying)::text, ('completed'::character varying)::text, ('expired'::character varying)::text, ('cancelled'::character varying)::text]))),
    CONSTRAINT test_assignments_test_version_check CHECK ((test_version > 0))
);


--
-- Name: test_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.test_attempts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    assignment_id uuid NOT NULL,
    status character varying(16) DEFAULT 'in_progress'::character varying NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    score_percent numeric(5,2),
    immutable_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT test_attempts_check CHECK (((finished_at IS NULL) OR (finished_at >= started_at))),
    CONSTRAINT test_attempts_immutable_snapshot_check CHECK ((jsonb_typeof(immutable_snapshot) = 'object'::text)),
    CONSTRAINT test_attempts_score_percent_check CHECK (((score_percent >= (0)::numeric) AND (score_percent <= (100)::numeric))),
    CONSTRAINT test_attempts_status_check CHECK (((status)::text = ANY (ARRAY[('in_progress'::character varying)::text, ('submitted'::character varying)::text, ('graded'::character varying)::text])))
);


--
-- Name: test_block_competencies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.test_block_competencies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    test_id uuid NOT NULL,
    block_id uuid NOT NULL,
    competency_id uuid NOT NULL,
    "position" integer NOT NULL,
    CONSTRAINT test_block_competencies_position_check CHECK (("position" > 0))
);


--
-- Name: test_blocks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.test_blocks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    test_id uuid NOT NULL,
    title character varying(250) NOT NULL,
    "position" integer NOT NULL,
    CONSTRAINT test_blocks_position_check CHECK (("position" > 0))
);


--
-- Name: test_competency_reservations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.test_competency_reservations (
    user_id uuid NOT NULL,
    competency_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    reserved_at timestamp with time zone DEFAULT now() NOT NULL,
    released_at timestamp with time zone
);


--
-- Name: test_competency_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.test_competency_results (
    attempt_id uuid NOT NULL,
    competency_id uuid NOT NULL,
    correct_count integer NOT NULL,
    total_count integer NOT NULL,
    level smallint NOT NULL,
    completed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT test_competency_results_check CHECK ((correct_count <= total_count)),
    CONSTRAINT test_competency_results_check1 CHECK ((((level = 0) AND (correct_count = 0)) OR ((level = 1) AND (correct_count > 0) AND (correct_count < total_count)) OR ((level = ANY (ARRAY[2, 3])) AND (correct_count = total_count)))),
    CONSTRAINT test_competency_results_correct_count_check CHECK ((correct_count >= 0)),
    CONSTRAINT test_competency_results_level_check CHECK (((level >= 0) AND (level <= 3))),
    CONSTRAINT test_competency_results_total_count_check CHECK ((total_count >= 2))
);


--
-- Name: test_questions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.test_questions (
    test_id uuid NOT NULL,
    question_id uuid NOT NULL,
    block_competency_id uuid NOT NULL,
    "position" integer NOT NULL,
    points numeric(7,2) DEFAULT 1 NOT NULL,
    competency_id uuid NOT NULL,
    CONSTRAINT test_questions_points_check CHECK ((points > (0)::numeric)),
    CONSTRAINT test_questions_position_check CHECK (("position" > 0))
);


--
-- Name: test_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.test_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    test_id uuid NOT NULL,
    version integer NOT NULL,
    frozen_structure jsonb NOT NULL,
    published_by uuid,
    published_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT test_versions_frozen_structure_check CHECK ((jsonb_typeof(frozen_structure) = 'object'::text)),
    CONSTRAINT test_versions_version_check CHECK ((version > 0))
);


--
-- Name: tests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    title character varying(250) NOT NULL,
    description text,
    scope character varying(16) NOT NULL,
    department_id uuid,
    status character varying(16) DEFAULT 'draft'::character varying NOT NULL,
    duration_minutes integer,
    version integer DEFAULT 1 NOT NULL,
    passing_percent numeric(5,2) DEFAULT 70 NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_test_scope CHECK (((((scope)::text = 'general'::text) AND (department_id IS NULL)) OR (((scope)::text = 'department'::text) AND (department_id IS NOT NULL)))),
    CONSTRAINT tests_duration_minutes_check CHECK ((duration_minutes > 0)),
    CONSTRAINT tests_passing_percent_check CHECK (((passing_percent >= (0)::numeric) AND (passing_percent <= (100)::numeric))),
    CONSTRAINT tests_scope_check CHECK (((scope)::text = ANY (ARRAY[('general'::character varying)::text, ('department'::character varying)::text]))),
    CONSTRAINT tests_status_check CHECK (((status)::text = ANY (ARRAY[('draft'::character varying)::text, ('published'::character varying)::text, ('archived'::character varying)::text]))),
    CONSTRAINT tests_version_check CHECK ((version > 0))
);


--
-- Name: user_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_groups (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(120) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_roles (
    user_id uuid NOT NULL,
    role_id uuid NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    department_id uuid,
    email character varying(320) NOT NULL,
    username character varying(80) NOT NULL,
    display_name character varying(160) NOT NULL,
    full_name character varying(255),
    password_hash text,
    avatar_url text,
    external_subject character varying(255),
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: v_department_competency_summary; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_department_competency_summary AS
 SELECT u.department_id,
    ec.competency_id,
    ec.current_level,
    count(*) AS employee_count
   FROM (public.employee_competencies ec
     JOIN public.users u ON ((u.id = ec.user_id)))
  WHERE ((u.department_id IS NOT NULL) AND u.is_active)
  GROUP BY u.department_id, ec.competency_id, ec.current_level;


--
-- Name: v_employee_errors; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_employee_errors AS
 SELECT a.user_id,
    a.test_id,
    t.id AS attempt_id,
    aa.question_id,
    q.competency_id,
    aa.answered_at,
    aa.awarded_points
   FROM (((public.attempt_answers aa
     JOIN public.test_attempts t ON ((t.id = aa.attempt_id)))
     JOIN public.test_assignments a ON ((a.id = t.assignment_id)))
     JOIN public.questions q ON ((q.id = aa.question_id)))
  WHERE (aa.is_correct IS FALSE);


--
-- Name: v_test_results; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_test_results AS
 SELECT a.user_id,
    a.test_id,
    t.id AS attempt_id,
    t.status,
    t.started_at,
    t.finished_at,
    t.score_percent,
        CASE
            WHEN (t.score_percent IS NULL) THEN NULL::boolean
            ELSE (t.score_percent >= tests.passing_percent)
        END AS passed
   FROM ((public.test_attempts t
     JOIN public.test_assignments a ON ((a.id = t.assignment_id)))
     JOIN public.tests ON ((tests.id = a.test_id)));


--
-- Name: video_uploads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.video_uploads (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    uploaded_by uuid NOT NULL,
    page_id uuid,
    filename text NOT NULL,
    storage_name text NOT NULL,
    media_type character varying(32) NOT NULL,
    byte_size bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT video_storage_name_format CHECK ((storage_name ~ '^[0-9a-f]{32}\.(mp4|webm)$'::text)),
    CONSTRAINT video_uploads_byte_size_check CHECK (((byte_size > 0) AND (byte_size <= 524288000))),
    CONSTRAINT video_uploads_media_type_check CHECK (((media_type)::text = ANY ((ARRAY['video/mp4'::character varying, 'video/webm'::character varying])::text[])))
);


--
-- Name: answer_options answer_options_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.answer_options
    ADD CONSTRAINT answer_options_pkey PRIMARY KEY (id);


--
-- Name: answer_options answer_options_question_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.answer_options
    ADD CONSTRAINT answer_options_question_id_id_key UNIQUE (question_id, id);


--
-- Name: answer_options answer_options_question_id_position_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.answer_options
    ADD CONSTRAINT answer_options_question_id_position_key UNIQUE (question_id, "position");


--
-- Name: assessment_cooldowns assessment_cooldowns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assessment_cooldowns
    ADD CONSTRAINT assessment_cooldowns_pkey PRIMARY KEY (user_id, competency_id);


--
-- Name: attachment_chunks attachment_chunks_attachment_id_byte_offset_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachment_chunks
    ADD CONSTRAINT attachment_chunks_attachment_id_byte_offset_key UNIQUE (attachment_id, byte_offset);


--
-- Name: attachment_chunks attachment_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachment_chunks
    ADD CONSTRAINT attachment_chunks_pkey PRIMARY KEY (attachment_id, chunk_no);


--
-- Name: attachments attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_pkey PRIMARY KEY (id);


--
-- Name: attempt_answers attempt_answers_attempt_id_question_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_answers
    ADD CONSTRAINT attempt_answers_attempt_id_question_id_key UNIQUE (attempt_id, question_id);


--
-- Name: attempt_answers attempt_answers_id_question_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_answers
    ADD CONSTRAINT attempt_answers_id_question_id_key UNIQUE (id, question_id);


--
-- Name: attempt_answers attempt_answers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_answers
    ADD CONSTRAINT attempt_answers_pkey PRIMARY KEY (id);


--
-- Name: attempt_competencies attempt_competencies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_competencies
    ADD CONSTRAINT attempt_competencies_pkey PRIMARY KEY (attempt_id, competency_id);


--
-- Name: attempt_questions attempt_questions_attempt_id_position_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_questions
    ADD CONSTRAINT attempt_questions_attempt_id_position_key UNIQUE (attempt_id, "position");


--
-- Name: attempt_questions attempt_questions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_questions
    ADD CONSTRAINT attempt_questions_pkey PRIMARY KEY (attempt_id, question_id);


--
-- Name: attempt_selected_options attempt_selected_options_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_selected_options
    ADD CONSTRAINT attempt_selected_options_pkey PRIMARY KEY (attempt_answer_id, option_id);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: comments comments_page_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT comments_page_id_id_key UNIQUE (page_id, id);


--
-- Name: comments comments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT comments_pkey PRIMARY KEY (id);


--
-- Name: competencies competencies_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competencies
    ADD CONSTRAINT competencies_code_key UNIQUE (code);


--
-- Name: competencies competencies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competencies
    ADD CONSTRAINT competencies_pkey PRIMARY KEY (id);


--
-- Name: competency_assessment_events competency_assessment_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_assessment_events
    ADD CONSTRAINT competency_assessment_events_pkey PRIMARY KEY (id);


--
-- Name: competency_assessments competency_assessments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_assessments
    ADD CONSTRAINT competency_assessments_pkey PRIMARY KEY (id);


--
-- Name: competency_categories competency_categories_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_categories
    ADD CONSTRAINT competency_categories_code_key UNIQUE (code);


--
-- Name: competency_categories competency_categories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_categories
    ADD CONSTRAINT competency_categories_pkey PRIMARY KEY (id);


--
-- Name: competency_labels competency_labels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_labels
    ADD CONSTRAINT competency_labels_pkey PRIMARY KEY (competency_id, label_id);


--
-- Name: competency_profiles competency_profiles_department_id_name_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_profiles
    ADD CONSTRAINT competency_profiles_department_id_name_version_key UNIQUE (department_id, name, version);


--
-- Name: competency_profiles competency_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_profiles
    ADD CONSTRAINT competency_profiles_pkey PRIMARY KEY (id);


--
-- Name: competency_scales competency_scales_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_scales
    ADD CONSTRAINT competency_scales_pkey PRIMARY KEY (id);


--
-- Name: competency_scores competency_scores_assessment_id_competency_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_scores
    ADD CONSTRAINT competency_scores_assessment_id_competency_id_key UNIQUE (assessment_id, competency_id);


--
-- Name: competency_scores competency_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_scores
    ADD CONSTRAINT competency_scores_pkey PRIMARY KEY (id);


--
-- Name: department_kk_responsibles department_kk_responsibles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.department_kk_responsibles
    ADD CONSTRAINT department_kk_responsibles_pkey PRIMARY KEY (department_id, user_id);


--
-- Name: departments departments_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.departments
    ADD CONSTRAINT departments_code_key UNIQUE (code);


--
-- Name: departments departments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.departments
    ADD CONSTRAINT departments_pkey PRIMARY KEY (id);


--
-- Name: employee_additional_competencies employee_additional_competencies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_additional_competencies
    ADD CONSTRAINT employee_additional_competencies_pkey PRIMARY KEY (user_id, competency_id);


--
-- Name: employee_competencies employee_competencies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_competencies
    ADD CONSTRAINT employee_competencies_pkey PRIMARY KEY (user_id, competency_id);


--
-- Name: group_members group_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_members
    ADD CONSTRAINT group_members_pkey PRIMARY KEY (group_id, user_id);


--
-- Name: interview_competencies interview_competencies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interview_competencies
    ADD CONSTRAINT interview_competencies_pkey PRIMARY KEY (interview_id, competency_id);


--
-- Name: interview_ratings interview_ratings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interview_ratings
    ADD CONSTRAINT interview_ratings_pkey PRIMARY KEY (interview_id, competency_id);


--
-- Name: interviews interviews_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interviews
    ADD CONSTRAINT interviews_pkey PRIMARY KEY (id);


--
-- Name: labels labels_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.labels
    ADD CONSTRAINT labels_name_key UNIQUE (name);


--
-- Name: labels labels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.labels
    ADD CONSTRAINT labels_pkey PRIMARY KEY (id);


--
-- Name: login_rate_limits login_rate_limits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.login_rate_limits
    ADD CONSTRAINT login_rate_limits_pkey PRIMARY KEY (scope, key_hash);


--
-- Name: page_approvals page_approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_approvals
    ADD CONSTRAINT page_approvals_pkey PRIMARY KEY (id);


--
-- Name: page_favorites page_favorites_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_favorites
    ADD CONSTRAINT page_favorites_pkey PRIMARY KEY (page_id, user_id);


--
-- Name: page_labels page_labels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_labels
    ADD CONSTRAINT page_labels_pkey PRIMARY KEY (page_id, label_id);


--
-- Name: page_links page_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_links
    ADD CONSTRAINT page_links_pkey PRIMARY KEY (source_page_id, target_page_id);


--
-- Name: page_permissions page_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_permissions
    ADD CONSTRAINT page_permissions_pkey PRIMARY KEY (id);


--
-- Name: page_reactions page_reactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_reactions
    ADD CONSTRAINT page_reactions_pkey PRIMARY KEY (page_id, user_id, reaction);


--
-- Name: page_revisions page_revisions_page_id_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_revisions
    ADD CONSTRAINT page_revisions_page_id_version_key UNIQUE (page_id, version);


--
-- Name: page_revisions page_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_revisions
    ADD CONSTRAINT page_revisions_pkey PRIMARY KEY (id);


--
-- Name: pages pages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pages
    ADD CONSTRAINT pages_pkey PRIMARY KEY (id);


--
-- Name: pages pages_space_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pages
    ADD CONSTRAINT pages_space_id_id_key UNIQUE (space_id, id);


--
-- Name: profile_competencies profile_competencies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profile_competencies
    ADD CONSTRAINT profile_competencies_pkey PRIMARY KEY (profile_id, competency_id);


--
-- Name: question_import_items question_import_items_import_id_ordinal_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_import_items
    ADD CONSTRAINT question_import_items_import_id_ordinal_key UNIQUE (import_id, ordinal);


--
-- Name: question_import_items question_import_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_import_items
    ADD CONSTRAINT question_import_items_pkey PRIMARY KEY (id);


--
-- Name: question_imports question_imports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_imports
    ADD CONSTRAINT question_imports_pkey PRIMARY KEY (id);


--
-- Name: question_revisions question_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_revisions
    ADD CONSTRAINT question_revisions_pkey PRIMARY KEY (id);


--
-- Name: question_revisions question_revisions_question_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_revisions
    ADD CONSTRAINT question_revisions_question_id_id_key UNIQUE (question_id, id);


--
-- Name: question_revisions question_revisions_question_id_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_revisions
    ADD CONSTRAINT question_revisions_question_id_version_key UNIQUE (question_id, version);


--
-- Name: questions questions_import_item_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT questions_import_item_id_key UNIQUE (import_item_id);


--
-- Name: questions questions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT questions_pkey PRIMARY KEY (id);


--
-- Name: roles roles_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_code_key UNIQUE (code);


--
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (token);


--
-- Name: space_members space_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.space_members
    ADD CONSTRAINT space_members_pkey PRIMARY KEY (space_id, user_id);


--
-- Name: spaces spaces_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.spaces
    ADD CONSTRAINT spaces_pkey PRIMARY KEY (id);


--
-- Name: spaces spaces_space_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.spaces
    ADD CONSTRAINT spaces_space_key_key UNIQUE (space_key);


--
-- Name: templates templates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.templates
    ADD CONSTRAINT templates_pkey PRIMARY KEY (id);


--
-- Name: test_assignments test_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_assignments
    ADD CONSTRAINT test_assignments_pkey PRIMARY KEY (id);


--
-- Name: test_attempts test_attempts_assignment_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_attempts
    ADD CONSTRAINT test_attempts_assignment_id_key UNIQUE (assignment_id);


--
-- Name: test_attempts test_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_attempts
    ADD CONSTRAINT test_attempts_pkey PRIMARY KEY (id);


--
-- Name: test_block_competencies test_block_competencies_block_id_position_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_block_competencies
    ADD CONSTRAINT test_block_competencies_block_id_position_key UNIQUE (block_id, "position");


--
-- Name: test_block_competencies test_block_competencies_id_competency_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_block_competencies
    ADD CONSTRAINT test_block_competencies_id_competency_id_key UNIQUE (id, competency_id);


--
-- Name: test_block_competencies test_block_competencies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_block_competencies
    ADD CONSTRAINT test_block_competencies_pkey PRIMARY KEY (id);


--
-- Name: test_block_competencies test_block_competencies_test_id_competency_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_block_competencies
    ADD CONSTRAINT test_block_competencies_test_id_competency_id_key UNIQUE (test_id, competency_id);


--
-- Name: test_block_competencies test_block_competencies_test_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_block_competencies
    ADD CONSTRAINT test_block_competencies_test_id_id_key UNIQUE (test_id, id);


--
-- Name: test_blocks test_blocks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_blocks
    ADD CONSTRAINT test_blocks_pkey PRIMARY KEY (id);


--
-- Name: test_blocks test_blocks_test_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_blocks
    ADD CONSTRAINT test_blocks_test_id_id_key UNIQUE (test_id, id);


--
-- Name: test_blocks test_blocks_test_id_position_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_blocks
    ADD CONSTRAINT test_blocks_test_id_position_key UNIQUE (test_id, "position");


--
-- Name: test_competency_reservations test_competency_reservations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_competency_reservations
    ADD CONSTRAINT test_competency_reservations_pkey PRIMARY KEY (attempt_id, competency_id);


--
-- Name: test_competency_results test_competency_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_competency_results
    ADD CONSTRAINT test_competency_results_pkey PRIMARY KEY (attempt_id, competency_id);


--
-- Name: test_questions test_questions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_questions
    ADD CONSTRAINT test_questions_pkey PRIMARY KEY (test_id, question_id);


--
-- Name: test_questions test_questions_test_id_position_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_questions
    ADD CONSTRAINT test_questions_test_id_position_key UNIQUE (test_id, "position");


--
-- Name: test_versions test_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_versions
    ADD CONSTRAINT test_versions_pkey PRIMARY KEY (id);


--
-- Name: test_versions test_versions_test_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_versions
    ADD CONSTRAINT test_versions_test_id_id_key UNIQUE (test_id, id);


--
-- Name: test_versions test_versions_test_id_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_versions
    ADD CONSTRAINT test_versions_test_id_version_key UNIQUE (test_id, version);


--
-- Name: tests tests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tests
    ADD CONSTRAINT tests_pkey PRIMARY KEY (id);


--
-- Name: questions uq_questions_id_comp; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT uq_questions_id_comp UNIQUE (id, competency_id);


--
-- Name: user_groups user_groups_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_groups
    ADD CONSTRAINT user_groups_name_key UNIQUE (name);


--
-- Name: user_groups user_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_groups
    ADD CONSTRAINT user_groups_pkey PRIMARY KEY (id);


--
-- Name: user_roles user_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_pkey PRIMARY KEY (user_id, role_id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_external_subject_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_external_subject_key UNIQUE (external_subject);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: video_uploads video_uploads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_uploads
    ADD CONSTRAINT video_uploads_pkey PRIMARY KEY (id);


--
-- Name: video_uploads video_uploads_storage_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_uploads
    ADD CONSTRAINT video_uploads_storage_name_key UNIQUE (storage_name);


--
-- Name: ix_additional_competencies_comp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_additional_competencies_comp ON public.employee_additional_competencies USING btree (competency_id);


--
-- Name: ix_attachment_chunks_range; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_attachment_chunks_range ON public.attachment_chunks USING btree (attachment_id, byte_offset);


--
-- Name: ix_attempt_answers_wrong; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_attempt_answers_wrong ON public.attempt_answers USING btree (attempt_id) WHERE (is_correct IS FALSE);


--
-- Name: ix_attempt_questions_comp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_attempt_questions_comp ON public.attempt_questions USING btree (attempt_id, competency_id);


--
-- Name: ix_audit_log_actor_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_log_actor_time ON public.audit_log USING btree (actor_id, occurred_at DESC);


--
-- Name: ix_audit_log_object_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_log_object_time ON public.audit_log USING btree (object_type, object_id, occurred_at DESC);


--
-- Name: ix_competencies_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_competencies_category ON public.competencies USING btree (category_id);


--
-- Name: ix_departments_manager; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_departments_manager ON public.departments USING btree (manager_id);


--
-- Name: ix_departments_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_departments_parent ON public.departments USING btree (parent_id);


--
-- Name: ix_employee_competencies_comp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_employee_competencies_comp ON public.employee_competencies USING btree (competency_id);


--
-- Name: ix_events_attempt; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_attempt ON public.competency_assessment_events USING btree (test_attempt_id) WHERE (test_attempt_id IS NOT NULL);


--
-- Name: ix_events_interview; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_interview ON public.competency_assessment_events USING btree (interview_id) WHERE (interview_id IS NOT NULL);


--
-- Name: ix_events_user_comp_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_user_comp_date ON public.competency_assessment_events USING btree (user_id, competency_id, assessed_at DESC);


--
-- Name: ix_interviews_employee; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interviews_employee ON public.interviews USING btree (employee_id, scheduled_at DESC);


--
-- Name: ix_interviews_interviewer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interviews_interviewer ON public.interviews USING btree (interviewer_id, scheduled_at DESC);


--
-- Name: ix_labels_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_labels_name ON public.labels USING btree (name);


--
-- Name: ix_login_rate_limits_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_login_rate_limits_started ON public.login_rate_limits USING btree (window_started);


--
-- Name: ix_page_labels_label; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_page_labels_label ON public.page_labels USING btree (label_id);


--
-- Name: ix_page_permissions_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_page_permissions_group ON public.page_permissions USING btree (group_id) WHERE (group_id IS NOT NULL);


--
-- Name: ix_page_permissions_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_page_permissions_user ON public.page_permissions USING btree (user_id) WHERE (user_id IS NOT NULL);


--
-- Name: ix_page_revisions_page; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_page_revisions_page ON public.page_revisions USING btree (page_id, created_at DESC);


--
-- Name: ix_pages_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pages_parent ON public.pages USING btree (space_id, parent_id, "position");


--
-- Name: ix_pages_title_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pages_title_trgm ON public.pages USING gin (title public.gin_trgm_ops);


--
-- Name: ix_profile_competencies_comp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_profile_competencies_comp ON public.profile_competencies USING btree (competency_id);


--
-- Name: ix_question_import_items_import; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_question_import_items_import ON public.question_import_items USING btree (import_id, status);


--
-- Name: ix_question_import_items_external; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_question_import_items_external ON public.question_import_items USING btree (import_id, external_id);


--
-- Name: ix_question_imports_department_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_question_imports_department_created ON public.question_imports USING btree (department_id, created_at DESC, id DESC);


--
-- Name: ix_question_revisions_question; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_question_revisions_question ON public.question_revisions USING btree (question_id, version DESC);


--
-- Name: ix_questions_comp_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_questions_comp_status ON public.questions USING btree (competency_id, moderation_status);


--
-- Name: ix_sessions_expiry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sessions_expiry ON public.sessions USING btree (expires_at);


--
-- Name: ix_tbc_competency; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tbc_competency ON public.test_block_competencies USING btree (competency_id);


--
-- Name: ix_test_assignments_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_test_assignments_user ON public.test_assignments USING btree (user_id, assigned_at DESC);


--
-- Name: ix_test_attempts_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_test_attempts_status ON public.test_attempts USING btree (status, started_at DESC);


--
-- Name: ix_test_blocks_test; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_test_blocks_test ON public.test_blocks USING btree (test_id, "position");


--
-- Name: ix_tq_block_comp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tq_block_comp ON public.test_questions USING btree (block_competency_id);


--
-- Name: ix_users_department; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_department ON public.users USING btree (department_id, is_active);


--
-- Name: ix_video_uploads_owner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_uploads_owner ON public.video_uploads USING btree (uploaded_by);


--
-- Name: ix_video_uploads_page; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_video_uploads_page ON public.video_uploads USING btree (page_id);


--
-- Name: ux_department_active_profile; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_department_active_profile ON public.competency_profiles USING btree (department_id) WHERE is_active;


--
-- Name: ux_test_active_reservation; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_test_active_reservation ON public.test_competency_reservations USING btree (user_id, competency_id) WHERE (released_at IS NULL);


--
-- Name: answer_options answer_options_question_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.answer_options
    ADD CONSTRAINT answer_options_question_id_fkey FOREIGN KEY (question_id) REFERENCES public.questions(id) ON DELETE CASCADE;


--
-- Name: assessment_cooldowns assessment_cooldowns_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assessment_cooldowns
    ADD CONSTRAINT assessment_cooldowns_competency_id_fkey FOREIGN KEY (competency_id) REFERENCES public.competencies(id) ON DELETE RESTRICT;


--
-- Name: assessment_cooldowns assessment_cooldowns_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assessment_cooldowns
    ADD CONSTRAINT assessment_cooldowns_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: attachment_chunks attachment_chunks_attachment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachment_chunks
    ADD CONSTRAINT attachment_chunks_attachment_id_fkey FOREIGN KEY (attachment_id) REFERENCES public.attachments(id) ON DELETE CASCADE;


--
-- Name: attachments attachments_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_page_id_fkey FOREIGN KEY (page_id) REFERENCES public.pages(id) ON DELETE CASCADE;


--
-- Name: attachments attachments_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT attachments_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: attempt_answers attempt_answers_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_answers
    ADD CONSTRAINT attempt_answers_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.test_attempts(id) ON DELETE RESTRICT;


--
-- Name: attempt_answers attempt_answers_question_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_answers
    ADD CONSTRAINT attempt_answers_question_id_fkey FOREIGN KEY (question_id) REFERENCES public.questions(id) ON DELETE RESTRICT;


--
-- Name: attempt_competencies attempt_competencies_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_competencies
    ADD CONSTRAINT attempt_competencies_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.test_attempts(id) ON DELETE CASCADE;


--
-- Name: attempt_competencies attempt_competencies_block_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_competencies
    ADD CONSTRAINT attempt_competencies_block_id_fkey FOREIGN KEY (block_id) REFERENCES public.test_blocks(id) ON DELETE RESTRICT;


--
-- Name: attempt_competencies attempt_competencies_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_competencies
    ADD CONSTRAINT attempt_competencies_competency_id_fkey FOREIGN KEY (competency_id) REFERENCES public.competencies(id) ON DELETE RESTRICT;


--
-- Name: attempt_questions attempt_questions_attempt_id_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_questions
    ADD CONSTRAINT attempt_questions_attempt_id_competency_id_fkey FOREIGN KEY (attempt_id, competency_id) REFERENCES public.attempt_competencies(attempt_id, competency_id) ON DELETE RESTRICT;


--
-- Name: attempt_questions attempt_questions_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_questions
    ADD CONSTRAINT attempt_questions_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.test_attempts(id) ON DELETE CASCADE;


--
-- Name: attempt_questions attempt_questions_question_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_questions
    ADD CONSTRAINT attempt_questions_question_id_fkey FOREIGN KEY (question_id) REFERENCES public.questions(id) ON DELETE RESTRICT;


--
-- Name: attempt_questions attempt_questions_question_id_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_questions
    ADD CONSTRAINT attempt_questions_question_id_revision_id_fkey FOREIGN KEY (question_id, revision_id) REFERENCES public.question_revisions(question_id, id) ON DELETE RESTRICT;


--
-- Name: attempt_selected_options attempt_selected_options_attempt_answer_id_question_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_selected_options
    ADD CONSTRAINT attempt_selected_options_attempt_answer_id_question_id_fkey FOREIGN KEY (attempt_answer_id, question_id) REFERENCES public.attempt_answers(id, question_id) ON DELETE CASCADE;


--
-- Name: attempt_selected_options attempt_selected_options_question_id_option_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_selected_options
    ADD CONSTRAINT attempt_selected_options_question_id_option_id_fkey FOREIGN KEY (question_id, option_id) REFERENCES public.answer_options(question_id, id) ON DELETE RESTRICT;


--
-- Name: audit_log audit_log_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: comments comments_author_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT comments_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: comments comments_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT comments_page_id_fkey FOREIGN KEY (page_id) REFERENCES public.pages(id) ON DELETE CASCADE;


--
-- Name: comments comments_page_id_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT comments_page_id_parent_id_fkey FOREIGN KEY (page_id, parent_id) REFERENCES public.comments(page_id, id) ON DELETE CASCADE;


--
-- Name: competencies competencies_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competencies
    ADD CONSTRAINT competencies_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.competency_categories(id) ON DELETE RESTRICT;


--
-- Name: competency_assessment_events competency_assessment_events_assessed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_assessment_events
    ADD CONSTRAINT competency_assessment_events_assessed_by_fkey FOREIGN KEY (assessed_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: competency_assessment_events competency_assessment_events_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_assessment_events
    ADD CONSTRAINT competency_assessment_events_competency_id_fkey FOREIGN KEY (competency_id) REFERENCES public.competencies(id) ON DELETE RESTRICT;


--
-- Name: competency_assessment_events competency_assessment_events_interview_id_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_assessment_events
    ADD CONSTRAINT competency_assessment_events_interview_id_competency_id_fkey FOREIGN KEY (interview_id, competency_id) REFERENCES public.interview_ratings(interview_id, competency_id) ON DELETE RESTRICT;


--
-- Name: competency_assessment_events competency_assessment_events_previous_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_assessment_events
    ADD CONSTRAINT competency_assessment_events_previous_event_id_fkey FOREIGN KEY (previous_event_id) REFERENCES public.competency_assessment_events(id) ON DELETE RESTRICT;


--
-- Name: competency_assessment_events competency_assessment_events_test_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_assessment_events
    ADD CONSTRAINT competency_assessment_events_test_attempt_id_fkey FOREIGN KEY (test_attempt_id) REFERENCES public.test_attempts(id) ON DELETE RESTRICT;


--
-- Name: competency_assessment_events competency_assessment_events_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_assessment_events
    ADD CONSTRAINT competency_assessment_events_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: competency_assessments competency_assessments_evaluator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_assessments
    ADD CONSTRAINT competency_assessments_evaluator_id_fkey FOREIGN KEY (evaluator_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: competency_assessments competency_assessments_scale_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_assessments
    ADD CONSTRAINT competency_assessments_scale_id_fkey FOREIGN KEY (scale_id) REFERENCES public.competency_scales(id) ON DELETE RESTRICT;


--
-- Name: competency_assessments competency_assessments_specialist_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_assessments
    ADD CONSTRAINT competency_assessments_specialist_id_fkey FOREIGN KEY (specialist_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: competency_categories competency_categories_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_categories
    ADD CONSTRAINT competency_categories_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.competency_categories(id) ON DELETE RESTRICT;


--
-- Name: competency_labels competency_labels_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_labels
    ADD CONSTRAINT competency_labels_competency_id_fkey FOREIGN KEY (competency_id) REFERENCES public.competencies(id) ON DELETE CASCADE;


--
-- Name: competency_labels competency_labels_label_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_labels
    ADD CONSTRAINT competency_labels_label_id_fkey FOREIGN KEY (label_id) REFERENCES public.labels(id) ON DELETE CASCADE;


--
-- Name: competency_profiles competency_profiles_approved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_profiles
    ADD CONSTRAINT competency_profiles_approved_by_fkey FOREIGN KEY (approved_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: competency_profiles competency_profiles_department_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_profiles
    ADD CONSTRAINT competency_profiles_department_id_fkey FOREIGN KEY (department_id) REFERENCES public.departments(id) ON DELETE RESTRICT;


--
-- Name: competency_profiles competency_profiles_prepared_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_profiles
    ADD CONSTRAINT competency_profiles_prepared_by_fkey FOREIGN KEY (prepared_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: competency_scores competency_scores_assessment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_scores
    ADD CONSTRAINT competency_scores_assessment_id_fkey FOREIGN KEY (assessment_id) REFERENCES public.competency_assessments(id) ON DELETE CASCADE;


--
-- Name: competency_scores competency_scores_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.competency_scores
    ADD CONSTRAINT competency_scores_competency_id_fkey FOREIGN KEY (competency_id) REFERENCES public.competencies(id) ON DELETE RESTRICT;


--
-- Name: department_kk_responsibles department_kk_responsibles_appointed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.department_kk_responsibles
    ADD CONSTRAINT department_kk_responsibles_appointed_by_fkey FOREIGN KEY (appointed_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: department_kk_responsibles department_kk_responsibles_department_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.department_kk_responsibles
    ADD CONSTRAINT department_kk_responsibles_department_id_fkey FOREIGN KEY (department_id) REFERENCES public.departments(id) ON DELETE CASCADE;


--
-- Name: department_kk_responsibles department_kk_responsibles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.department_kk_responsibles
    ADD CONSTRAINT department_kk_responsibles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: departments departments_manager_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.departments
    ADD CONSTRAINT departments_manager_id_fkey FOREIGN KEY (manager_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: departments departments_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.departments
    ADD CONSTRAINT departments_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.departments(id) ON DELETE RESTRICT;


--
-- Name: employee_additional_competencies employee_additional_competencies_added_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_additional_competencies
    ADD CONSTRAINT employee_additional_competencies_added_by_fkey FOREIGN KEY (added_by) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: employee_additional_competencies employee_additional_competencies_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_additional_competencies
    ADD CONSTRAINT employee_additional_competencies_competency_id_fkey FOREIGN KEY (competency_id) REFERENCES public.competencies(id) ON DELETE RESTRICT;


--
-- Name: employee_additional_competencies employee_additional_competencies_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_additional_competencies
    ADD CONSTRAINT employee_additional_competencies_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: employee_competencies employee_competencies_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_competencies
    ADD CONSTRAINT employee_competencies_competency_id_fkey FOREIGN KEY (competency_id) REFERENCES public.competencies(id) ON DELETE RESTRICT;


--
-- Name: employee_competencies employee_competencies_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_competencies
    ADD CONSTRAINT employee_competencies_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: employee_competencies employee_competencies_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_competencies
    ADD CONSTRAINT employee_competencies_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: attempt_answers fk_answer_attempt_question; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attempt_answers
    ADD CONSTRAINT fk_answer_attempt_question FOREIGN KEY (attempt_id, question_id) REFERENCES public.attempt_questions(attempt_id, question_id) ON DELETE RESTRICT;


--
-- Name: interview_ratings fk_interview_selected_competency; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interview_ratings
    ADD CONSTRAINT fk_interview_selected_competency FOREIGN KEY (interview_id, competency_id) REFERENCES public.interview_competencies(interview_id, competency_id) ON DELETE RESTRICT;


--
-- Name: questions fk_questions_import_item; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT fk_questions_import_item FOREIGN KEY (import_item_id) REFERENCES public.question_import_items(id) ON DELETE SET NULL;


--
-- Name: test_assignments fk_test_assignment_version; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_assignments
    ADD CONSTRAINT fk_test_assignment_version FOREIGN KEY (test_id, test_version) REFERENCES public.test_versions(test_id, version) ON DELETE RESTRICT;


--
-- Name: test_questions fk_test_questions_block_competency; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_questions
    ADD CONSTRAINT fk_test_questions_block_competency FOREIGN KEY (test_id, block_competency_id) REFERENCES public.test_block_competencies(test_id, id) ON DELETE CASCADE;


--
-- Name: test_questions fk_tq_block_comp; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_questions
    ADD CONSTRAINT fk_tq_block_comp FOREIGN KEY (block_competency_id, competency_id) REFERENCES public.test_block_competencies(id, competency_id) ON DELETE RESTRICT;


--
-- Name: test_questions fk_tq_question_comp; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_questions
    ADD CONSTRAINT fk_tq_question_comp FOREIGN KEY (question_id, competency_id) REFERENCES public.questions(id, competency_id) ON DELETE RESTRICT;


--
-- Name: group_members group_members_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_members
    ADD CONSTRAINT group_members_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.user_groups(id) ON DELETE CASCADE;


--
-- Name: group_members group_members_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.group_members
    ADD CONSTRAINT group_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: interview_competencies interview_competencies_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interview_competencies
    ADD CONSTRAINT interview_competencies_competency_id_fkey FOREIGN KEY (competency_id) REFERENCES public.competencies(id) ON DELETE RESTRICT;


--
-- Name: interview_competencies interview_competencies_interview_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interview_competencies
    ADD CONSTRAINT interview_competencies_interview_id_fkey FOREIGN KEY (interview_id) REFERENCES public.interviews(id) ON DELETE CASCADE;


--
-- Name: interview_ratings interview_ratings_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interview_ratings
    ADD CONSTRAINT interview_ratings_competency_id_fkey FOREIGN KEY (competency_id) REFERENCES public.competencies(id) ON DELETE RESTRICT;


--
-- Name: interview_ratings interview_ratings_interview_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interview_ratings
    ADD CONSTRAINT interview_ratings_interview_id_fkey FOREIGN KEY (interview_id) REFERENCES public.interviews(id) ON DELETE CASCADE;


--
-- Name: interviews interviews_assigned_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interviews
    ADD CONSTRAINT interviews_assigned_by_fkey FOREIGN KEY (assigned_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: interviews interviews_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interviews
    ADD CONSTRAINT interviews_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: interviews interviews_interviewer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interviews
    ADD CONSTRAINT interviews_interviewer_id_fkey FOREIGN KEY (interviewer_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: page_approvals page_approvals_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_approvals
    ADD CONSTRAINT page_approvals_page_id_fkey FOREIGN KEY (page_id) REFERENCES public.pages(id) ON DELETE CASCADE;


--
-- Name: page_approvals page_approvals_reviewed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_approvals
    ADD CONSTRAINT page_approvals_reviewed_by_fkey FOREIGN KEY (reviewed_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: page_approvals page_approvals_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_approvals
    ADD CONSTRAINT page_approvals_revision_id_fkey FOREIGN KEY (revision_id) REFERENCES public.page_revisions(id) ON DELETE RESTRICT;


--
-- Name: page_approvals page_approvals_submitted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_approvals
    ADD CONSTRAINT page_approvals_submitted_by_fkey FOREIGN KEY (submitted_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: page_favorites page_favorites_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_favorites
    ADD CONSTRAINT page_favorites_page_id_fkey FOREIGN KEY (page_id) REFERENCES public.pages(id) ON DELETE CASCADE;


--
-- Name: page_favorites page_favorites_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_favorites
    ADD CONSTRAINT page_favorites_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: page_labels page_labels_label_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_labels
    ADD CONSTRAINT page_labels_label_id_fkey FOREIGN KEY (label_id) REFERENCES public.labels(id) ON DELETE CASCADE;


--
-- Name: page_labels page_labels_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_labels
    ADD CONSTRAINT page_labels_page_id_fkey FOREIGN KEY (page_id) REFERENCES public.pages(id) ON DELETE CASCADE;


--
-- Name: page_links page_links_source_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_links
    ADD CONSTRAINT page_links_source_page_id_fkey FOREIGN KEY (source_page_id) REFERENCES public.pages(id) ON DELETE CASCADE;


--
-- Name: page_links page_links_target_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_links
    ADD CONSTRAINT page_links_target_page_id_fkey FOREIGN KEY (target_page_id) REFERENCES public.pages(id) ON DELETE CASCADE;


--
-- Name: page_permissions page_permissions_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_permissions
    ADD CONSTRAINT page_permissions_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.user_groups(id) ON DELETE CASCADE;


--
-- Name: page_permissions page_permissions_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_permissions
    ADD CONSTRAINT page_permissions_page_id_fkey FOREIGN KEY (page_id) REFERENCES public.pages(id) ON DELETE CASCADE;


--
-- Name: page_permissions page_permissions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_permissions
    ADD CONSTRAINT page_permissions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: page_reactions page_reactions_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_reactions
    ADD CONSTRAINT page_reactions_page_id_fkey FOREIGN KEY (page_id) REFERENCES public.pages(id) ON DELETE CASCADE;


--
-- Name: page_reactions page_reactions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_reactions
    ADD CONSTRAINT page_reactions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: page_revisions page_revisions_editor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_revisions
    ADD CONSTRAINT page_revisions_editor_id_fkey FOREIGN KEY (editor_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: page_revisions page_revisions_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_revisions
    ADD CONSTRAINT page_revisions_page_id_fkey FOREIGN KEY (page_id) REFERENCES public.pages(id) ON DELETE CASCADE;


--
-- Name: pages pages_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pages
    ADD CONSTRAINT pages_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: pages pages_space_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pages
    ADD CONSTRAINT pages_space_id_fkey FOREIGN KEY (space_id) REFERENCES public.spaces(id) ON DELETE CASCADE;


--
-- Name: pages pages_space_id_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pages
    ADD CONSTRAINT pages_space_id_parent_id_fkey FOREIGN KEY (space_id, parent_id) REFERENCES public.pages(space_id, id) ON DELETE RESTRICT;


--
-- Name: pages pages_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pages
    ADD CONSTRAINT pages_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: profile_competencies profile_competencies_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profile_competencies
    ADD CONSTRAINT profile_competencies_competency_id_fkey FOREIGN KEY (competency_id) REFERENCES public.competencies(id) ON DELETE RESTRICT;


--
-- Name: profile_competencies profile_competencies_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profile_competencies
    ADD CONSTRAINT profile_competencies_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.competency_profiles(id) ON DELETE CASCADE;


--
-- Name: question_import_items question_import_items_import_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_import_items
    ADD CONSTRAINT question_import_items_import_id_fkey FOREIGN KEY (import_id) REFERENCES public.question_imports(id) ON DELETE RESTRICT;


--
-- Name: question_import_items question_import_items_reviewed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_import_items
    ADD CONSTRAINT question_import_items_reviewed_by_fkey FOREIGN KEY (reviewed_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: question_imports question_imports_department_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_imports
    ADD CONSTRAINT question_imports_department_id_fkey FOREIGN KEY (department_id) REFERENCES public.departments(id) ON DELETE RESTRICT;


--
-- Name: question_imports question_imports_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_imports
    ADD CONSTRAINT question_imports_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: question_revisions question_revisions_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_revisions
    ADD CONSTRAINT question_revisions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: question_revisions question_revisions_question_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.question_revisions
    ADD CONSTRAINT question_revisions_question_id_fkey FOREIGN KEY (question_id) REFERENCES public.questions(id) ON DELETE RESTRICT;


--
-- Name: questions questions_bank_department_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT questions_bank_department_id_fkey FOREIGN KEY (bank_department_id) REFERENCES public.departments(id) ON DELETE RESTRICT;


--
-- Name: questions questions_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT questions_competency_id_fkey FOREIGN KEY (competency_id) REFERENCES public.competencies(id) ON DELETE RESTRICT;


--
-- Name: questions questions_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT questions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: questions questions_reviewed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT questions_reviewed_by_fkey FOREIGN KEY (reviewed_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: questions questions_source_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT questions_source_page_id_fkey FOREIGN KEY (source_page_id) REFERENCES public.pages(id) ON DELETE SET NULL;


--
-- Name: questions questions_source_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT questions_source_revision_id_fkey FOREIGN KEY (source_revision_id) REFERENCES public.page_revisions(id) ON DELETE SET NULL;


--
-- Name: sessions sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: space_members space_members_space_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.space_members
    ADD CONSTRAINT space_members_space_id_fkey FOREIGN KEY (space_id) REFERENCES public.spaces(id) ON DELETE CASCADE;


--
-- Name: space_members space_members_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.space_members
    ADD CONSTRAINT space_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: spaces spaces_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.spaces
    ADD CONSTRAINT spaces_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: templates templates_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.templates
    ADD CONSTRAINT templates_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: test_assignments test_assignments_assigned_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_assignments
    ADD CONSTRAINT test_assignments_assigned_by_fkey FOREIGN KEY (assigned_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: test_assignments test_assignments_test_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_assignments
    ADD CONSTRAINT test_assignments_test_id_fkey FOREIGN KEY (test_id) REFERENCES public.tests(id) ON DELETE RESTRICT;


--
-- Name: test_assignments test_assignments_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_assignments
    ADD CONSTRAINT test_assignments_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: test_attempts test_attempts_assignment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_attempts
    ADD CONSTRAINT test_attempts_assignment_id_fkey FOREIGN KEY (assignment_id) REFERENCES public.test_assignments(id) ON DELETE RESTRICT;


--
-- Name: test_block_competencies test_block_competencies_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_block_competencies
    ADD CONSTRAINT test_block_competencies_competency_id_fkey FOREIGN KEY (competency_id) REFERENCES public.competencies(id) ON DELETE RESTRICT;


--
-- Name: test_block_competencies test_block_competencies_test_id_block_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_block_competencies
    ADD CONSTRAINT test_block_competencies_test_id_block_id_fkey FOREIGN KEY (test_id, block_id) REFERENCES public.test_blocks(test_id, id) ON DELETE CASCADE;


--
-- Name: test_blocks test_blocks_test_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_blocks
    ADD CONSTRAINT test_blocks_test_id_fkey FOREIGN KEY (test_id) REFERENCES public.tests(id) ON DELETE CASCADE;


--
-- Name: test_competency_reservations test_competency_reservations_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_competency_reservations
    ADD CONSTRAINT test_competency_reservations_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.test_attempts(id) ON DELETE RESTRICT;


--
-- Name: test_competency_reservations test_competency_reservations_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_competency_reservations
    ADD CONSTRAINT test_competency_reservations_competency_id_fkey FOREIGN KEY (competency_id) REFERENCES public.competencies(id) ON DELETE RESTRICT;


--
-- Name: test_competency_reservations test_competency_reservations_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_competency_reservations
    ADD CONSTRAINT test_competency_reservations_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: test_competency_results test_competency_results_attempt_id_competency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_competency_results
    ADD CONSTRAINT test_competency_results_attempt_id_competency_id_fkey FOREIGN KEY (attempt_id, competency_id) REFERENCES public.attempt_competencies(attempt_id, competency_id) ON DELETE RESTRICT;


--
-- Name: test_questions test_questions_question_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_questions
    ADD CONSTRAINT test_questions_question_id_fkey FOREIGN KEY (question_id) REFERENCES public.questions(id) ON DELETE RESTRICT;


--
-- Name: test_questions test_questions_test_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_questions
    ADD CONSTRAINT test_questions_test_id_fkey FOREIGN KEY (test_id) REFERENCES public.tests(id) ON DELETE CASCADE;


--
-- Name: test_versions test_versions_published_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_versions
    ADD CONSTRAINT test_versions_published_by_fkey FOREIGN KEY (published_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: test_versions test_versions_test_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_versions
    ADD CONSTRAINT test_versions_test_id_fkey FOREIGN KEY (test_id) REFERENCES public.tests(id) ON DELETE RESTRICT;


--
-- Name: tests tests_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tests
    ADD CONSTRAINT tests_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: tests tests_department_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tests
    ADD CONSTRAINT tests_department_id_fkey FOREIGN KEY (department_id) REFERENCES public.departments(id) ON DELETE RESTRICT;


--
-- Name: user_roles user_roles_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.roles(id) ON DELETE RESTRICT;


--
-- Name: user_roles user_roles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: users users_department_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_department_id_fkey FOREIGN KEY (department_id) REFERENCES public.departments(id) ON DELETE SET NULL;


--
-- Name: video_uploads video_uploads_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_uploads
    ADD CONSTRAINT video_uploads_page_id_fkey FOREIGN KEY (page_id) REFERENCES public.pages(id) ON DELETE SET NULL;


--
-- Name: video_uploads video_uploads_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.video_uploads
    ADD CONSTRAINT video_uploads_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- PostgreSQL database dump complete
--

\unrestrict EzjBD3hgv4Zb8bpWmz9GChXRT6H2QyNYu4dkyOzUGwHnPmFvPXpGhqAqi6hMVci
