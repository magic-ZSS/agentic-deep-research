"""SQLite schema version 1 for knowledge and evidence metadata."""

MIGRATION_V1 = """
CREATE TABLE IF NOT EXISTS schema_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_scopes (
    scope_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    owner_user_id TEXT,
    visibility TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE (tenant_id, project_id, owner_user_id, visibility)
);

CREATE TABLE IF NOT EXISTS sources (
    scope_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    soft_deleted_at TEXT,
    payload TEXT NOT NULL,
    PRIMARY KEY (scope_id, source_id),
    UNIQUE (scope_id, kind, identity_key),
    FOREIGN KEY (scope_id) REFERENCES knowledge_scopes(scope_id)
);

CREATE TABLE IF NOT EXISTS documents (
    scope_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    logical_key TEXT NOT NULL,
    soft_deleted_at TEXT,
    payload TEXT NOT NULL,
    PRIMARY KEY (scope_id, document_id),
    UNIQUE (scope_id, source_id, logical_key),
    FOREIGN KEY (scope_id, source_id)
        REFERENCES sources(scope_id, source_id)
);

CREATE TABLE IF NOT EXISTS content_blobs (
    scope_id TEXT NOT NULL,
    blob_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    storage_ref TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (scope_id, blob_id),
    UNIQUE (scope_id, content_sha256),
    FOREIGN KEY (scope_id) REFERENCES knowledge_scopes(scope_id)
);

CREATE TABLE IF NOT EXISTS document_versions (
    scope_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    blob_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    version_number INTEGER NOT NULL CHECK (version_number >= 1),
    supersedes_version_id TEXT,
    lifecycle_status TEXT NOT NULL,
    soft_deleted_at TEXT,
    payload TEXT NOT NULL,
    PRIMARY KEY (scope_id, version_id),
    UNIQUE (scope_id, document_id, content_sha256),
    UNIQUE (scope_id, document_id, version_number),
    FOREIGN KEY (scope_id, document_id)
        REFERENCES documents(scope_id, document_id),
    FOREIGN KEY (scope_id, blob_id)
        REFERENCES content_blobs(scope_id, blob_id),
    FOREIGN KEY (scope_id, supersedes_version_id)
        REFERENCES document_versions(scope_id, version_id)
);

CREATE TABLE IF NOT EXISTS chunks (
    scope_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    text_sha256 TEXT NOT NULL,
    locator_key TEXT NOT NULL,
    soft_deleted_at TEXT,
    payload TEXT NOT NULL,
    PRIMARY KEY (scope_id, chunk_id),
    UNIQUE (scope_id, version_id, ordinal),
    FOREIGN KEY (scope_id, version_id)
        REFERENCES document_versions(scope_id, version_id)
);

CREATE TABLE IF NOT EXISTS requirements (
    scope_id TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    parent_id TEXT,
    run_id TEXT,
    status TEXT NOT NULL,
    soft_deleted_at TEXT,
    payload TEXT NOT NULL,
    PRIMARY KEY (scope_id, requirement_id),
    FOREIGN KEY (scope_id, parent_id)
        REFERENCES requirements(scope_id, requirement_id),
    FOREIGN KEY (scope_id) REFERENCES knowledge_scopes(scope_id)
);

CREATE TABLE IF NOT EXISTS evidence (
    scope_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    requirement_id TEXT,
    validation_status TEXT NOT NULL,
    soft_deleted_at TEXT,
    payload TEXT NOT NULL,
    PRIMARY KEY (scope_id, evidence_id),
    FOREIGN KEY (scope_id, chunk_id)
        REFERENCES chunks(scope_id, chunk_id),
    FOREIGN KEY (scope_id, requirement_id)
        REFERENCES requirements(scope_id, requirement_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    scope_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (scope_id, event_id),
    FOREIGN KEY (scope_id) REFERENCES knowledge_scopes(scope_id)
);

CREATE INDEX IF NOT EXISTS idx_versions_document
    ON document_versions(scope_id, document_id, version_number);
CREATE INDEX IF NOT EXISTS idx_versions_content
    ON document_versions(scope_id, content_sha256);
CREATE INDEX IF NOT EXISTS idx_chunks_version
    ON chunks(scope_id, version_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_requirements_run
    ON requirements(scope_id, run_id, requirement_id);
CREATE INDEX IF NOT EXISTS idx_evidence_requirement
    ON evidence(scope_id, requirement_id, evidence_id);
CREATE INDEX IF NOT EXISTS idx_evidence_chunk
    ON evidence(scope_id, chunk_id, evidence_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity
    ON audit_events(scope_id, entity_type, entity_id, action, created_at, event_id);
CREATE INDEX IF NOT EXISTS idx_audit_correlation
    ON audit_events(scope_id, correlation_id, created_at, event_id);
"""
