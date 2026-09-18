from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from fakes import FakeNotion, menu_page, notion_id

from spec_trace.errors import ValidationError
from spec_trace.planning_documents import PlanningDocumentService
from spec_trace.web import WebApplication
from spec_trace.workspace import Workspace


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

    def test_cycle_runs_in_background_and_reuses_running_job(self) -> None:
        started = threading.Event()
        release = threading.Event()
        calls: list[int] = []

        def cycle_runner() -> dict[str, object]:
            calls.append(1)
            started.set()
            release.wait(2.0)
            return {
                "source_sync": {"status": "COMPLETED", "active_pages": 2},
                "recovery": [],
                "collections": [],
                "answers": [],
                "review_responses": [],
            }

        app = WebApplication(
            self.workspace,
            notion_factory=lambda: self.fake,
            cycle_runner=cycle_runner,
        )

        first = app.start_cycle()
        self.assertEqual(first["status"], "RUNNING")
        self.assertTrue(started.wait(1.0))

        second = app.start_cycle()
        self.assertEqual(second["status"], "RUNNING")
        self.assertEqual(len(calls), 1)

        release.set()
        deadline = time.monotonic() + 2.0
        status = app.cycle_status()
        while status["status"] == "RUNNING" and time.monotonic() < deadline:
            time.sleep(0.01)
            status = app.cycle_status()

        self.assertEqual(status["status"], "COMPLETED")
        self.assertEqual(status["result"]["source_sync"]["active_pages"], 2)
        self.assertEqual(len(calls), 1)

    def test_cycle_failure_is_exposed_as_job_status(self) -> None:
        def cycle_runner() -> dict[str, object]:
            raise RuntimeError("boom")

        app = WebApplication(self.workspace, cycle_runner=cycle_runner)
        app.start_cycle()
        deadline = time.monotonic() + 2.0
        status = app.cycle_status()
        while status["status"] == "RUNNING" and time.monotonic() < deadline:
            time.sleep(0.01)
            status = app.cycle_status()

        self.assertEqual(status["status"], "FAILED")
        self.assertEqual(status["error"], "internal error: RuntimeError")


if __name__ == "__main__":
    unittest.main()
