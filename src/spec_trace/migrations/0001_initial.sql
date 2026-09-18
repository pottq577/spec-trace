CREATE TABLE repositories (
    repository_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    local_path TEXT NOT NULL UNIQUE,
    remote_identity TEXT,
    default_ref TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE planning_documents (
    planning_document_id TEXT PRIMARY KEY,
    notion_database_id TEXT NOT NULL,
    root_notion_page_id TEXT NOT NULL,
    title TEXT NOT NULL,
    source_status TEXT NOT NULL CHECK (source_status IN ('AVAILABLE', 'UNAVAILABLE')),
    current_snapshot_id TEXT,
    last_collected_at TEXT,
    attention_required INTEGER NOT NULL DEFAULT 0 CHECK (attention_required IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE (notion_database_id, root_notion_page_id)
);

CREATE TABLE source_pages (
    source_page_id TEXT PRIMARY KEY,
    planning_document_id TEXT NOT NULL REFERENCES planning_documents(planning_document_id),
    notion_page_id TEXT NOT NULL,
    current_parent_source_page_id TEXT REFERENCES source_pages(source_page_id),
    role TEXT NOT NULL CHECK (role IN ('ROOT', 'COMPOSED_CHILD')),
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (planning_document_id, notion_page_id)
);

CREATE TABLE source_page_snapshots (
    source_page_snapshot_id TEXT PRIMARY KEY,
    source_page_id TEXT NOT NULL REFERENCES source_pages(source_page_id),
    previous_snapshot_id TEXT REFERENCES source_page_snapshots(source_page_snapshot_id),
    content_hash TEXT NOT NULL,
    content_ref TEXT NOT NULL,
    raw_content_ref TEXT,
    captured_at TEXT NOT NULL,
    UNIQUE (source_page_id, content_hash)
);

CREATE TABLE planning_document_snapshots (
    planning_document_snapshot_id TEXT PRIMARY KEY,
    planning_document_id TEXT NOT NULL REFERENCES planning_documents(planning_document_id),
    previous_snapshot_id TEXT REFERENCES planning_document_snapshots(planning_document_snapshot_id),
    aggregate_hash TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    UNIQUE (planning_document_id, aggregate_hash)
);

CREATE TABLE planning_snapshot_pages (
    planning_document_snapshot_id TEXT NOT NULL REFERENCES planning_document_snapshots(planning_document_snapshot_id),
    source_page_id TEXT NOT NULL REFERENCES source_pages(source_page_id),
    source_page_snapshot_id TEXT NOT NULL REFERENCES source_page_snapshots(source_page_snapshot_id),
    notion_page_id TEXT NOT NULL,
    parent_notion_page_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('ROOT', 'COMPOSED_CHILD')),
    content_hash TEXT NOT NULL,
    PRIMARY KEY (planning_document_snapshot_id, notion_page_id)
);

CREATE TABLE source_references (
    source_reference_id TEXT PRIMARY KEY,
    planning_document_snapshot_id TEXT NOT NULL REFERENCES planning_document_snapshots(planning_document_snapshot_id),
    source_page_snapshot_id TEXT NOT NULL REFERENCES source_page_snapshots(source_page_snapshot_id),
    target_notion_page_id TEXT NOT NULL,
    target_planning_document_id TEXT REFERENCES planning_documents(planning_document_id),
    reference_type TEXT NOT NULL,
    location_json TEXT NOT NULL,
    UNIQUE (
        planning_document_snapshot_id,
        source_page_snapshot_id,
        target_notion_page_id,
        reference_type,
        location_json
    )
);

CREATE TABLE collection_runs (
    collection_run_id TEXT PRIMARY KEY,
    planning_document_id TEXT NOT NULL REFERENCES planning_documents(planning_document_id),
    trigger_type TEXT NOT NULL,
    status TEXT NOT NULL,
    baseline_snapshot_id TEXT REFERENCES planning_document_snapshots(planning_document_snapshot_id),
    created_snapshot_id TEXT REFERENCES planning_document_snapshots(planning_document_snapshot_id),
    attempt INTEGER NOT NULL DEFAULT 1,
    failure_code TEXT,
    retryable INTEGER CHECK (retryable IN (0, 1)),
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE UNIQUE INDEX one_active_collection_per_document
ON collection_runs(planning_document_id)
WHERE completed_at IS NULL;

CREATE TABLE change_sets (
    change_set_id TEXT PRIMARY KEY,
    planning_document_id TEXT NOT NULL REFERENCES planning_documents(planning_document_id),
    baseline_snapshot_id TEXT NOT NULL REFERENCES planning_document_snapshots(planning_document_snapshot_id),
    target_snapshot_id TEXT NOT NULL REFERENCES planning_document_snapshots(planning_document_snapshot_id),
    analysis_status TEXT NOT NULL CHECK (
        analysis_status IN (
            'PENDING_SOURCE_DIFF', 'SOURCE_DIFF_PROPOSED',
            'SOURCE_DIFF_ADOPTED', 'IMPACT_PROPOSED', 'COMPLETED', 'FAILED'
        )
    ),
    created_at TEXT NOT NULL,
    UNIQUE (planning_document_id, baseline_snapshot_id, target_snapshot_id)
);

CREATE TABLE physical_changes (
    physical_change_id TEXT PRIMARY KEY,
    change_set_id TEXT NOT NULL REFERENCES change_sets(change_set_id),
    notion_page_id TEXT NOT NULL,
    change_type TEXT NOT NULL CHECK (
        change_type IN (
            'PAGE_ADDED', 'PAGE_REMOVED', 'CONTENT_CHANGED',
            'PARENT_CHANGED', 'ROLE_CHANGED'
        )
    ),
    baseline_source_page_snapshot_id TEXT REFERENCES source_page_snapshots(source_page_snapshot_id),
    target_source_page_snapshot_id TEXT REFERENCES source_page_snapshots(source_page_snapshot_id),
    baseline_parent_notion_page_id TEXT,
    target_parent_notion_page_id TEXT,
    baseline_content_hash TEXT,
    target_content_hash TEXT,
    UNIQUE (change_set_id, notion_page_id, change_type)
);

CREATE TABLE analysis_proposals (
    analysis_proposal_id TEXT PRIMARY KEY,
    analysis_type TEXT NOT NULL CHECK (analysis_type IN ('SOURCE_DIFF', 'IMPACT', 'REVIEW')),
    subject_ref TEXT NOT NULL,
    input_snapshot_refs_json TEXT NOT NULL,
    code_baseline_json TEXT,
    contract_version TEXT NOT NULL,
    producer_type TEXT NOT NULL CHECK (producer_type IN ('AI', 'RULE', 'DEVELOPER')),
    producer_ref TEXT,
    payload_ref TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PROPOSED', 'STALE', 'RESOLVED', 'SUPERSEDED')),
    created_at TEXT NOT NULL,
    UNIQUE (subject_ref, contract_version, payload_hash)
);

CREATE TABLE analysis_candidates (
    analysis_proposal_id TEXT NOT NULL REFERENCES analysis_proposals(analysis_proposal_id),
    candidate_key TEXT NOT NULL,
    candidate_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'ADOPTED', 'REJECTED', 'SUPERSEDED')),
    PRIMARY KEY (analysis_proposal_id, candidate_key)
);

