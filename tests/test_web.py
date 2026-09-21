from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from fakes import FakeNotion, menu_page, notion_id, paragraph

from spec_trace.config import SettingsService
from spec_trace.errors import ValidationError
from spec_trace.planning_documents import PlanningDocumentService
from spec_trace.runtime import RuntimeService
from spec_trace.web import HTML, WebApplication
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

    def _create_child_change(self) -> str:
        connection = self.workspace.database.connect()
        try:
            planning_document_id = connection.execute(
                """
                SELECT planning_document_id
                FROM planning_documents
                WHERE root_notion_page_id = ?
                """,
                (self.child_id,),
            ).fetchone()["planning_document_id"]
        finally:
            connection.close()

        runtime = RuntimeService(
            self.workspace,
            self.fake,
            sleeper=lambda _: None,
        )
        runtime.collect_document(planning_document_id)
        self.fake.children[self.child_id] = [
            paragraph(notion_id(899), "주간 기준시간 기본값 40시간")
        ]
        self.fake.pages[self.child_id]["last_edited_time"] = (
            "2026-09-21T00:01:00.000Z"
        )
        runtime.collect_document(planning_document_id)
        return planning_document_id

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

    def test_dashboard_exposes_document_change_filter(self) -> None:
        self.assertIn('id="documentFilter"', HTML)
        self.assertIn('value="changed"', HTML)
        self.assertIn('value="unchanged"', HTML)
        self.assertIn('value="uncollected"', HTML)

    def test_state_exposes_latest_change_summary(self) -> None:
        self._create_child_change()

        state = WebApplication(
            self.workspace,
            notion_factory=lambda: self.fake,
        ).state()
        latest = state["documents"][0]["children"][0]["latest_change"]

        self.assertIsNotNone(latest)
        self.assertEqual(latest["change_count"], 1)
        self.assertEqual(latest["analysis_status"], "PENDING_SOURCE_DIFF")
        self.assertIsNotNone(latest["created_at"])

    def test_state_rolls_up_descendant_change_summary(self) -> None:
        self._create_child_change()

        state = WebApplication(
            self.workspace,
            notion_factory=lambda: self.fake,
        ).state()
        root = state["documents"][0]
        child = root["children"][0]

        self.assertIsNone(root["latest_change"])
        self.assertEqual(root["subtree_change"]["changed_documents"], 1)
        self.assertEqual(
            root["subtree_change"]["latest_created_at"],
            child["latest_change"]["created_at"],
        )
        self.assertEqual(child["subtree_change"]["changed_documents"], 1)

    def test_document_changes_returns_physical_change_history(self) -> None:
        planning_document_id = self._create_child_change()

        result = WebApplication(
            self.workspace,
            notion_factory=lambda: self.fake,
        ).document_changes(planning_document_id)

        self.assertEqual(result["title"], "근무유형별 근무기준등록")
        self.assertEqual(len(result["change_sets"]), 1)
        change_set = result["change_sets"][0]
        self.assertEqual(change_set["change_count"], 1)
        self.assertEqual(change_set["analysis_status"], "PENDING_SOURCE_DIFF")
        self.assertIsNotNone(change_set["baseline_captured_at"])
        self.assertIsNotNone(change_set["target_captured_at"])
        self.assertEqual(
            change_set["physical_changes"][0]["change_type"],
            "CONTENT_CHANGED",
        )
        self.assertEqual(
            change_set["physical_changes"][0]["page_title"],
            "근무유형별 근무기준등록",
        )
        self.assertEqual(
            change_set["physical_changes"][0]["notion_page_id"],
            self.child_id,
        )
        change = change_set["physical_changes"][0]
        self.assertEqual(change["added_lines"], 2)
        self.assertEqual(change["deleted_lines"], 0)
        self.assertFalse(change["content_diff_truncated"])
        self.assertTrue(
            any(
                line == "+주간 기준시간 기본값 40시간"
                for line in change["content_diff"]
            )
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

    def test_export_all_documents_writes_collected_snapshots(self) -> None:
        connection = self.workspace.database.connect()
        try:
            planning_document_id = connection.execute(
                """
                SELECT planning_document_id
                FROM planning_documents
                WHERE root_notion_page_id = ?
                """,
                (self.child_id,),
            ).fetchone()["planning_document_id"]
        finally:
            connection.close()

        RuntimeService(
            self.workspace,
            self.fake,
            sleeper=lambda _: None,
        ).collect_document(planning_document_id)
        export_root = Path(self.temp.name) / "exports"
        export_root.mkdir()
        SettingsService(self.workspace).set_export_root(str(export_root))

        result = WebApplication(
            self.workspace,
            notion_factory=lambda: self.fake,
        ).export_all_documents()

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["exported"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertTrue(Path(result["exports"][0]["path"]).is_file())

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

    def test_cycle_status_exposes_live_progress(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def cycle_runner() -> dict[str, object]:
            started.set()
            release.wait(2.0)
            return {
                "source_sync": {"status": "COMPLETED", "active_pages": 1},
                "recovery": [],
                "collections": [],
                "answers": [],
                "review_responses": [],
            }

        app = WebApplication(self.workspace, cycle_runner=cycle_runner)
        app.start_cycle()
        self.assertTrue(started.wait(1.0))
        app._update_cycle_progress(
            {
                "phase": "collect_all",
                "phase_label": "Notion 문서 확인/수집",
                "current": 37,
                "processed": 36,
                "total": 172,
                "title": "근무유형별 근무기준등록",
            }
        )

        status = app.cycle_status()
        self.assertEqual(status["progress"]["processed"], 36)
        self.assertEqual(status["progress"]["total"], 172)
        self.assertEqual(status["progress"]["title"], "근무유형별 근무기준등록")
        self.assertIsNotNone(status["started_at"])
        release.set()

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
