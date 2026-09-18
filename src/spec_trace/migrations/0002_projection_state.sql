ALTER TABLE blockers ADD COLUMN available_work_json TEXT NOT NULL DEFAULT '[]';

CREATE TABLE notion_status_blocks (
    planning_document_id TEXT PRIMARY KEY REFERENCES planning_documents(planning_document_id),
    status_block_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE notion_decision_blocks (
    decision_id TEXT PRIMARY KEY REFERENCES decisions(decision_id),
    block_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
