CREATE TABLE published_review_documents (
    review_document_id TEXT PRIMARY KEY,
    planning_document_id TEXT NOT NULL REFERENCES planning_documents(planning_document_id),
    local_path TEXT NOT NULL,
    title TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    notion_page_id TEXT NOT NULL UNIQUE,
    answer_slot_block_id TEXT NOT NULL,
    last_answer_hash TEXT,
    last_answer_at TEXT,
    published_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (planning_document_id, local_path)
);