CREATE TABLE review_actions (
    review_action_id TEXT PRIMARY KEY,
    analysis_proposal_id TEXT NOT NULL REFERENCES analysis_proposals(analysis_proposal_id),
    candidate_key TEXT NOT NULL,
    action TEXT NOT NULL CHECK (
        action IN ('ADOPT', 'EDIT_AND_ADOPT', 'REJECT', 'SPLIT', 'MERGE')
    ),
    reviewed_payload_json TEXT,
    reviewer TEXT NOT NULL,
    reason TEXT,
    supersedes_review_action_id TEXT REFERENCES review_actions(review_action_id),
    created_at TEXT NOT NULL
);

CREATE TABLE change_items (
    change_item_id TEXT PRIMARY KEY,
    change_set_id TEXT NOT NULL REFERENCES change_sets(change_set_id),
    classification TEXT NOT NULL CHECK (
        classification IN (
            'POLICY_CHANGED', 'REQUIREMENT_ADDED', 'REQUIREMENT_REMOVED',
            'CONDITION_CHANGED', 'WORDING_ONLY', 'IRRELEVANT'
        )
    ),
    summary TEXT NOT NULL,
    physical_change_refs_json TEXT NOT NULL,
    source_proposal_id TEXT NOT NULL REFERENCES analysis_proposals(analysis_proposal_id),
    source_review_action_id TEXT NOT NULL REFERENCES review_actions(review_action_id),
    created_at TEXT NOT NULL
);

CREATE TABLE change_item_source_evidence (
    change_item_id TEXT NOT NULL REFERENCES change_items(change_item_id),
    side TEXT NOT NULL CHECK (side IN ('BASELINE', 'TARGET')),
    source_page_snapshot_id TEXT NOT NULL REFERENCES source_page_snapshots(source_page_snapshot_id),
    block_path_json TEXT NOT NULL,
    field TEXT,
    quote_hash TEXT NOT NULL,
    PRIMARY KEY (change_item_id, side, source_page_snapshot_id, block_path_json, quote_hash)
);

