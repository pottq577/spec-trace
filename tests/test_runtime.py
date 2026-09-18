from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spec_trace.pending import PendingOperationService
from spec_trace.planning_documents import PlanningDocumentService
from spec_trace.runtime import RuntimeService, StatusService
from spec_trace.smoke import LiveSmokeService
from spec_trace.workspace import Workspace

from fakes import FakeNotion, notion_id, page, paragraph


class RuntimeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.temp.name))
        self.workspace.initialize()
        self.database_id = notion_id(950)
        self.data_source_id = notion_id(951)
        self.root_id = notion_id(50)
        self.fake = FakeNotion(
            pages={
                self.root_id: page(
                    self.root_id,
                    "근무유형별근무기준등록",
                    data_source_id=self.data_source_id,
                )
            },
            children={self.root_id: [paragraph(notion_id(5001), "주 40시간")]},
        )
        self.fake.root_page_id = self.root_id
        self.fake.databases[self.database_id] = {
            "id": self.database_id,
            "data_sources": [{"id": self.data_source_id, "name": "기획"}],
        }
        self.document = PlanningDocumentService(
            self.workspace.database, self.fake
        ).register(self.database_id, self.root_id)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_collect_document_retries_source_unstable(self) -> None:
        RuntimeService(self.workspace, self.fake, sleeper=lambda _: None).collect_document(
            self.document.planning_document_id
        )

        def mutate(fake: FakeNotion) -> None:
            fake.children[self.root_id][0] = paragraph(notion_id(5001), "주 35시간")
            fake.pages[self.root_id]["last_edited_time"] = "2026-09-18T01:00:00.000Z"

        self.fake.root_retrieve_count = 0
        self.fake.on_second_capture = mutate
        delays: list[float] = []
        result = RuntimeService(
            self.workspace, self.fake, sleeper=delays.append
        ).collect_document(self.document.planning_document_id)

        self.assertEqual(result["status"], "SNAPSHOT_CREATED")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(delays, [5.0])

    def test_run_cycle_reconciles_projection_before_collection(self) -> None:
        PendingOperationService(self.workspace.database).schedule(
            "PROJECT_DOCUMENT",
            self.document.planning_document_id,
            f"projection:{self.document.planning_document_id}",
        )
        result = RuntimeService(
            self.workspace, self.fake, sleeper=lambda _: None
        ).run_cycle()

        self.assertEqual(result["recovery"][0]["status"], "COMPLETED")
        self.assertEqual(result["collections"][0]["status"], "SNAPSHOT_CREATED")
        connection = self.workspace.database.connect()
        try:
            pages = connection.execute(
                "SELECT notion_page_id FROM planning_snapshot_pages"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual([row["notion_page_id"] for row in pages], [self.root_id])

    def test_status_exposes_current_snapshot_and_pending_projection(self) -> None:
        RuntimeService(self.workspace, self.fake, sleeper=lambda _: None).collect_document(
            self.document.planning_document_id
        )
        PendingOperationService(self.workspace.database).schedule(
            "PROJECT_DOCUMENT",
            self.document.planning_document_id,
            f"projection:{self.document.planning_document_id}",
        )

        status = StatusService(self.workspace).document(
            self.document.planning_document_id
        )
        self.assertEqual(status["document"]["source_status"], "AVAILABLE")
        self.assertIsNotNone(status["document"]["current_snapshot_id"])
        self.assertEqual(status["pending_operations"][0]["status"], "PENDING")

    def test_live_smoke_write_is_idempotent_and_does_not_change_source(self) -> None:
        result = LiveSmokeService(self.workspace, self.fake).run(
            self.database_id, self.root_id, allow_write=True
        )

        self.assertTrue(result["write_checked"])
        self.assertEqual(result["post_projection_collection"]["status"], "UNCHANGED")
        self.assertEqual(result["second_projection"]["decisions_projected"], 0)
        self.assertEqual(result["second_projection"]["questions_projected"], 0)


if __name__ == "__main__":
    unittest.main()
