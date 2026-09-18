from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .content_store import ContentStore
from .db import Database
from .errors import ResourceNotFound, StateConflict, ValidationError
from .evidence import EvidenceService
from .repositories import RepositoryService
from .util import canonical_json_bytes, new_id, sha256_bytes, utc_now

CONTRACT_VERSION = "1"
ANALYSIS_TYPES = {"SOURCE_DIFF", "IMPACT", "REVIEW"}
CHANGE_CLASSIFICATIONS = {
    "POLICY_CHANGED",
    "REQUIREMENT_ADDED",
    "REQUIREMENT_REMOVED",
    "CONDITION_CHANGED",
    "WORDING_ONLY",
    "IRRELEVANT",
}
IMPACT_ASSESSMENTS = {
    "UNAFFECTED",
    "REVIEW_REQUIRED",
    "INVALIDATED",
    "IMPLEMENTATION_CHANGE_REQUIRED",
}
FINDING_TYPES = {
    "CODE_MISMATCH",
    "POLICY_CONFLICT",
    "DOCUMENT_CONFLICT",
    "LOGIC_DEFECT",
    "REQUIREMENT_GAP",
    "AMBIGUITY",
}


@dataclass(frozen=True)
class ProposalRecord:
    analysis_proposal_id: str
    analysis_type: str
    subject_ref: str
    status: str


