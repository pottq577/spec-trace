from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spec_trace.collector import SourceCollector
from spec_trace.planning_documents import PlanningDocumentService
from spec_trace.workspace import Workspace

from fakes import FakeNotion, child_page, notion_id, page, paragraph


class CollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.temp.name))
        self.workspace.initialize()
        self.database_id = notion_id(900)
        self.root_id = notion_id(1)
        self.child_id = notion_id(2)
        self.data_source_id = notion_id(901)
        self.fake = FakeNotion(
            pages={
                self.root_id: page(self.root_id, "근무유형별근무기준등록", data_source_id=self.data_source_id),
                self.child_id: page(self.child_id, "설명보완서"),
            },
            children={
                self.root_id: [
                    paragraph(notion_id(101), "주 40시간"),
                    child_page(self.child_id, "설명보완서"),
                ],
                self.child_id: [paragraph(notion_id(201), "보상휴가 정책")],
            },
        )
        self.fake.root_page_id = self.root_id
        self.fake.databases[self.database_id] = {"id": self.database_id, "data_sources": [{"id": self.data_source_id, "name": "기획"}]}
        self.document = PlanningDocumentService(self.workspace.database, self.fake).register(
            self.database_id, self.root_id
        )
        self.collector = SourceCollector(
            self.workspace.database, self.workspace.content_store, self.fake
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_initial_snapshot_then_unchanged(self) -> None:
        first = self.collector.collect(self.document.planning_document_id)
        second = self.collector.collect(self.document.planning_document_id)
        self.assertEqual(first.status, "SNAPSHOT_CREATED")
        self.assertIsNone(first.change_set_id)
        self.assertEqual(second.status, "UNCHANGED")
        self.assertEqual(second.snapshot_id, first.snapshot_id)

        connection = self.workspace.database.connect()
        try:
            count = connection.execute("SELECT COUNT(*) AS c FROM planning_snapshot_pages").fetchone()["c"]
        finally:
            connection.close()
        self.assertEqual(count, 2)

    def test_changed_content_creates_change_set_and_physical_change(self) -> None:
        first = self.collector.collect(self.document.planning_document_id)
        self.fake.children[self.child_id] = [paragraph(notion_id(201), "보상휴가 정책 변경")]
        self.fake.pages[self.child_id]["last_edited_time"] = "2026-09-18T00:01:00.000Z"

        second = self.collector.collect(self.document.planning_document_id)
        self.assertEqual(second.status, "SNAPSHOT_CREATED")
        self.assertNotEqual(second.snapshot_id, first.snapshot_id)
        self.assertIsNotNone(second.change_set_id)

        connection = self.workspace.database.connect()
        try:
            rows = connection.execute(
                "SELECT notion_page_id, change_type FROM physical_changes WHERE change_set_id = ?",
                (second.change_set_id,),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual([(row["notion_page_id"], row["change_type"]) for row in rows], [(self.child_id, "CONTENT_CHANGED")])

    def test_unstable_capture_does_not_publish_snapshot(self) -> None:
        first = self.collector.collect(self.document.planning_document_id)

        def mutate(fake: FakeNotion) -> None:
            fake.children[self.root_id][0] = paragraph(notion_id(101), "주 35시간")
            fake.pages[self.root_id]["last_edited_time"] = "2026-09-18T00:02:00.000Z"

        self.fake.root_retrieve_count = 0
        self.fake.on_second_capture = mutate
        unstable = self.collector.collect(self.document.planning_document_id)
        self.assertEqual(unstable.status, "SOURCE_UNSTABLE")

        current = PlanningDocumentService(self.workspace.database, self.fake).get(
            self.document.planning_document_id
        )
        self.assertEqual(current.current_snapshot_id, first.snapshot_id)

    def test_system_review_page_is_excluded(self) -> None:
        review_page_id = notion_id(3)
        self.fake.pages[review_page_id] = page(review_page_id, "개발 검토")
        self.fake.children[review_page_id] = [paragraph(notion_id(301), "시스템 출력")]
        self.fake.children[self.root_id].append(child_page(review_page_id, "개발 검토"))
        with self.workspace.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO notion_review_pages(planning_document_id, review_page_id, updated_at)
                VALUES (?, ?, ?)
                """,
                (self.document.planning_document_id, review_page_id, "2026-09-18T00:00:00Z"),
            )

        result = self.collector.collect(self.document.planning_document_id)
        connection = self.workspace.database.connect()
        try:
            rows = connection.execute(
                "SELECT notion_page_id FROM planning_snapshot_pages WHERE planning_document_snapshot_id = ?",
                (result.snapshot_id,),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual({row["notion_page_id"] for row in rows}, {self.root_id, self.child_id})
