CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE roles (
    id SERIAL PRIMARY KEY,
    code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(64) NOT NULL
);

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    login VARCHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    salt VARCHAR(64) NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE user_roles (
    user_id INT REFERENCES users(id) ON DELETE CASCADE,
    role_id INT REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE groups (
    id SERIAL PRIMARY KEY,
    name VARCHAR(128) UNIQUE NOT NULL
);

CREATE TABLE group_members (
    group_id INT REFERENCES groups(id) ON DELETE CASCADE,
    user_id INT REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE spaces (
    id SERIAL PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    key VARCHAR(32) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE pages (
    id SERIAL PRIMARY KEY,
    space_id INT REFERENCES spaces(id) ON DELETE CASCADE,
    parent_id INT REFERENCES pages(id) ON DELETE SET NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    status VARCHAR(24) NOT NULL DEFAULT 'draft',
    author_id INT REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_pages_parent ON pages(parent_id);
CREATE INDEX idx_pages_space ON pages(space_id);
CREATE INDEX idx_pages_title_trgm ON pages USING gin (title gin_trgm_ops);
CREATE INDEX idx_pages_content_trgm ON pages USING gin (content gin_trgm_ops);

CREATE TABLE labels (
    id SERIAL PRIMARY KEY,
    name VARCHAR(64) UNIQUE NOT NULL
);
CREATE TABLE page_labels (
    page_id INT REFERENCES pages(id) ON DELETE CASCADE,
    label_id INT REFERENCES labels(id) ON DELETE CASCADE,
    PRIMARY KEY (page_id, label_id)
);

CREATE TABLE comments (
    id SERIAL PRIMARY KEY,
    page_id INT REFERENCES pages(id) ON DELETE CASCADE,
    parent_id INT REFERENCES comments(id) ON DELETE CASCADE,
    author_id INT REFERENCES users(id) ON DELETE SET NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    deleted BOOLEAN DEFAULT FALSE
);

CREATE TABLE attachments (
    id SERIAL PRIMARY KEY,
    page_id INT REFERENCES pages(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    mime_type VARCHAR(128) NOT NULL,
    size BIGINT NOT NULL,
    storage_path VARCHAR(512) NOT NULL,
    preview_path VARCHAR(512),
    uploaded_by INT REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE moderation_requests (
    id SERIAL PRIMARY KEY,
    page_id INT REFERENCES pages(id) ON DELETE CASCADE,
    author_id INT REFERENCES users(id),
    assigned_moderator_id INT REFERENCES users(id),
    assigned_at TIMESTAMPTZ,
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    rejection_comment TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX uq_moderation_active
    ON moderation_requests(page_id)
    WHERE status IN ('pending','in_progress');

CREATE TABLE page_history (
    id SERIAL PRIMARY KEY,
    page_id INT REFERENCES pages(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    edited_by INT REFERENCES users(id),
    version INT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE page_permissions (
    id SERIAL PRIMARY KEY,
    page_id INT REFERENCES pages(id) ON DELETE CASCADE,
    subject_type VARCHAR(16) NOT NULL,
    subject_id INT NOT NULL,
    can_read BOOLEAN DEFAULT TRUE,
    can_write BOOLEAN DEFAULT FALSE
);

CREATE TABLE audit_log (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(64) NOT NULL,
    entity VARCHAR(64),
    entity_id INT,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE sessions (
    token VARCHAR(64) PRIMARY KEY,
    user_id INT REFERENCES users(id) ON DELETE CASCADE,
    csrf_token VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