CREATE TABLE evidence_refs (
    evidence_ref_id TEXT PRIMARY KEY,
    evidence_type TEXT NOT NULL CHECK (
        evidence_type IN (
            'SOURCE', 'RELATED_DOCUMENT', 'DERIVED_ARTIFACT',
            'FINAL_SPEC', 'CODE', 'DECISION', 'IMPLEMENTATION'
        )
    ),
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (evidence_type, payload_hash)
);

CREATE TABLE impact_links (
    impact_link_id TEXT PRIMARY KEY,
    change_item_id TEXT REFERENCES change_items(change_item_id),
    reference_change_ref TEXT,
    target_type TEXT NOT NULL CHECK (
        target_type IN (
            'FINDING', 'DECISION', 'FINAL_SPEC_REVISION',
            'IMPLEMENTATION', 'RELATED_DOCUMENT', 'CODE'
        )
    ),
    target_ref TEXT NOT NULL,
    assessment TEXT NOT NULL CHECK (
        assessment IN (
            'UNAFFECTED', 'REVIEW_REQUIRED', 'INVALIDATED',
            'IMPLEMENTATION_CHANGE_REQUIRED'
        )
    ),
    summary TEXT NOT NULL,
    rationale TEXT NOT NULL,
    proposed_action TEXT NOT NULL,
    source_proposal_id TEXT NOT NULL REFERENCES analysis_proposals(analysis_proposal_id),
    source_review_action_id TEXT NOT NULL REFERENCES review_actions(review_action_id),
    created_at TEXT NOT NULL,
    CHECK (change_item_id IS NOT NULL OR reference_change_ref IS NOT NULL),
    UNIQUE (change_item_id, reference_change_ref, target_type, target_ref)
);

CREATE TABLE impact_link_evidence (
    impact_link_id TEXT NOT NULL REFERENCES impact_links(impact_link_id),
    evidence_ref_id TEXT NOT NULL REFERENCES evidence_refs(evidence_ref_id),
    PRIMARY KEY (impact_link_id, evidence_ref_id)
);

CREATE TABLE review_cycles (
    review_cycle_id TEXT PRIMARY KEY,
    planning_document_id TEXT NOT NULL REFERENCES planning_documents(planning_document_id),
    target_planning_snapshot_id TEXT NOT NULL REFERENCES planning_document_snapshots(planning_document_snapshot_id),
    baseline_planning_snapshot_id TEXT REFERENCES planning_document_snapshots(planning_document_snapshot_id),
    code_baseline_json TEXT,
    review_type TEXT NOT NULL CHECK (review_type IN ('INITIAL', 'CHANGE')),
    status TEXT NOT NULL CHECK (
        status IN (
            'PENDING', 'REVIEWING', 'AWAITING_PLANNER',
            'REVERIFYING', 'COMPLETED', 'SUPERSEDED'
        )
    ),
    created_at TEXT NOT NULL
);

CREATE TABLE findings (
    finding_id TEXT PRIMARY KEY,
    review_cycle_id TEXT NOT NULL REFERENCES review_cycles(review_cycle_id),
    finding_type TEXT NOT NULL CHECK (
        finding_type IN (
            'CODE_MISMATCH', 'POLICY_CONFLICT', 'DOCUMENT_CONFLICT',
            'LOGIC_DEFECT', 'REQUIREMENT_GAP', 'AMBIGUITY'
        )
    ),
    decision_owner TEXT NOT NULL CHECK (decision_owner IN ('DEVELOPER', 'PLANNER')),
    blocking INTEGER NOT NULL CHECK (blocking IN (0, 1)),
    summary TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('OPEN', 'RESOLVING', 'RESOLVED', 'REOPENED', 'SUPERSEDED')
    ),
    source_proposal_id TEXT REFERENCES analysis_proposals(analysis_proposal_id),
    source_review_action_id TEXT REFERENCES review_actions(review_action_id),
    created_at TEXT NOT NULL
);

CREATE TABLE finding_evidence (
    finding_id TEXT NOT NULL REFERENCES findings(finding_id),
    evidence_ref_id TEXT NOT NULL REFERENCES evidence_refs(evidence_ref_id),
    PRIMARY KEY (finding_id, evidence_ref_id)
);

CREATE TABLE decisions (
    decision_id TEXT PRIMARY KEY,
    finding_id TEXT NOT NULL REFERENCES findings(finding_id),
    owner TEXT NOT NULL CHECK (owner IN ('DEVELOPER', 'PLANNER')),
    adopted_option TEXT NOT NULL,
    rationale TEXT NOT NULL,
    supersedes_decision_id TEXT REFERENCES decisions(decision_id),
    status TEXT NOT NULL CHECK (status IN ('ADOPTED', 'SUPERSEDED', 'INVALIDATED')),
    created_at TEXT NOT NULL
);

