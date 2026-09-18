from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spec_trace.config import SettingsService
from spec_trace.planning_documents import PlanningDocumentService
from spec_trace.runtime import RuntimeService
from spec_trace.source_export import SourceExportService
from spec_trace.workspace import Workspace

from fakes import FakeNotion, child_page, menu_page, notion_id, page, paragraph


class SourceExportServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.temp.name) / "spec-trace")
        self.workspace.initialize()
        self.database_id = notion_id(700)
        self.data_source_id = notion_id(701)
        self.root_id = notion_id(1)
        self.category_id = notion_id(2)
        self.document_id = notion_id(3)
        self.child_page_id = notion_id(4)
        self.fake = FakeNotion(
            pages={
                self.root_id: menu_page(
                    self.root_id,
                    "근태관리",
                    self.data_source_id,
                ),
                self.category_id: menu_page(
                    self.category_id,
                    "근태기준관리",
                    self.data_source_id,
                    self.root_id,
                ),
                self.document_id: menu_page(
                    self.document_id,
                    "근무유형별 근무기준등록",
                    self.data_source_id,
                    self.category_id,
                ),
                self.child_page_id: page(self.child_page_id, "설명보완서"),
            },
            children={
                self.document_id: [
                    paragraph(notion_id(100), "주 40시간"),
                    child_page(self.child_page_id, "설명보완서"),
                ],
                self.child_page_id: [paragraph(notion_id(101), "정책 설명")],
            },
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
                """
                SELECT planning_document_id FROM planning_documents
                WHERE root_notion_page_id = ?
                """,
                (self.document_id,),
            ).fetchone()["planning_document_id"]
        finally:
            connection.close()
        RuntimeService(
            self.workspace,
            self.fake,
            sleeper=lambda _: None,
        ).collect_document(self.planning_document_id)

        self.export_root = Path(self.temp.name) / "PEOPLO" / "docs" / "PRD_Notion"
        self.target_dir = (
            self.export_root
            / "근태관리"
            / "1_근태기준관리"
            / "4_근무유형별근무기준등록"
        )
        self.target_dir.mkdir(parents=True)
        SettingsService(self.workspace).set_export_root(str(self.export_root))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_exports_combined_snapshot_into_existing_numbered_menu_path(self) -> None:
        result = SourceExportService(self.workspace).export(self.planning_document_id)

        path = Path(result["path"])
        self.assertEqual(path.parent, self.target_dir)
        self.assertEqual(path.name, "근무유형별 근무기준등록.md")
        markdown = path.read_text(encoding="utf-8")
        self.assertIn("specTraceManaged: true", markdown)
        self.assertIn("# 근무유형별 근무기준등록", markdown)
        self.assertIn("주 40시간", markdown)
        self.assertIn("## 설명보완서", markdown)
        self.assertIn("정책 설명", markdown)

    def test_does_not_overwrite_unmanaged_markdown(self) -> None:
        unmanaged = self.target_dir / "근무유형별 근무기준등록.md"
        unmanaged.write_text("manual", encoding="utf-8")

        result = SourceExportService(self.workspace).export(self.planning_document_id)

        self.assertEqual(
            Path(result["path"]).name,
            "근무유형별 근무기준등록.notion.md",
        )
        self.assertEqual(unmanaged.read_text(encoding="utf-8"), "manual")


if __name__ == "__main__":
    unittest.main()
