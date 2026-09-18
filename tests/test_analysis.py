from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fakes import FakeNotion, notion_id, page, paragraph

from spec_trace.analysis import AnalysisService
from spec_trace.collector import SourceCollector
from spec_trace.errors import StateConflict
from spec_trace.planning_documents import PlanningDocumentService
from spec_trace.util import sha256_text
from spec_trace.workspace import Workspace


class AnalysisServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.temp.name))
        self.workspace.initialize()
        self.database_id = notion_id(800)
        self.data_source_id = notion_id(801)
        self.root_id = notion_id(10)
        self.fake = FakeNotion(
            pages={
                self.root_id: page(
                    self.root_id, "근태정책", data_source_id=self.data_source_id
                )
            },
            children={self.root_id: [paragraph(notion_id(1001), "주 40시간")]},
        )
        self.fake.root_page_id = self.root_id
        self.fake.databases[self.database_id] = {
            "id": self.database_id,
            "data_sources": [{"id": self.data_source_id, "name": "기획"}],
        }
        self.document = PlanningDocumentService(
            self.workspace.database, self.fake
        ).register(self.database_id, self.root_id)
        self.collector = SourceCollector(
            self.workspace.database, self.workspace.content_store, self.fake
        )
        self.initial = self.collector.collect(self.document.planning_document_id)
        self.fake.children[self.root_id] = [paragraph(notion_id(1001), "주 35시간")]
        self.fake.pages[self.root_id]["last_edited_time"] = "2026-09-18T01:00:00.000Z"
        self.changed = self.collector.collect(self.document.planning_document_id)
        self.service = AnalysisService(
            self.workspace.database,
            self.workspace.content_store,
            self.workspace.root,
            self.workspace.analysis_requests_dir,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _source_diff_response(self) -> Path:
        packet_path = self.service.export_packet(
            "SOURCE_DIFF", self.changed.change_set_id
        )
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        connection = self.workspace.database.connect()
        try:
            physical = connection.execute(
                "SELECT * FROM physical_changes WHERE change_set_id = ?",
                (self.changed.change_set_id,),
            ).fetchone()
        finally:
            connection.close()
        response = packet["expected_output"]
        response["producer"] = {"type": "AI", "ref": "test-agent"}
        response["candidates"] = [
            {
                "candidate_key": "change-1",
                "candidate_type": "CHANGE_ITEM",
                "classification": "POLICY_CHANGED",
                "summary": "주간 기준 시간이 40시간에서 35시간으로 변경됨",
                "physical_change_refs": [physical["physical_change_id"]],
                "source_evidence": [
                    {
                        "side": "BASELINE",
                        "source_page_snapshot_id": physical[
                            "baseline_source_page_snapshot_id"
                        ],
                        "block_path": ["blocks", 0],
                        "field": "paragraph.rich_text",
                        "quote_hash": sha256_text("주 40시간"),
                    },
                    {
                        "side": "TARGET",
                        "source_page_snapshot_id": physical[
                            "target_source_page_snapshot_id"
                        ],
                        "block_path": ["blocks", 0],
                        "field": "paragraph.rich_text",
                        "quote_hash": sha256_text("주 35시간"),
                    },
                ],
            }
        ]
        path = self.workspace.analysis_responses_dir / "source-diff.json"
        path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
        return path

    def test_source_diff_import_does_not_change_domain_until_adopted(self) -> None:
        proposal = self.service.import_proposal(self._source_diff_response())
        connection = self.workspace.database.connect()
        try:
            before = connection.execute(
                "SELECT COUNT(*) AS c FROM change_items"
            ).fetchone()["c"]
        finally:
            connection.close()
        self.assertEqual(before, 0)

        result = self.service.review_candidate(
            proposal.analysis_proposal_id, "change-1", "ADOPT"
        )
        self.assertIn("change_item_id", result["created"])
        connection = self.workspace.database.connect()
        try:
            item = connection.execute("SELECT * FROM change_items").fetchone()
            status = connection.execute(
                "SELECT analysis_status FROM change_sets WHERE change_set_id = ?",
                (self.changed.change_set_id,),
            ).fetchone()["analysis_status"]
        finally:
            connection.close()
        self.assertEqual(item["classification"], "POLICY_CHANGED")
        self.assertEqual(status, "SOURCE_DIFF_ADOPTED")

    def test_impact_adoption_creates_impact_link(self) -> None:
        source = self.service.import_proposal(self._source_diff_response())
        adopted = self.service.review_candidate(
            source.analysis_proposal_id, "change-1", "ADOPT"
        )
        change_item_id = adopted["created"]["change_item_id"]

        packet = json.loads(
            self.service.export_packet("IMPACT", self.changed.change_set_id).read_text(
                encoding="utf-8"
            )
        )
        response = packet["expected_output"]
        response["producer"] = {"type": "AI", "ref": "test-agent"}
        response["candidates"] = [
            {
                "candidate_key": "impact-1",
                "candidate_type": "IMPACT_LINK",
                "change_item_id": change_item_id,
                "target_type": "CODE",
                "target_ref": "weekly-policy-service",
                "assessment": "IMPLEMENTATION_CHANGE_REQUIRED",
                "summary": "주간 기준시간 계산 변경 필요",
                "rationale": "기준시간 상수가 변경됨",
                "proposed_action": "계산 로직 수정",
                "evidence_refs": [],
            }
        ]
        path = self.workspace.analysis_responses_dir / "impact.json"
        path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
        proposal = self.service.import_proposal(path)
        reviewed = self.service.review_candidate(
            proposal.analysis_proposal_id, "impact-1", "ADOPT"
        )
        self.assertIn("impact_link_id", reviewed["created"])

    def test_review_adoption_creates_finding_with_evidence(self) -> None:
        packet = json.loads(
            self.service.export_packet(
                "REVIEW", self.document.planning_document_id
            ).read_text(encoding="utf-8")
        )
        response = packet["expected_output"]
        response["producer"] = {"type": "AI", "ref": "test-agent"}
        response["candidates"] = [
            {
                "candidate_key": "finding-1",
                "candidate_type": "FINDING",
                "finding_type": "REQUIREMENT_GAP",
                "decision_owner": "PLANNER",
                "blocking": True,
                "summary": "35시간 정책의 공휴일 처리 기준이 없음",
                "evidence_refs": [
                    {
                        "type": "SOURCE",
                        "payload": {
                            "planning_document_snapshot_id": self.changed.snapshot_id,
                            "notion_page_id": self.root_id,
                        },
                    }
                ],
            }
        ]
        path = self.workspace.analysis_responses_dir / "review.json"
        path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
        proposal = self.service.import_proposal(path)
        reviewed = self.service.review_candidate(
            proposal.analysis_proposal_id, "finding-1", "ADOPT"
        )
        self.assertIn("finding_id", reviewed["created"])

        connection = self.workspace.database.connect()
        try:
            finding = connection.execute("SELECT * FROM findings").fetchone()
            cycle = connection.execute(
                "SELECT * FROM review_cycles WHERE review_cycle_id = ?",
                (finding["review_cycle_id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(finding["decision_owner"], "PLANNER")
        self.assertEqual(cycle["review_type"], "CHANGE")
        self.assertEqual(cycle["status"], "REVIEWING")

    def test_import_rejects_stale_review_snapshot(self) -> None:
        packet = json.loads(
            self.service.export_packet(
                "REVIEW", self.document.planning_document_id
            ).read_text(encoding="utf-8")
        )
        response = packet["expected_output"]
        response["producer"] = {"type": "AI", "ref": "test-agent"}
        response["candidates"] = [
            {
                "candidate_key": "finding-1",
                "candidate_type": "FINDING",
                "finding_type": "AMBIGUITY",
                "decision_owner": "DEVELOPER",
                "blocking": False,
                "summary": "테스트",
                "evidence_refs": [
                    {
                        "type": "SOURCE",
                        "payload": {"snapshot": self.changed.snapshot_id},
                    }
                ],
            }
        ]
        self.fake.children[self.root_id] = [paragraph(notion_id(1001), "주 30시간")]
        self.fake.pages[self.root_id]["last_edited_time"] = "2026-09-18T02:00:00.000Z"
        self.collector.collect(self.document.planning_document_id)
        path = self.workspace.analysis_responses_dir / "stale.json"
        path.write_text(json.dumps(response), encoding="utf-8")
        with self.assertRaises(StateConflict):
            self.service.import_proposal(path)
