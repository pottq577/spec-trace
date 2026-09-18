from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from spec_trace.collector import SourceCollector
from spec_trace.devflow import DevFlowService
from spec_trace.errors import ExternalServiceError, StateConflict
from spec_trace.planning_documents import PlanningDocumentService
from spec_trace.projection import ProjectionService
from spec_trace.repositories import RepositoryService
from spec_trace.review import FinalSpecService, ReviewService
from spec_trace.util import new_id, utc_now
from spec_trace.workspace import Workspace

from fakes import FakeNotion, notion_id, page, paragraph


class ReviewFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.temp.name))
        self.workspace.initialize()
        self.database_id = notion_id(700)
        self.data_source_id = notion_id(701)
        self.root_id = notion_id(20)
        self.fake = FakeNotion(
            pages={self.root_id: page(self.root_id, "근태정책", data_source_id=self.data_source_id)},
            children={self.root_id: [paragraph(notion_id(2001), "주간 근무 정책")]},
        )
        self.fake.root_page_id = self.root_id
        self.fake.databases[self.database_id] = {
            "id": self.database_id,
            "data_sources": [{"id": self.data_source_id, "name": "기획"}],
        }
        self.document = PlanningDocumentService(self.workspace.database, self.fake).register(
            self.database_id, self.root_id
        )
        self.snapshot = SourceCollector(
            self.workspace.database, self.workspace.content_store, self.fake
        ).collect(self.document.planning_document_id)
        self.cycle_id, self.developer_finding_id, self.planner_finding_id = self._seed_findings()
        self.review = ReviewService(self.workspace.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _seed_findings(self) -> tuple[str, str, str]:
        cycle_id = new_id()
        developer_finding_id = new_id()
        planner_finding_id = new_id()
        with self.workspace.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO review_cycles(
                    review_cycle_id, planning_document_id, target_planning_snapshot_id,
                    review_type, status, created_at
                ) VALUES (?, ?, ?, 'INITIAL', 'REVIEWING', ?)
                """,
                (cycle_id, self.document.planning_document_id, self.snapshot.snapshot_id, utc_now()),
            )
            connection.execute(
                """
                INSERT INTO findings(
                    finding_id, review_cycle_id, finding_type, decision_owner,
                    blocking, summary, status, created_at
                ) VALUES (?, ?, 'POLICY_CONFLICT', 'DEVELOPER', 0, ?, 'OPEN', ?)
                """,
                (developer_finding_id, cycle_id, "기존 근태 정책 재사용", utc_now()),
            )
            connection.execute(
                """
                INSERT INTO findings(
                    finding_id, review_cycle_id, finding_type, decision_owner,
                    blocking, summary, status, created_at
                ) VALUES (?, ?, 'REQUIREMENT_GAP', 'PLANNER', 1, ?, 'OPEN', ?)
                """,
                (planner_finding_id, cycle_id, "공휴일 처리 기준 누락", utc_now()),
            )
        return cycle_id, developer_finding_id, planner_finding_id

    @staticmethod
    def _source_evidence(snapshot_id: str) -> list[dict]:
        return [{"type": "SOURCE", "payload": {"planning_document_snapshot_id": snapshot_id}}]

    def test_projection_answer_final_spec_and_devflow_round_trip(self) -> None:
        developer_decision = self.review.adopt_developer_decision(
            self.developer_finding_id,
            {
                "adopted_option": "기존 정책 재사용",
                "rationale": "중복 정책을 만들지 않음",
                "evidence_refs": self._source_evidence(self.snapshot.snapshot_id),
            },
        )
        question_id = self.review.publish_question(
            self.planner_finding_id,
            {
                "question": "공휴일 포함 시 기준시간은?",
                "options": ["15시간 유지", "근무일수 비례"],
                "tradeoffs": ["정책 일관성", "근무일 반영"],
                "developer_recommendation": "15시간 유지",
            },
        )
        blocker_id = self.review.set_blocker(
            self.planner_finding_id,
            {
                "reason": "공휴일 정책 미확정",
                "resume_condition": "기획자 정책 확정",
                "available_work": ["조회 인터페이스"],
                "scopes": [
                    {
                        "scope_type": "IMPLEMENTATION",
                        "target_ref": "weekly-time",
                        "description": "주간 기준시간 계산",
                        "resume_work": "WORK-weekly-time",
                    }
                ],
            },
        )

        projection = ProjectionService(self.workspace.database, self.fake)
        first_sync = projection.sync(self.document.planning_document_id)
        second_sync = projection.sync(self.document.planning_document_id)
        self.assertEqual(first_sync["decisions_projected"], 1)
        self.assertEqual(first_sync["questions_projected"], 1)
        self.assertEqual(second_sync["decisions_projected"], 0)
        self.assertEqual(second_sync["questions_projected"], 0)

        connection = self.workspace.database.connect()
        try:
            review_pages = connection.execute("SELECT COUNT(*) AS c FROM notion_review_pages").fetchone()["c"]
            question_mapping = connection.execute(
                "SELECT * FROM notion_question_blocks WHERE open_question_id = ?", (question_id,)
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(review_pages, 1)

        # The system-owned review page must not change the source Snapshot.
        collected = SourceCollector(
            self.workspace.database, self.workspace.content_store, self.fake
        ).collect(self.document.planning_document_id)
        self.assertEqual(collected.status, "UNCHANGED")

        self.fake.append_block_children(
            question_mapping["answer_slot_block_id"],
            [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "15시간 유지"}, "plain_text": "15시간 유지"}]}}],
        )
        answered = projection.sync(self.document.planning_document_id)
        self.assertEqual(answered["answers_collected"], 1)
        repeated = projection.sync(self.document.planning_document_id)
        self.assertEqual(repeated["answers_collected"], 0)

        connection = self.workspace.database.connect()
        try:
            planner_answer = connection.execute(
                "SELECT * FROM planner_answers WHERE open_question_id = ?", (question_id,)
            ).fetchone()
            decisions_before_verify = connection.execute(
                "SELECT COUNT(*) AS c FROM decisions WHERE owner = 'PLANNER'"
            ).fetchone()["c"]
        finally:
            connection.close()
        self.assertEqual(decisions_before_verify, 0)

        planner_decision = self.review.verify_answer(
            planner_answer["planner_answer_id"],
            {
                "adopted_option": "15시간 유지",
                "rationale": "기존 주간 정책과 일관됨",
                "evidence_refs": self._source_evidence(self.snapshot.snapshot_id),
            },
        )
        self.assertIsNotNone(planner_decision)

        final_path = self.workspace.root / "final-spec.md"
        final_path.write_text("# 최종설계\n\n공휴일에도 주 15시간 기준을 유지한다.\n", encoding="utf-8")
        final_service = FinalSpecService(self.workspace.database, self.workspace.content_store)
        with self.assertRaises(StateConflict):
            final_service.create(self.document.planning_document_id, final_path)

        resume = self.review.resolve_blocker(blocker_id, "정책 확정")
        self.assertEqual(resume, ["WORK-weekly-time"])
        revision_id = final_service.create(self.document.planning_document_id, final_path)

        repo_dir = self.workspace.root / "repo"
        repo_dir.mkdir()
        subprocess.run(["git", "init", "-q", str(repo_dir)], check=True)
        subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test"], check=True)
        (repo_dir / "service.py").write_text("POLICY = 15\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo_dir), "add", "service.py"], check=True)
        subprocess.run(["git", "-C", str(repo_dir), "commit", "-qm", "implement"], check=True)
        repository = RepositoryService(self.workspace.database, self.workspace.root).add("fixture", str(repo_dir))
        commit_sha = subprocess.check_output(["git", "-C", str(repo_dir), "rev-parse", "HEAD"], text=True).strip()

        devflow = DevFlowService(
            self.workspace.database,
            self.workspace.content_store,
            self.workspace.root,
            self.workspace.exports_dir,
        )
        handoff = devflow.export(revision_id)
        for name in ["manifest.json", "final-spec.md", "decisions.json", "blockers.json", "traceability.json", "implementation-receipt.schema.json"]:
            self.assertTrue((handoff / name).exists(), name)

        receipt = {
            "contract_version": "1",
            "final_spec_revision_id": revision_id,
            "repository": repository.repository_id,
            "commit_sha": commit_sha,
            "path_refs": [{"path": "service.py", "symbol": "POLICY"}],
            "work_ref": "fixture/WORK-01",
            "decision_ids": [developer_decision, planner_decision],
            "verified_at": "2026-09-18T03:00:00Z",
        }
        receipt_path = self.workspace.root / "receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        implementation_ref = devflow.import_receipt(receipt_path)
        self.assertTrue(implementation_ref)

    def test_projection_failure_keeps_internal_decision_and_schedules_recovery(self) -> None:
        decision_id = self.review.adopt_developer_decision(
            self.developer_finding_id,
            {
                "adopted_option": "기존 정책 재사용",
                "rationale": "정책 일관성",
                "evidence_refs": self._source_evidence(self.snapshot.snapshot_id),
            },
        )
        self.fake.fail_next_write = True
        with self.assertRaises(ExternalServiceError):
            ProjectionService(self.workspace.database, self.fake).sync(self.document.planning_document_id)

        connection = self.workspace.database.connect()
        try:
            decision = connection.execute("SELECT status FROM decisions WHERE decision_id = ?", (decision_id,)).fetchone()
            pending = connection.execute(
                "SELECT * FROM pending_operations WHERE dedupe_key = ?", (f"projection:{self.document.planning_document_id}",)
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(decision["status"], "ADOPTED")
        self.assertEqual(pending["status"], "FAILED")
