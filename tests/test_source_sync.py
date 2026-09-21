from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fakes import FakeNotion, menu_page, notion_id

from spec_trace.planning_documents import PlanningDocumentService
from spec_trace.workspace import Workspace


class SourceSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.temp.name))
        self.workspace.initialize()
        self.database_id = notion_id(900)
        self.data_source_id = notion_id(901)
        self.root_id = notion_id(1)
        self.child_id = notion_id(2)
        self.fake = FakeNotion(
            pages={
                self.root_id: menu_page(
                    self.root_id,
                    "근태관리",
                    self.data_source_id,
                ),
                self.child_id: menu_page(
                    self.child_id,
                    "근무유형별 근무기준등록",
                    self.data_source_id,
                    self.root_id,
                ),
            },
            children={},
        )
        self.fake.databases[self.database_id] = {
            "id": self.database_id,
            "data_sources": [{"id": self.data_source_id, "name": "메뉴"}],
        }
        self.service = PlanningDocumentService(
            self.workspace.database,
            self.fake,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_sync_registers_menu_rows_and_parent_relation(self) -> None:
        result = self.service.sync_data_source(
            self.database_id,
            self.data_source_id,
        )

        self.assertEqual(result["created"], 2)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["active_pages"], 2)

        connection = self.workspace.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT root_notion_page_id, title, notion_data_source_id,
                       menu_parent_notion_page_id, source_status,
                       source_last_edited_time
                FROM planning_documents
                ORDER BY root_notion_page_id
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["menu_parent_notion_page_id"], None)
        self.assertEqual(rows[1]["menu_parent_notion_page_id"], self.root_id)
        self.assertEqual(rows[1]["notion_data_source_id"], self.data_source_id)
        self.assertEqual(rows[1]["source_status"], "AVAILABLE")
        self.assertEqual(
            rows[1]["source_last_edited_time"],
            "2026-09-18T00:00:00.000Z",
        )

    def test_sync_updates_metadata_and_marks_missing_rows_unavailable(self) -> None:
        first = self.service.sync_data_source(
            self.database_id,
            self.data_source_id,
        )
        self.assertEqual(first["created"], 2)

        self.fake.pages[self.child_id]["properties"]["Name"]["title"][0][
            "plain_text"
        ] = "근무유형별 근무기준등록 v2"
        self.fake.pages[self.child_id]["properties"]["Name"]["title"][0]["text"][
            "content"
        ] = "근무유형별 근무기준등록 v2"
        del self.fake.pages[self.root_id]

        second = self.service.sync_data_source(
            self.database_id,
            self.data_source_id,
        )

        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], 1)
        self.assertEqual(second["unavailable"], 1)

        connection = self.workspace.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT root_notion_page_id, title, source_status
                FROM planning_documents
                ORDER BY root_notion_page_id
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(rows[0]["source_status"], "UNAVAILABLE")
        self.assertEqual(rows[1]["title"], "근무유형별 근무기준등록 v2")
        self.assertEqual(rows[1]["source_status"], "AVAILABLE")


if __name__ == "__main__":
    unittest.main()
