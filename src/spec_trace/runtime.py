from __future__ import annotations

import json
import time
from typing import Any, Callable

from .collector import SourceCollector
from .errors import ResourceNotFound, SpecTraceError, ValidationError
from .notion import NotionPort
from .projection import ProjectionService
from .workspace import Workspace


class StatusService:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.database = workspace.database

    def document(self, planning_document_id: str) -> dict[str, Any]:
        connection = self.database.connect()
        try:
            document = connection.execute(
                "SELECT * FROM planning_documents WHERE planning_document_id = ?",
                (planning_document_id,),
            ).fetchone()
            if document is None:
                raise ResourceNotFound(
                    f"planning document not found: {planning_document_id}"
                )

            change_sets = connection.execute(
                """
                SELECT change_set_id, baseline_snapshot_id, target_snapshot_id,
                       analysis_status, created_at
                FROM change_sets
                WHERE planning_document_id = ? AND analysis_status != 'COMPLETED'
                ORDER BY created_at DESC, change_set_id DESC
                """,
                (planning_document_id,),
            ).fetchall()
            cycle = connection.execute(
                """
                SELECT * FROM review_cycles
                WHERE planning_document_id = ? AND status != 'SUPERSEDED'
                ORDER BY created_at DESC, review_cycle_id DESC LIMIT 1
                """,
                (planning_document_id,),
            ).fetchone()
            findings = connection.execute(
                """
                SELECT f.* FROM findings f
                JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                WHERE rc.planning_document_id = ?
                  AND f.status NOT IN ('RESOLVED', 'SUPERSEDED')
                ORDER BY f.created_at, f.finding_id
                """,
                (planning_document_id,),
            ).fetchall()
            questions = connection.execute(
                """
                SELECT oq.*, (
                    SELECT pa.planner_answer_id FROM planner_answers pa
                    WHERE pa.open_question_id = oq.open_question_id
                    ORDER BY pa.answered_at DESC, pa.planner_answer_id DESC LIMIT 1
                ) AS latest_answer_id
                FROM open_questions oq
                JOIN findings f ON f.finding_id = oq.finding_id
                JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                WHERE rc.planning_document_id = ?
                  AND oq.status NOT IN ('RESOLVED', 'SUPERSEDED')
                ORDER BY oq.created_at, oq.open_question_id
                """,
                (planning_document_id,),
            ).fetchall()
            blockers = connection.execute(
                """
                SELECT b.* FROM blockers b
                JOIN findings f ON f.finding_id = b.finding_id
                JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                WHERE rc.planning_document_id = ? AND b.status = 'ACTIVE'
                ORDER BY b.created_at, b.blocker_id
                """,
                (planning_document_id,),
            ).fetchall()
            final_spec = connection.execute(
                """
                SELECT fs.current_revision_id AS final_spec_revision_id,
                       fsr.content_hash, fsr.created_at
                FROM final_specs fs
                LEFT JOIN final_spec_revisions fsr
                  ON fsr.final_spec_revision_id = fs.current_revision_id
                WHERE fs.planning_document_id = ?
                """,
                (planning_document_id,),
            ).fetchone()
            pending = connection.execute(
                """
                SELECT operation_id, operation_type, subject_ref, dedupe_key,
                       status, attempt, available_at, failure_code, created_at
                FROM pending_operations
                WHERE (subject_ref = ? OR dedupe_key = ?)
                  AND status != 'COMPLETED'
                ORDER BY created_at, operation_id
                """,
                (planning_document_id, f"projection:{planning_document_id}"),
            ).fetchall()
            collection_runs = connection.execute(
                """
                SELECT collection_run_id, status, attempt, failure_code,
                       retryable, started_at, completed_at
                FROM collection_runs
                WHERE planning_document_id = ? AND retryable = 1
                  AND status IN ('SOURCE_UNSTABLE', 'COLLECTION_FAILED')
                ORDER BY started_at DESC LIMIT 5
                """,
                (planning_document_id,),
            ).fetchall()

            blocker_payloads: list[dict[str, Any]] = []
            for blocker in blockers:
                scopes = connection.execute(
                    """
                    SELECT blocked_scope_id, scope_type, target_ref,
                           description, resume_work
                    FROM blocked_scopes WHERE blocker_id = ?
                    ORDER BY blocked_scope_id
                    """,
                    (blocker["blocker_id"],),
                ).fetchall()
                payload = dict(blocker)
                payload["available_work"] = json.loads(
                    payload.pop("available_work_json", "[]") or "[]"
                )
                payload["blocked_scopes"] = [dict(row) for row in scopes]
                blocker_payloads.append(payload)
        finally:
            connection.close()

        return {
            "document": {
                "planning_document_id": document["planning_document_id"],
                "title": document["title"],
                "source_status": document["source_status"],
                "current_snapshot_id": document["current_snapshot_id"],
                "last_collected_at": document["last_collected_at"],
                "attention_required": bool(document["attention_required"]),
            },
            "change_sets": [dict(row) for row in change_sets],
            "review_cycle": dict(cycle) if cycle else None,
            "findings": [dict(row) for row in findings],
            "open_questions": [self._question_payload(row) for row in questions],
            "blockers": blocker_payloads,
            "final_spec_revision": dict(final_spec) if final_spec else None,
            "pending_operations": [dict(row) for row in pending],
            "retryable_collection_runs": [dict(row) for row in collection_runs],
        }

    @staticmethod
    def _question_payload(row) -> dict[str, Any]:
        payload = dict(row)
        payload["options"] = json.loads(payload.pop("options_json"))
        payload["tradeoffs"] = json.loads(payload.pop("tradeoffs_json"))
        return payload


