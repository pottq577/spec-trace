from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spec_trace.errors import ValidationError
from spec_trace.planning_documents import PlanningDocumentService
from spec_trace.web import WebApplication
from spec_trace.workspace import Workspace

from fakes import FakeNotion, menu_page, notion_id


class WebApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.temp.name))
        self.workspace.initialize()
        self.database_id = notion_id(800)
        self.data_source_id = notion_id(801)
        self.root_id = notion_id(10)
        self.child_id = notion_id(11)
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
        PlanningDocumentService(self.workspace.database, self.fake).sync_data_source(
            self.database_id,
            self.data_source_id,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_state_returns_hierarchical_document_tree(self) -> None:
        state = WebApplication(
            self.workspace,
            notion_factory=lambda: self.fake,
        ).state()

        self.assertEqual(len(state["documents"]), 1)
        self.assertEqual(state["documents"][0]["title"], "근태관리")
        self.assertEqual(
            state["documents"][0]["children"][0]["title"],
            "근무유형별 근무기준등록",
        )

    def test_browse_directories_lists_server_folders_within_root(self) -> None:
        browse_root = Path(self.temp.name) / "workspaces"
        (browse_root / "PEOPLO" / "docs").mkdir(parents=True)
        (browse_root / "spec-trace").mkdir()
        (browse_root / ".hidden").mkdir()

        app = WebApplication(
            self.workspace,
            notion_factory=lambda: self.fake,
            browse_root=browse_root,
        )
        root = app.browse_directories()
        self.assertEqual(root["path"], str(browse_root.resolve()))
        self.assertIsNone(root["parent"])
        self.assertEqual(
            [item["name"] for item in root["directories"]],
            ["PEOPLO", "spec-trace"],
        )

        peoplo = app.browse_directories(str(browse_root / "PEOPLO"))
        self.assertEqual(peoplo["parent"], str(browse_root.resolve()))
        self.assertEqual([item["name"] for item in peoplo["directories"]], ["docs"])

    def test_browse_directories_rejects_path_outside_root(self) -> None:
        browse_root = Path(self.temp.name) / "workspaces"
        browse_root.mkdir()
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        app = WebApplication(
            self.workspace,
            notion_factory=lambda: self.fake,
            browse_root=browse_root,
        )

        with self.assertRaises(ValidationError):
            app.browse_directories(str(outside))


if __name__ == "__main__":
    unittest.main()