class AnalysisService:
    def __init__(
        self,
        database: Database,
        content_store: ContentStore,
        workspace_root: Path,
        requests_dir: Path,
    ):
        self.database = database
        self.content_store = content_store
        self.workspace_root = workspace_root
        self.requests_dir = requests_dir
        self.requests_dir.mkdir(parents=True, exist_ok=True)

    def export_packet(self, analysis_type: str, subject_ref: str) -> Path:
        analysis_type = analysis_type.upper().replace("-", "_")
        if analysis_type not in ANALYSIS_TYPES:
            raise ValidationError(f"unsupported analysis type: {analysis_type}")
        if analysis_type == "SOURCE_DIFF":
            packet = self._source_diff_packet(subject_ref)
        elif analysis_type == "IMPACT":
            packet = self._impact_packet(subject_ref)
        else:
            packet = self._review_packet(subject_ref)
        payload = canonical_json_bytes(packet)
        target = self.requests_dir / f"{analysis_type.lower()}-{subject_ref}.json"
        target.write_bytes(payload)
        return target

    def import_proposal(self, path: Path) -> ProposalRecord:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(f"invalid proposal file: {path}") from exc
        self._validate_proposal(payload)
        proposal_type = payload["analysis_type"]
        subject_ref = payload["subject_ref"]
        expected_snapshots = self._expected_snapshot_refs(proposal_type, subject_ref)
        if payload["input_snapshot_refs"] != expected_snapshots:
            raise StateConflict("proposal Snapshot inputs are stale")
        expected_code = self._code_baseline()
        proposal_code = payload.get("code_baseline") or []
        if proposal_type in {"IMPACT", "REVIEW"} and proposal_code != expected_code:
            raise StateConflict("proposal code baseline is stale")
        payload_hash = sha256_bytes(canonical_json_bytes(payload))
        _, payload_ref = self.content_store.put_json(payload)
        now = utc_now()
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM analysis_proposals
                WHERE subject_ref = ? AND contract_version = ? AND payload_hash = ?
                """,
                (subject_ref, CONTRACT_VERSION, payload_hash),
            ).fetchone()
            if existing:
                return self._proposal_from_row(existing)
            proposal_id = new_id()
            connection.execute(
                """
                INSERT INTO analysis_proposals(
                    analysis_proposal_id, analysis_type, subject_ref, input_snapshot_refs_json,
                    code_baseline_json, contract_version, producer_type, producer_ref,
                    payload_ref, payload_hash, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PROPOSED', ?)
                """,
                (
                    proposal_id,
                    proposal_type,
                    subject_ref,
                    json.dumps(payload["input_snapshot_refs"], separators=(",", ":")),
                    json.dumps(proposal_code, separators=(",", ":")) if proposal_code else None,
                    CONTRACT_VERSION,
                    payload.get("producer", {}).get("type", "AI"),
                    payload.get("producer", {}).get("ref"),
                    payload_ref,
                    payload_hash,
                    now,
                ),
            )
            for candidate in payload["candidates"]:
                connection.execute(
                    """
                    INSERT INTO analysis_candidates(
                        analysis_proposal_id, candidate_key, candidate_type, payload_json, status
                    ) VALUES (?, ?, ?, ?, 'PENDING')
                    """,
                    (
                        proposal_id,
                        candidate["candidate_key"],
                        candidate["candidate_type"],
                        canonical_json_bytes(candidate).decode("utf-8"),
                    ),
                )
            row = connection.execute(
                "SELECT * FROM analysis_proposals WHERE analysis_proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            return self._proposal_from_row(row)

    def show(self, proposal_id: str) -> dict[str, Any]:
        connection = self.database.connect()
        try:
            proposal = connection.execute(
                "SELECT * FROM analysis_proposals WHERE analysis_proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            if proposal is None:
                raise ResourceNotFound(f"proposal not found: {proposal_id}")
            candidates = connection.execute(
                """
                SELECT candidate_key, candidate_type, payload_json, status
                FROM analysis_candidates WHERE analysis_proposal_id = ? ORDER BY candidate_key
                """,
                (proposal_id,),
            ).fetchall()
            return {
                "proposal_id": proposal_id,
                "analysis_type": proposal["analysis_type"],
                "subject_ref": proposal["subject_ref"],
                "status": proposal["status"],
                "candidates": [
                    {
                        "candidate_key": row["candidate_key"],
                        "candidate_type": row["candidate_type"],
                        "status": row["status"],
                        "payload": json.loads(row["payload_json"]),
                    }
                    for row in candidates
                ],
            }
        finally:
            connection.close()

    def review_candidate(
        self,
        proposal_id: str,
        candidate_key: str,
        action: str,
        *,
        reviewer: str = "developer",
        reviewed_payload: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        action = action.upper().replace("-", "_")
        if action not in {"ADOPT", "EDIT_AND_ADOPT", "REJECT"}:
            raise ValidationError("supported review actions are ADOPT, EDIT_AND_ADOPT, REJECT")
        with self.database.transaction() as connection:
            proposal = connection.execute(
                "SELECT * FROM analysis_proposals WHERE analysis_proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            candidate = connection.execute(
                """
                SELECT * FROM analysis_candidates
                WHERE analysis_proposal_id = ? AND candidate_key = ?
                """,
                (proposal_id, candidate_key),
            ).fetchone()
            if proposal is None or candidate is None:
                raise ResourceNotFound("proposal candidate not found")
            if proposal["status"] != "PROPOSED" or candidate["status"] != "PENDING":
                raise StateConflict("proposal candidate is already resolved")
            self._assert_fresh(connection, proposal)
            base_payload = json.loads(candidate["payload_json"])
            effective = reviewed_payload if action == "EDIT_AND_ADOPT" else base_payload
            if action == "EDIT_AND_ADOPT" and reviewed_payload is None:
                raise ValidationError("EDIT_AND_ADOPT requires reviewed payload")
            action_id = new_id()
            connection.execute(
                """
                INSERT INTO review_actions(
                    review_action_id, analysis_proposal_id, candidate_key, action,
                    reviewed_payload_json, reviewer, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_id,
                    proposal_id,
                    candidate_key,
                    action,
                    canonical_json_bytes(effective).decode("utf-8") if effective else None,
                    reviewer,
                    reason,
                    utc_now(),
                ),
            )
            created: dict[str, Any] | None = None
            if action == "REJECT":
                connection.execute(
                    "UPDATE analysis_candidates SET status = 'REJECTED' WHERE analysis_proposal_id = ? AND candidate_key = ?",
                    (proposal_id, candidate_key),
                )
            else:
                created = self._adopt(connection, proposal, effective, action_id)
                connection.execute(
                    "UPDATE analysis_candidates SET status = 'ADOPTED' WHERE analysis_proposal_id = ? AND candidate_key = ?",
                    (proposal_id, candidate_key),
                )
            pending = connection.execute(
                "SELECT COUNT(*) AS c FROM analysis_candidates WHERE analysis_proposal_id = ? AND status = 'PENDING'",
                (proposal_id,),
            ).fetchone()["c"]
            if pending == 0:
                connection.execute(
                    "UPDATE analysis_proposals SET status = 'RESOLVED' WHERE analysis_proposal_id = ?",
                    (proposal_id,),
                )
                if proposal["analysis_type"] == "SOURCE_DIFF":
                    connection.execute(
                        "UPDATE change_sets SET analysis_status = 'SOURCE_DIFF_ADOPTED' WHERE change_set_id = ?",
                        (proposal["subject_ref"],),
                    )
            return {"review_action_id": action_id, "action": action, "created": created}

    def _adopt(self, connection, proposal, payload: dict[str, Any], action_id: str) -> dict[str, Any]:
        analysis_type = proposal["analysis_type"]
        if analysis_type == "SOURCE_DIFF":
            return self._adopt_source_diff(connection, proposal, payload, action_id)
        if analysis_type == "IMPACT":
            return self._adopt_impact(connection, proposal, payload, action_id)
        return self._adopt_review(connection, proposal, payload, action_id)

    def _adopt_source_diff(self, connection, proposal, payload, action_id) -> dict[str, Any]:
        classification = payload.get("classification")
        if classification not in CHANGE_CLASSIFICATIONS:
            raise ValidationError(f"invalid ChangeItem classification: {classification}")
        summary = str(payload.get("summary") or "").strip()
        physical_refs = payload.get("physical_change_refs") or []
        evidence = payload.get("source_evidence") or []
        if not summary or not physical_refs or not evidence:
            raise ValidationError("Source Diff adoption requires summary, physical_change_refs, and source_evidence")
        for ref in physical_refs:
            if connection.execute(
                "SELECT 1 FROM physical_changes WHERE physical_change_id = ? AND change_set_id = ?",
                (ref, proposal["subject_ref"]),
            ).fetchone() is None:
                raise ValidationError(f"physical change does not belong to ChangeSet: {ref}")
        change_item_id = new_id()
        connection.execute(
            """
            INSERT INTO change_items(
                change_item_id, change_set_id, classification, summary,
                physical_change_refs_json, source_proposal_id, source_review_action_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                change_item_id,
                proposal["subject_ref"],
                classification,
                summary,
                json.dumps(physical_refs, separators=(",", ":")),
                proposal["analysis_proposal_id"],
                action_id,
                utc_now(),
            ),
        )
        for item in evidence:
            side = item.get("side")
            snapshot_id = item.get("source_page_snapshot_id")
            if side not in {"BASELINE", "TARGET"} or not snapshot_id:
                raise ValidationError("invalid Source Diff evidence")
            if connection.execute(
                "SELECT 1 FROM source_page_snapshots WHERE source_page_snapshot_id = ?",
                (snapshot_id,),
            ).fetchone() is None:
                raise ValidationError(f"source snapshot not found: {snapshot_id}")
            block_path = item.get("block_path") or []
            quote_hash = str(item.get("quote_hash") or "")
            if len(quote_hash) != 64:
                raise ValidationError("source evidence quote_hash must be SHA-256")
            connection.execute(
                """
                INSERT INTO change_item_source_evidence(
                    change_item_id, side, source_page_snapshot_id, block_path_json, field, quote_hash
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    change_item_id,
                    side,
                    snapshot_id,
                    json.dumps(block_path, separators=(",", ":")),
                    item.get("field"),
                    quote_hash,
                ),
            )
        return {"change_item_id": change_item_id}

    def _adopt_impact(self, connection, proposal, payload, action_id) -> dict[str, Any]:
        assessment = payload.get("assessment")
        if assessment not in IMPACT_ASSESSMENTS:
            raise ValidationError(f"invalid impact assessment: {assessment}")
        change_item_id = payload.get("change_item_id")
        reference_change_ref = payload.get("reference_change_ref")
        if not change_item_id and not reference_change_ref:
            raise ValidationError("Impact adoption requires change_item_id or reference_change_ref")
        target_type = payload.get("target_type")
        target_ref = str(payload.get("target_ref") or "").strip()
        if target_type not in {"FINDING", "DECISION", "FINAL_SPEC_REVISION", "IMPLEMENTATION", "RELATED_DOCUMENT", "CODE"} or not target_ref:
            raise ValidationError("invalid impact target")
        impact_link_id = new_id()
        connection.execute(
            """
            INSERT INTO impact_links(
                impact_link_id, change_item_id, reference_change_ref, target_type,
                target_ref, assessment, summary, rationale, proposed_action,
                source_proposal_id, source_review_action_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                impact_link_id,
                change_item_id,
                reference_change_ref,
                target_type,
                target_ref,
                assessment,
                str(payload.get("summary") or ""),
                str(payload.get("rationale") or ""),
                str(payload.get("proposed_action") or ""),
                proposal["analysis_proposal_id"],
                action_id,
                utc_now(),
            ),
        )
        for evidence in payload.get("evidence_refs") or []:
            evidence_id = EvidenceService(self.database).ensure(
                evidence["type"], evidence["payload"], connection=connection
            )
            connection.execute(
                "INSERT INTO impact_link_evidence(impact_link_id, evidence_ref_id) VALUES (?, ?)",
                (impact_link_id, evidence_id),
            )
        return {"impact_link_id": impact_link_id}

    def _adopt_review(self, connection, proposal, payload, action_id) -> dict[str, Any]:
        finding_type = payload.get("finding_type")
        owner = payload.get("decision_owner")
        summary = str(payload.get("summary") or "").strip()
        if finding_type not in FINDING_TYPES or owner not in {"DEVELOPER", "PLANNER"} or not summary:
            raise ValidationError("invalid review Finding candidate")
        review_cycle_id = self._ensure_review_cycle(connection, proposal["subject_ref"])
        evidence_refs = payload.get("evidence_refs") or []
        if not evidence_refs:
            raise ValidationError("Finding adoption requires at least one evidence ref")
        finding_id = new_id()
        connection.execute(
            """
            INSERT INTO findings(
                finding_id, review_cycle_id, finding_type, decision_owner, blocking,
                summary, status, source_proposal_id, source_review_action_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
            """,
            (
                finding_id,
                review_cycle_id,
                finding_type,
                owner,
                int(bool(payload.get("blocking"))),
                summary,
                proposal["analysis_proposal_id"],
                action_id,
                utc_now(),
            ),
        )
        for evidence in evidence_refs:
            evidence_id = EvidenceService(self.database).ensure(
                evidence["type"], evidence["payload"], connection=connection
            )
            connection.execute(
                "INSERT INTO finding_evidence(finding_id, evidence_ref_id) VALUES (?, ?)",
                (finding_id, evidence_id),
            )
        connection.execute(
            "UPDATE review_cycles SET status = 'REVIEWING' WHERE review_cycle_id = ? AND status = 'PENDING'",
            (review_cycle_id,),
        )
        return {"finding_id": finding_id, "review_cycle_id": review_cycle_id}

    def _ensure_review_cycle(self, connection, planning_document_id: str) -> str:
        document = connection.execute(
            "SELECT current_snapshot_id FROM planning_documents WHERE planning_document_id = ?",
            (planning_document_id,),
        ).fetchone()
        if document is None or document["current_snapshot_id"] is None:
            raise StateConflict("PlanningDocument has no current Snapshot")
        target = document["current_snapshot_id"]
        existing = connection.execute(
            """
            SELECT review_cycle_id FROM review_cycles
            WHERE planning_document_id = ? AND target_planning_snapshot_id = ?
              AND status != 'SUPERSEDED'
            ORDER BY created_at DESC LIMIT 1
            """,
            (planning_document_id, target),
        ).fetchone()
        if existing:
            return existing["review_cycle_id"]
        snapshot = connection.execute(
            "SELECT previous_snapshot_id FROM planning_document_snapshots WHERE planning_document_snapshot_id = ?",
            (target,),
        ).fetchone()
        baseline = snapshot["previous_snapshot_id"] if snapshot else None
        review_cycle_id = new_id()
        code_baseline = self._code_baseline()
        connection.execute(
            """
            INSERT INTO review_cycles(
                review_cycle_id, planning_document_id, target_planning_snapshot_id,
                baseline_planning_snapshot_id, code_baseline_json, review_type, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)
            """,
            (
                review_cycle_id,
                planning_document_id,
                target,
                baseline,
                json.dumps(code_baseline, separators=(",", ":")) if code_baseline else None,
                "CHANGE" if baseline else "INITIAL",
                utc_now(),
            ),
        )
        return review_cycle_id

    def _validate_proposal(self, payload: dict[str, Any]) -> None:
        required = {"contract_version", "analysis_type", "subject_ref", "input_snapshot_refs", "candidates"}
        missing = required - payload.keys()
        if missing:
            raise ValidationError(f"proposal missing fields: {', '.join(sorted(missing))}")
        if payload["contract_version"] != CONTRACT_VERSION:
            raise ValidationError(f"unsupported contract version: {payload['contract_version']}")
        if payload["analysis_type"] not in ANALYSIS_TYPES:
            raise ValidationError(f"invalid analysis type: {payload['analysis_type']}")
        if not isinstance(payload["candidates"], list) or not payload["candidates"]:
            raise ValidationError("proposal requires at least one candidate")
        keys: set[str] = set()
        for candidate in payload["candidates"]:
            key = str(candidate.get("candidate_key") or "")
            if not key or key in keys or not candidate.get("candidate_type"):
                raise ValidationError("candidate keys must be unique and candidate_type is required")
            keys.add(key)

    def _assert_fresh(self, connection, proposal) -> None:
        expected = self._expected_snapshot_refs(proposal["analysis_type"], proposal["subject_ref"], connection=connection)
        actual = json.loads(proposal["input_snapshot_refs_json"])
        if expected != actual:
            connection.execute(
                "UPDATE analysis_proposals SET status = 'STALE' WHERE analysis_proposal_id = ?",
                (proposal["analysis_proposal_id"],),
            )
            raise StateConflict("proposal Snapshot inputs are stale")
        if proposal["analysis_type"] in {"IMPACT", "REVIEW"}:
            expected_code = self._code_baseline()
            actual_code = json.loads(proposal["code_baseline_json"] or "[]")
            if expected_code != actual_code:
                connection.execute(
                    "UPDATE analysis_proposals SET status = 'STALE' WHERE analysis_proposal_id = ?",
                    (proposal["analysis_proposal_id"],),
                )
                raise StateConflict("proposal code baseline is stale")

    def _source_diff_packet(self, change_set_id: str) -> dict[str, Any]:
        connection = self.database.connect()
        try:
            change_set = connection.execute(
                "SELECT * FROM change_sets WHERE change_set_id = ?", (change_set_id,)
            ).fetchone()
            if change_set is None:
                raise ResourceNotFound(f"ChangeSet not found: {change_set_id}")
            physical = connection.execute(
                "SELECT * FROM physical_changes WHERE change_set_id = ? ORDER BY notion_page_id, change_type",
                (change_set_id,),
            ).fetchall()
            return {
                "contract_version": CONTRACT_VERSION,
                "analysis_type": "SOURCE_DIFF",
                "subject_ref": change_set_id,
                "input_snapshot_refs": [change_set["baseline_snapshot_id"], change_set["target_snapshot_id"]],
                "code_baseline": [],
                "physical_changes": [dict(row) for row in physical],
                "source_pages": self._source_packet_pages(
                    connection, [change_set["baseline_snapshot_id"], change_set["target_snapshot_id"]]
                ),
                "expected_output": self._expected_output("SOURCE_DIFF", change_set_id, [change_set["baseline_snapshot_id"], change_set["target_snapshot_id"]], []),
            }
        finally:
            connection.close()

    def _impact_packet(self, change_set_id: str) -> dict[str, Any]:
        connection = self.database.connect()
        try:
            change_set = connection.execute(
                "SELECT * FROM change_sets WHERE change_set_id = ?", (change_set_id,)
            ).fetchone()
            if change_set is None:
                raise ResourceNotFound(f"ChangeSet not found: {change_set_id}")
            items = connection.execute(
                "SELECT * FROM change_items WHERE change_set_id = ? ORDER BY created_at, change_item_id",
                (change_set_id,),
            ).fetchall()
            code = self._code_baseline()
            return {
                "contract_version": CONTRACT_VERSION,
                "analysis_type": "IMPACT",
                "subject_ref": change_set_id,
                "input_snapshot_refs": [change_set["target_snapshot_id"]],
                "code_baseline": code,
                "change_items": [dict(row) for row in items],
                "current_development_basis": self._development_basis(connection, change_set["planning_document_id"]),
                "expected_output": self._expected_output("IMPACT", change_set_id, [change_set["target_snapshot_id"]], code),
            }
        finally:
            connection.close()

    def _review_packet(self, planning_document_id: str) -> dict[str, Any]:
        connection = self.database.connect()
        try:
            document = connection.execute(
                "SELECT * FROM planning_documents WHERE planning_document_id = ?",
                (planning_document_id,),
            ).fetchone()
            if document is None or document["current_snapshot_id"] is None:
                raise ResourceNotFound(f"PlanningDocument with Snapshot not found: {planning_document_id}")
            snapshot = document["current_snapshot_id"]
            code = self._code_baseline()
            return {
                "contract_version": CONTRACT_VERSION,
                "analysis_type": "REVIEW",
                "subject_ref": planning_document_id,
                "input_snapshot_refs": [snapshot],
                "code_baseline": code,
                "source_pages": self._source_packet_pages(connection, [snapshot]),
                "current_development_basis": self._development_basis(connection, planning_document_id),
                "expected_output": self._expected_output("REVIEW", planning_document_id, [snapshot], code),
            }
        finally:
            connection.close()

    def _expected_output(self, analysis_type: str, subject_ref: str, snapshots: list[str], code: list[dict[str, str]]) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "analysis_type": analysis_type,
            "subject_ref": subject_ref,
            "input_snapshot_refs": snapshots,
            "code_baseline": code,
            "producer": {"type": "AI", "ref": "fill-me"},
            "candidates": [],
        }

    def _source_packet_pages(self, connection, snapshot_ids: list[str]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for snapshot_id in snapshot_ids:
            rows = connection.execute(
                """
                SELECT psp.*, sps.content_ref, sps.raw_content_ref
                FROM planning_snapshot_pages psp
                JOIN source_page_snapshots sps ON sps.source_page_snapshot_id = psp.source_page_snapshot_id
                WHERE psp.planning_document_snapshot_id = ? ORDER BY psp.notion_page_id
                """,
                (snapshot_id,),
            ).fetchall()
            result.extend({"planning_snapshot_id": snapshot_id, **dict(row)} for row in rows)
        return result

    def _development_basis(self, connection, planning_document_id: str) -> dict[str, Any]:
        final_spec = connection.execute(
            """
            SELECT fs.current_revision_id FROM final_specs fs WHERE fs.planning_document_id = ?
            """,
            (planning_document_id,),
        ).fetchone()
        decisions = connection.execute(
            """
            SELECT d.* FROM decisions d JOIN findings f ON f.finding_id = d.finding_id
            JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
            WHERE rc.planning_document_id = ? AND d.status = 'ADOPTED'
            ORDER BY d.created_at, d.decision_id
            """,
            (planning_document_id,),
        ).fetchall()
        return {
            "current_final_spec_revision_id": final_spec["current_revision_id"] if final_spec else None,
            "active_decisions": [dict(row) for row in decisions],
        }

    def _expected_snapshot_refs(self, analysis_type: str, subject_ref: str, connection=None) -> list[str]:
        owns = connection is None
        if owns:
            connection = self.database.connect()
        try:
            if analysis_type == "SOURCE_DIFF":
                row = connection.execute(
                    "SELECT baseline_snapshot_id, target_snapshot_id FROM change_sets WHERE change_set_id = ?",
                    (subject_ref,),
                ).fetchone()
                if row is None:
                    raise ResourceNotFound(f"ChangeSet not found: {subject_ref}")
                return [row["baseline_snapshot_id"], row["target_snapshot_id"]]
            if analysis_type == "IMPACT":
                row = connection.execute(
                    "SELECT target_snapshot_id FROM change_sets WHERE change_set_id = ?",
                    (subject_ref,),
                ).fetchone()
                if row is None:
                    raise ResourceNotFound(f"ChangeSet not found: {subject_ref}")
                return [row["target_snapshot_id"]]
            row = connection.execute(
                "SELECT current_snapshot_id FROM planning_documents WHERE planning_document_id = ?",
                (subject_ref,),
            ).fetchone()
            if row is None or row["current_snapshot_id"] is None:
                raise ResourceNotFound(f"PlanningDocument with Snapshot not found: {subject_ref}")
            return [row["current_snapshot_id"]]
        finally:
            if owns:
                connection.close()

    def _code_baseline(self) -> list[dict[str, str]]:
        service = RepositoryService(self.database, self.workspace_root)
        result: list[dict[str, str]] = []
        for repository in service.list():
            commit_sha = service.resolve_head(repository.repository_id)
            result.append({"repository_id": repository.repository_id, "commit_sha": commit_sha})
        result.sort(key=lambda item: item["repository_id"])
        return result

    @staticmethod
    def _proposal_from_row(row) -> ProposalRecord:
        return ProposalRecord(
            row["analysis_proposal_id"], row["analysis_type"], row["subject_ref"], row["status"]
        )