class RuntimeService:
    RETRY_DELAYS = (5.0, 15.0, 30.0)

    def __init__(
        self,
        workspace: Workspace,
        notion: NotionPort,
        *,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.workspace = workspace
        self.database = workspace.database
        self.notion = notion
        self.sleeper = sleeper

    def collect_document(self, planning_document_id: str) -> dict[str, Any]:
        collector = SourceCollector(
            self.database, self.workspace.content_store, self.notion
        )
        attempts = 1
        result = collector.collect(planning_document_id)
        for delay in self.RETRY_DELAYS:
            if result.status != "SOURCE_UNSTABLE":
                break
            self.sleeper(delay)
            attempts += 1
            result = collector.collect(planning_document_id)
        return {**result.__dict__, "attempts": attempts}

    def collect_all(self) -> list[dict[str, Any]]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT planning_document_id FROM planning_documents
                ORDER BY created_at, planning_document_id
                """
            ).fetchall()
        finally:
            connection.close()
        return [self.collect_document(row["planning_document_id"]) for row in rows]

    def reconcile_pending(self) -> list[dict[str, Any]]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM pending_operations
                WHERE operation_type = 'PROJECT_DOCUMENT'
                  AND status IN ('PENDING', 'FAILED')
                ORDER BY created_at, operation_id
                """
            ).fetchall()
        finally:
            connection.close()

        results: list[dict[str, Any]] = []
        for row in rows:
            with self.database.transaction() as connection:
                current = connection.execute(
                    "SELECT status FROM pending_operations WHERE operation_id = ?",
                    (row["operation_id"],),
                ).fetchone()
                if current is None or current["status"] not in {"PENDING", "FAILED"}:
                    continue
                connection.execute(
                    """
                    UPDATE pending_operations
                    SET status = 'RUNNING', failure_code = NULL
                    WHERE operation_id = ?
                    """,
                    (row["operation_id"],),
                )
            try:
                projection = ProjectionService(self.database, self.notion).sync(
                    row["subject_ref"]
                )
                results.append(
                    {
                        "operation_id": row["operation_id"],
                        "status": "COMPLETED",
                        "result": projection,
                    }
                )
            except SpecTraceError as exc:
                results.append(
                    {
                        "operation_id": row["operation_id"],
                        "status": "FAILED",
                        "error": str(exc),
                    }
                )
        return results

    def run_cycle(self) -> dict[str, Any]:
        recovery = self.reconcile_pending()
        collections = self.collect_all()
        return {"recovery": recovery, "collections": collections}

    @staticmethod
    def validate_interval(interval: float) -> float:
        if interval <= 0:
            raise ValidationError("watch interval must be greater than zero")
        return interval