CREATE TABLE decision_evidence (
    decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
    evidence_ref_id TEXT NOT NULL REFERENCES evidence_refs(evidence_ref_id),
    PRIMARY KEY (decision_id, evidence_ref_id)
);

CREATE TABLE open_questions (
    open_question_id TEXT PRIMARY KEY,
    finding_id TEXT NOT NULL REFERENCES findings(finding_id),
    question TEXT NOT NULL,
    options_json TEXT NOT NULL,
    tradeoffs_json TEXT NOT NULL,
    developer_recommendation TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('OPEN', 'ANSWERED', 'VERIFYING', 'RESOLVED', 'REOPENED', 'SUPERSEDED')
    ),
    created_at TEXT NOT NULL
);

CREATE TABLE planner_answers (
    planner_answer_id TEXT PRIMARY KEY,
    open_question_id TEXT NOT NULL REFERENCES open_questions(open_question_id),
    answer TEXT NOT NULL,
    answer_hash TEXT NOT NULL,
    answered_at TEXT NOT NULL,
    supersedes_answer_id TEXT REFERENCES planner_answers(planner_answer_id),
    UNIQUE (open_question_id, answer_hash)
);

CREATE TABLE blockers (
    blocker_id TEXT PRIMARY KEY,
    finding_id TEXT NOT NULL REFERENCES findings(finding_id),
    reason TEXT NOT NULL,
    resume_condition TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RESOLVED', 'SUPERSEDED')),
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX one_active_blocker_per_finding
ON blockers(finding_id)
WHERE status = 'ACTIVE';

CREATE TABLE blocked_scopes (
    blocked_scope_id TEXT PRIMARY KEY,
    blocker_id TEXT NOT NULL REFERENCES blockers(blocker_id),
    scope_type TEXT NOT NULL CHECK (
        scope_type IN ('FEATURE', 'DESIGN', 'IMPLEMENTATION', 'WORK_ITEM')
    ),
    target_ref TEXT NOT NULL,
    description TEXT NOT NULL,
    resume_work TEXT NOT NULL
);

CREATE TABLE final_specs (
    final_spec_id TEXT PRIMARY KEY,
    planning_document_id TEXT NOT NULL UNIQUE REFERENCES planning_documents(planning_document_id),
    current_revision_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE final_spec_revisions (
    final_spec_revision_id TEXT PRIMARY KEY,
    final_spec_id TEXT NOT NULL REFERENCES final_specs(final_spec_id),
    previous_revision_id TEXT REFERENCES final_spec_revisions(final_spec_revision_id),
    content_ref TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE final_spec_revision_snapshots (
    final_spec_revision_id TEXT NOT NULL REFERENCES final_spec_revisions(final_spec_revision_id),
    planning_document_snapshot_id TEXT NOT NULL REFERENCES planning_document_snapshots(planning_document_snapshot_id),
    PRIMARY KEY (final_spec_revision_id, planning_document_snapshot_id)
);

CREATE TABLE final_spec_revision_decisions (
    final_spec_revision_id TEXT NOT NULL REFERENCES final_spec_revisions(final_spec_revision_id),
    decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
    PRIMARY KEY (final_spec_revision_id, decision_id)
);

CREATE TABLE implementation_refs (
    implementation_ref_id TEXT PRIMARY KEY,
    final_spec_revision_id TEXT NOT NULL REFERENCES final_spec_revisions(final_spec_revision_id),
    repository_id TEXT NOT NULL REFERENCES repositories(repository_id),
    commit_sha TEXT NOT NULL,
    work_ref TEXT,
    decision_ids_json TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    UNIQUE (final_spec_revision_id, repository_id, commit_sha, work_ref)
);

CREATE TABLE implementation_paths (
    implementation_ref_id TEXT NOT NULL REFERENCES implementation_refs(implementation_ref_id),
    path TEXT NOT NULL,
    symbol TEXT,
    PRIMARY KEY (implementation_ref_id, path, symbol)
);

CREATE TABLE notion_review_pages (
    planning_document_id TEXT PRIMARY KEY REFERENCES planning_documents(planning_document_id),
    review_page_id TEXT NOT NULL UNIQUE,
    updated_at TEXT NOT NULL
);

CREATE TABLE notion_question_blocks (
    open_question_id TEXT PRIMARY KEY REFERENCES open_questions(open_question_id),
    question_section_block_id TEXT NOT NULL,
    answer_slot_block_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE notion_blocker_blocks (
    blocker_id TEXT PRIMARY KEY REFERENCES blockers(blocker_id),
    blocker_section_block_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE projection_runs (
    projection_run_id TEXT PRIMARY KEY,
    planning_document_id TEXT NOT NULL REFERENCES planning_documents(planning_document_id),
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED')),
    failure_code TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE pending_operations (
    operation_id TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL,
    subject_ref TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')),
    attempt INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    failure_code TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
