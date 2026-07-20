"""SQLite schema version 2 for durable document import jobs."""

MIGRATION_V2 = """
CREATE TABLE IF NOT EXISTS import_jobs (
    scope_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    input_kind TEXT NOT NULL,
    input_ref TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    chunk_config_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    index_status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
    blob_id TEXT,
    source_id TEXT,
    document_id TEXT,
    version_id TEXT,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (scope_id, job_id),
    UNIQUE (
        scope_id,
        input_kind,
        input_ref,
        content_sha256,
        parser_name,
        parser_version,
        chunk_config_sha256
    ),
    FOREIGN KEY (scope_id) REFERENCES knowledge_scopes(scope_id),
    FOREIGN KEY (scope_id, blob_id)
        REFERENCES content_blobs(scope_id, blob_id),
    FOREIGN KEY (scope_id, source_id)
        REFERENCES sources(scope_id, source_id),
    FOREIGN KEY (scope_id, document_id)
        REFERENCES documents(scope_id, document_id),
    FOREIGN KEY (scope_id, version_id)
        REFERENCES document_versions(scope_id, version_id)
);

CREATE INDEX IF NOT EXISTS idx_import_jobs_status
    ON import_jobs(scope_id, status, index_status, job_id);
CREATE INDEX IF NOT EXISTS idx_import_jobs_version
    ON import_jobs(scope_id, version_id, job_id);
"""
