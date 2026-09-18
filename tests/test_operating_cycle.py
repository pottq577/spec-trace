from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spec_trace.config import SettingsService
from spec_trace.runtime import RuntimeService
from spec_trace.workspace import Workspace

from fakes import FakeNotion, menu_page, notion_id


class OperatingCycleTest(unittest.TestCase):
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
        SettingsService(self.workspace).set_notion_source(
            self.database_id,
            self.data_source_id,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_cycle_syncs_source_before_collecting_available_documents(self) -> None:
        first = RuntimeService(
            self.workspace,
            self.fake,
            sleeper=lambda _: None,
        ).run_cycle()

        self.assertEqual(first["source_sync"]["status"], "COMPLETED")
        self.assertEqual(first["source_sync"]["active_pages"], 2)
        self.assertEqual(len(first["collections"]), 2)

        del self.fake.pages[self.child_id]
        second = RuntimeService(
            self.workspace,
            self.fake,
            sleeper=lambda _: None,
        ).run_cycle()

        self.assertEqual(second["source_sync"]["unavailable"], 1)
        self.assertEqual(len(second["collections"]), 1)
        self.assertEqual(len(second["answers"]), 1)

        connection = self.workspace.database.connect()
        try:
            row = connection.execute(
                """
                SELECT source_status FROM planning_documents
                WHERE root_notion_page_id = ?
                """,
                (self.child_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row["source_status"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
