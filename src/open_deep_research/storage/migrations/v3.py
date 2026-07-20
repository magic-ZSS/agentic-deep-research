"""SQLite schema version 3 for governed lifecycle proposals."""

MIGRATION_V3 = """
CREATE TABLE IF NOT EXISTS lifecycle_proposals (
    scope_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    target_entity_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    proposed_action TEXT NOT NULL,
    status TEXT NOT NULL,
    run_id TEXT,
    correlation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (scope_id, proposal_id),
    FOREIGN KEY (scope_id) REFERENCES knowledge_scopes(scope_id)
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_proposals_status
    ON lifecycle_proposals(scope_id, status, run_id, proposal_id);
CREATE INDEX IF NOT EXISTS idx_lifecycle_proposals_target
    ON lifecycle_proposals(scope_id, target_entity_type, target_id, proposal_id);
"""
