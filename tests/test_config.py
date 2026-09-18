from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spec_trace.config import SettingsService
from spec_trace.workspace import Workspace

from fakes import notion_id


class SettingsServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name) / "workspaces"
        self.workspace = Workspace(self.base / "spec-trace")
        self.export_root = self.base / "PEOPLO" / "docs" / "PRD_Notion"
        self.export_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_suggests_peoplo_prd_notion_sibling(self) -> None:
        service = SettingsService(self.workspace)

        self.assertEqual(service.suggested_export_root(), str(self.export_root.resolve()))

    def test_persists_notion_source_and_export_root(self) -> None:
        service = SettingsService(self.workspace)
        database_id = notion_id(100)
        data_source_id = notion_id(101)

        service.set_notion_source(database_id, data_source_id)
        service.set_export_root(str(self.export_root))

        settings = service.load()
        self.assertIsNotNone(settings.notion_source)
        self.assertEqual(settings.notion_source.database_id, database_id)
        self.assertEqual(settings.notion_source.data_source_id, data_source_id)
        self.assertEqual(settings.notion_source.parent_property, "상위 항목")
        self.assertEqual(settings.export_root, str(self.export_root.resolve()))


if __name__ == "__main__":
    unittest.main()
