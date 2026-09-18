from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .content_store import ContentStore
from .db import Database
from .errors import ResourceNotFound, StateConflict, ValidationError
from .evidence import EvidenceService
from .pending import PendingOperationService
from .util import new_id, utc_now


class ReviewService:
    def __init__(self, database: Database):
        self.database = database
        self.pending = PendingOperationService(database)

    def adopt_developer_decision(self, finding_id: str, payload: dict[str, Any]) -> str:
        adopted_option = str(payload.get("adopted_option") or "").strip()
        rationale = str(payload.get("rationale") or "").strip()
        evidence = payload.get("evidence_refs") or []
        if not adopted_option or not rationale or not evidence:
            raise ValidationError("Decision requires adopted_option, rationale, and evidence_refs")
        with self.database.transaction() as connection:
            finding = self._finding(connection, finding_id)
            if finding["decision_owner"] != "DEVELOPER":
                raise StateConflict("Finding requires planner decision")
            decision_id = self._insert_decision(
                connection, finding, "DEVELOPER", adopted_option, rationale,
                evidence, payload.get("supersedes_decision_id")
            )
            active_blocker = connection.execute(
                "SELECT 1 FROM blockers WHERE finding_id = ? AND status = 'ACTIVE'",
                (finding_id,),
            ).fetchone()
            connection.execute(
                "UPDATE findings SET status = ? WHERE finding_id = ?",
                ("RESOLVING" if active_blocker else "RESOLVED", finding_id),
            )
            self._recalculate_cycle(connection, finding["review_cycle_id"])
            planning_document_id = self._planning_document_for_cycle(connection, finding["review_cycle_id"])
        self.pending.schedule("PROJECT_DOCUMENT", planning_document_id, f"projection:{planning_document_id}")
        return decision_id

    def publish_question(self, finding_id: str, payload: dict[str, Any]) -> str:
        question = str(payload.get("question") or "").strip()
        options = payload.get("options") or []
        tradeoffs = payload.get("tradeoffs") or []
        recommendation = str(payload.get("developer_recommendation") or "").strip()
        if not question or not options or not recommendation:
            raise ValidationError("OpenQuestion requires question, options, and developer_recommendation")
        with self.database.transaction() as connection:
            finding = self._finding(connection, finding_id)
            if finding["decision_owner"] != "PLANNER":
                raise StateConflict("Finding is owned by developer")
            existing = connection.execute(
                """
                SELECT * FROM open_questions
                WHERE finding_id = ? AND status IN ('OPEN','ANSWERED','VERIFYING','REOPENED')
                ORDER BY created_at DESC LIMIT 1
                """,
                (finding_id,),
            ).fetchone()
            if existing:
                return existing["open_question_id"]
            question_id = new_id()
            connection.execute(
                """
                INSERT INTO open_questions(
                    open_question_id, finding_id, question, options_json, tradeoffs_json,
                    developer_recommendation, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?)
                """,
                (
                    question_id, finding_id, question,
                    json.dumps(options, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(tradeoffs, ensure_ascii=False, separators=(",", ":")),
                    recommendation, utc_now(),
                ),
            )
            connection.execute("UPDATE findings SET status = 'RESOLVING' WHERE finding_id = ?", (finding_id,))
            connection.execute(
                "UPDATE review_cycles SET status = 'AWAITING_PLANNER' WHERE review_cycle_id = ?",
                (finding["review_cycle_id"],),
            )
            planning_document_id = self._planning_document_for_cycle(connection, finding["review_cycle_id"])
        self.pending.schedule("PROJECT_DOCUMENT", planning_document_id, f"projection:{planning_document_id}")
        return question_id

    def set_blocker(self, finding_id: str, payload: dict[str, Any]) -> str:
        reason = str(payload.get("reason") or "").strip()
        resume_condition = str(payload.get("resume_condition") or "").strip()
        scopes = payload.get("scopes") or []
        available_work = payload.get("available_work") or []
        if not reason or not resume_condition or not scopes:
            raise ValidationError("Blocker requires reason, resume_condition, and at least one scope")
        with self.database.transaction() as connection:
            finding = self._finding(connection, finding_id)
            existing = connection.execute(
                "SELECT blocker_id FROM blockers WHERE finding_id = ? AND status = 'ACTIVE'",
                (finding_id,),
            ).fetchone()
            if existing:
                return existing["blocker_id"]
            blocker_id = new_id()
            connection.execute(
                """
                INSERT INTO blockers(
                    blocker_id, finding_id, reason, resume_condition, status,
                    created_at, available_work_json
                ) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)
                """,
                (
                    blocker_id, finding_id, reason, resume_condition, utc_now(),
                    json.dumps(available_work, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            for scope in scopes:
                scope_type = scope.get("scope_type")
                if scope_type not in {"FEATURE", "DESIGN", "IMPLEMENTATION", "WORK_ITEM"}:
                    raise ValidationError(f"invalid BlockedScope type: {scope_type}")
                connection.execute(
                    """
                    INSERT INTO blocked_scopes(
                        blocked_scope_id, blocker_id, scope_type, target_ref, description, resume_work
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(), blocker_id, scope_type,
                        str(scope.get("target_ref") or ""),
                        str(scope.get("description") or ""),
                        str(scope.get("resume_work") or ""),
                    ),
                )
            connection.execute(
                "UPDATE findings SET blocking = 1, status = CASE WHEN status = 'RESOLVED' THEN 'REOPENED' ELSE status END WHERE finding_id = ?",
                (finding_id,),
            )
            self._recalculate_cycle(connection, finding["review_cycle_id"])
            planning_document_id = self._planning_document_for_cycle(connection, finding["review_cycle_id"])
        self.pending.schedule("PROJECT_DOCUMENT", planning_document_id, f"projection:{planning_document_id}")
        return blocker_id

    def resolve_blocker(self, blocker_id: str, reason: str | None = None) -> list[str]:
        with self.database.transaction() as connection:
            blocker = connection.execute("SELECT * FROM blockers WHERE blocker_id = ?", (blocker_id,)).fetchone()
            if blocker is None:
                raise ResourceNotFound(f"Blocker not found: {blocker_id}")
            scopes = connection.execute(
                "SELECT resume_work FROM blocked_scopes WHERE blocker_id = ? ORDER BY blocked_scope_id",
                (blocker_id,),
            ).fetchall()
            connection.execute("UPDATE blockers SET status = 'RESOLVED' WHERE blocker_id = ?", (blocker_id,))
            finding = self._finding(connection, blocker["finding_id"])
            decision = connection.execute(
                "SELECT 1 FROM decisions WHERE finding_id = ? AND status = 'ADOPTED'",
                (finding["finding_id"],),
            ).fetchone()
            if decision:
                connection.execute("UPDATE findings SET status = 'RESOLVED' WHERE finding_id = ?", (finding["finding_id"],))
            self._recalculate_cycle(connection, finding["review_cycle_id"])
            planning_document_id = self._planning_document_for_cycle(connection, finding["review_cycle_id"])
            resume = [row["resume_work"] for row in scopes if row["resume_work"]]
        self.pending.schedule("PROJECT_DOCUMENT", planning_document_id, f"projection:{planning_document_id}")
        for target in resume:
            self.pending.schedule("RESUME_WORK", target, f"resume:{target}")
        return resume

    def verify_answer(self, planner_answer_id: str, payload: dict[str, Any], *, reopen: bool = False) -> str | None:
        with self.database.transaction() as connection:
            answer = connection.execute(
                """
                SELECT pa.*, oq.finding_id, oq.open_question_id, oq.status AS question_status
                FROM planner_answers pa JOIN open_questions oq ON oq.open_question_id = pa.open_question_id
                WHERE pa.planner_answer_id = ?
                """,
                (planner_answer_id,),
            ).fetchone()
            if answer is None:
                raise ResourceNotFound(f"PlannerAnswer not found: {planner_answer_id}")
            finding = self._finding(connection, answer["finding_id"])
            if reopen:
                connection.execute(
                    "UPDATE open_questions SET status = 'REOPENED' WHERE open_question_id = ?",
                    (answer["open_question_id"],),
                )
                connection.execute("UPDATE findings SET status = 'REOPENED' WHERE finding_id = ?", (finding["finding_id"],))
                connection.execute(
                    "UPDATE review_cycles SET status = 'AWAITING_PLANNER' WHERE review_cycle_id = ?",
                    (finding["review_cycle_id"],),
                )
                return None
            adopted_option = str(payload.get("adopted_option") or "").strip()
            rationale = str(payload.get("rationale") or "").strip()
            evidence = payload.get("evidence_refs") or []
            if not adopted_option or not rationale or not evidence:
                raise ValidationError("answer verification requires adopted_option, rationale, and evidence_refs")
            connection.execute(
                "UPDATE open_questions SET status = 'VERIFYING' WHERE open_question_id = ?",
                (answer["open_question_id"],),
            )
            decision_id = self._insert_decision(
                connection, finding, "PLANNER", adopted_option, rationale,
                evidence, payload.get("supersedes_decision_id")
            )
            connection.execute(
                "UPDATE open_questions SET status = 'RESOLVED' WHERE open_question_id = ?",
                (answer["open_question_id"],),
            )
            active_blocker = connection.execute(
                "SELECT 1 FROM blockers WHERE finding_id = ? AND status = 'ACTIVE'",
                (finding["finding_id"],),
            ).fetchone()
            connection.execute(
                "UPDATE findings SET status = ? WHERE finding_id = ?",
                ("RESOLVING" if active_blocker else "RESOLVED", finding["finding_id"]),
            )
            self._recalculate_cycle(connection, finding["review_cycle_id"])
            planning_document_id = self._planning_document_for_cycle(connection, finding["review_cycle_id"])
        self.pending.schedule("PROJECT_DOCUMENT", planning_document_id, f"projection:{planning_document_id}")
        return decision_id

    def _insert_decision(self, connection, finding, owner, adopted_option, rationale, evidence, supersedes) -> str:
        if supersedes:
            previous = connection.execute(
                "SELECT finding_id FROM decisions WHERE decision_id = ?", (supersedes,)
            ).fetchone()
            if previous is None or previous["finding_id"] != finding["finding_id"]:
                raise ValidationError("superseded Decision must belong to the same Finding")
        decision_id = new_id()
        connection.execute(
            """
            INSERT INTO decisions(
                decision_id, finding_id, owner, adopted_option, rationale,
                supersedes_decision_id, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'ADOPTED', ?)
            """,
            (decision_id, finding["finding_id"], owner, adopted_option, rationale, supersedes, utc_now()),
        )
        if supersedes:
            connection.execute("UPDATE decisions SET status = 'SUPERSEDED' WHERE decision_id = ?", (supersedes,))
        for item in evidence:
            evidence_id = EvidenceService(self.database).ensure(item["type"], item["payload"], connection=connection)
            connection.execute(
                "INSERT INTO decision_evidence(decision_id, evidence_ref_id) VALUES (?, ?)",
                (decision_id, evidence_id),
            )
        return decision_id

    def _recalculate_cycle(self, connection, cycle_id: str) -> None:
        questions = connection.execute(
            """
            SELECT oq.status FROM open_questions oq JOIN findings f ON f.finding_id = oq.finding_id
            WHERE f.review_cycle_id = ? AND oq.status IN ('OPEN','REOPENED','ANSWERED','VERIFYING')
            """,
            (cycle_id,),
        ).fetchall()
        if any(row["status"] in {"OPEN", "REOPENED"} for row in questions):
            status = "AWAITING_PLANNER"
        elif any(row["status"] in {"ANSWERED", "VERIFYING"} for row in questions):
            status = "REVERIFYING"
        else:
            unresolved = connection.execute(
                """
                SELECT COUNT(*) AS c FROM findings
                WHERE review_cycle_id = ? AND status NOT IN ('RESOLVED','SUPERSEDED')
                """,
                (cycle_id,),
            ).fetchone()["c"]
            blockers = connection.execute(
                """
                SELECT COUNT(*) AS c FROM blockers b JOIN findings f ON f.finding_id = b.finding_id
                WHERE f.review_cycle_id = ? AND b.status = 'ACTIVE'
                """,
                (cycle_id,),
            ).fetchone()["c"]
            status = "COMPLETED" if unresolved == 0 and blockers == 0 else "REVIEWING"
        connection.execute("UPDATE review_cycles SET status = ? WHERE review_cycle_id = ?", (status, cycle_id))

    @staticmethod
    def _planning_document_for_cycle(connection, cycle_id: str) -> str:
        return connection.execute(
            "SELECT planning_document_id FROM review_cycles WHERE review_cycle_id = ?", (cycle_id,)
        ).fetchone()["planning_document_id"]

    @staticmethod
    def _finding(connection, finding_id: str):
        row = connection.execute("SELECT * FROM findings WHERE finding_id = ?", (finding_id,)).fetchone()
        if row is None:
            raise ResourceNotFound(f"Finding not found: {finding_id}")
        return row


class FinalSpecService:
    def __init__(self, database: Database, content_store: ContentStore):
        self.database = database
        self.content_store = content_store

    def create(self, planning_document_id: str, content_path: Path) -> str:
        if not content_path.exists():
            raise ResourceNotFound(f"FinalSpec content not found: {content_path}")
        content = content_path.read_text(encoding="utf-8")
        content_hash, content_ref = self.content_store.put_text(content, ".md")
        now = utc_now()
        with self.database.transaction() as connection:
            document = connection.execute(
                "SELECT * FROM planning_documents WHERE planning_document_id = ?",
                (planning_document_id,),
            ).fetchone()
            if document is None or document["current_snapshot_id"] is None:
                raise StateConflict("PlanningDocument has no current Snapshot")
            unresolved = connection.execute(
                """
                SELECT COUNT(*) AS c FROM findings f JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                WHERE rc.planning_document_id = ? AND f.status NOT IN ('RESOLVED','SUPERSEDED')
                """,
                (planning_document_id,),
            ).fetchone()["c"]
            active_blockers = connection.execute(
                """
                SELECT COUNT(*) AS c FROM blockers b JOIN findings f ON f.finding_id = b.finding_id
                JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                WHERE rc.planning_document_id = ? AND b.status = 'ACTIVE'
                """,
                (planning_document_id,),
            ).fetchone()["c"]
            if unresolved or active_blockers:
                raise StateConflict("FinalSpec requires all Findings and active Blockers to be resolved")
            final_spec = connection.execute(
                "SELECT * FROM final_specs WHERE planning_document_id = ?", (planning_document_id,)
            ).fetchone()
            final_spec_id = final_spec["final_spec_id"] if final_spec else new_id()
            previous_revision_id = final_spec["current_revision_id"] if final_spec else None
            if final_spec is None:
                connection.execute(
                    "INSERT INTO final_specs(final_spec_id, planning_document_id, created_at) VALUES (?, ?, ?)",
                    (final_spec_id, planning_document_id, now),
                )
            revision_id = new_id()
            connection.execute(
                """
                INSERT INTO final_spec_revisions(
                    final_spec_revision_id, final_spec_id, previous_revision_id,
                    content_ref, content_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (revision_id, final_spec_id, previous_revision_id, content_ref, content_hash, now),
            )
            connection.execute(
                "INSERT INTO final_spec_revision_snapshots(final_spec_revision_id, planning_document_snapshot_id) VALUES (?, ?)",
                (revision_id, document["current_snapshot_id"]),
            )
            decisions = connection.execute(
                """
                SELECT d.decision_id FROM decisions d JOIN findings f ON f.finding_id = d.finding_id
                JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                WHERE rc.planning_document_id = ? AND d.status = 'ADOPTED'
                """,
                (planning_document_id,),
            ).fetchall()
            for decision in decisions:
                connection.execute(
                    "INSERT INTO final_spec_revision_decisions(final_spec_revision_id, decision_id) VALUES (?, ?)",
                    (revision_id, decision["decision_id"]),
                )
            connection.execute(
                "UPDATE final_specs SET current_revision_id = ? WHERE final_spec_id = ?",
                (revision_id, final_spec_id),
            )
            return revision_id
