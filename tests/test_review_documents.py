from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spec_trace.config import SettingsService
from spec_trace.planning_documents import PlanningDocumentService
from spec_trace.review_documents import ReviewDocumentService
from spec_trace.workspace import Workspace

from fakes import FakeNotion, menu_page, notion_id, paragraph


class ReviewDocumentServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.temp.name) / "spec-trace")
        self.workspace.initialize()
        self.database_id = notion_id(600)
        self.data_source_id = notion_id(601)
        self.page_id = notion_id(1)
        self.fake = FakeNotion(
            pages={
                self.page_id: menu_page(
                    self.page_id,
                    "근무유형별 근무기준등록",
                    self.data_source_id,
                )
            },
            children={},
        )
        self.fake.databases[self.database_id] = {
            "id": self.database_id,
            "data_sources": [{"id": self.data_source_id, "name": "메뉴"}],
        }
        PlanningDocumentService(self.workspace.database, self.fake).sync_data_source(
            self.database_id,
            self.data_source_id,
        )
        connection = self.workspace.database.connect()
        try:
            self.planning_document_id = connection.execute(
                "SELECT planning_document_id FROM planning_documents"
            ).fetchone()["planning_document_id"]
        finally:
            connection.close()

        self.export_root = Path(self.temp.name) / "PEOPLO" / "docs" / "PRD_Notion"
        self.document_dir = self.export_root / "4_근무유형별 근무기준등록"
        self.document_dir.mkdir(parents=True)
        SettingsService(self.workspace).set_export_root(str(self.export_root))
        self.review_path = self.document_dir / "기획확인요청.md"
        self.review_path.write_text(
            "# 기획 확인 요청\n\n## 주간 기준\n\n- 40시간\n- 15시간\n",
            encoding="utf-8",
        )
        self.service = ReviewDocumentService(self.workspace, self.fake)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_publish_update_preserves_answer_and_collects_response(self) -> None:
        first = self.service.publish(
            self.planning_document_id,
            ["기획확인요청.md"],
        )[0]
        self.assertEqual(first["status"], "PUBLISHED")

        connection = self.workspace.database.connect()
        try:
            mapping = connection.execute(
                "SELECT * FROM published_review_documents"
            ).fetchone()
        finally:
            connection.close()
        self.fake.append_block_children(
            mapping["answer_slot_block_id"],
            [paragraph(notion_id(999), "주 40시간으로 확정해주세요.")],
        )

        self.review_path.write_text(
            "# 기획 확인 요청\n\n## 주간 기준\n\n- 40시간\n- 15시간\n\n추가 확인 필요\n",
            encoding="utf-8",
        )
        updated = self.service.publish(
            self.planning_document_id,
            ["기획확인요청.md"],
        )[0]
        self.assertEqual(updated["status"], "UPDATED")
        self.assertEqual(updated["notion_page_id"], first["notion_page_id"])

        responses = self.service.collect_responses(self.planning_document_id)
        self.assertEqual(len(responses), 1)
        self.assertIn("주 40시간으로 확정해주세요.", responses[0]["answer"])
        response_path = Path(responses[0]["path"])
        self.assertTrue(response_path.is_file())
        self.assertIn("responses", response_path.parts)

    def test_list_local_excludes_managed_source_and_responses(self) -> None:
        (self.document_dir / "근무유형별 근무기준등록.md").write_text(
            "---\nspecTraceManaged: true\n---\n",
            encoding="utf-8",
        )
        response_dir = self.document_dir / "responses" / "old"
        response_dir.mkdir(parents=True)
        (response_dir / "answer.md").write_text("answer", encoding="utf-8")

        result = self.service.list_local(self.planning_document_id)

        self.assertEqual(
            [item["path"] for item in result["documents"]],
            ["기획확인요청.md"],
        )


if __name__ == "__main__":
    unittest.main()
