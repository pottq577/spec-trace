from __future__ import annotations

import unittest

from spec_trace.cli import build_parser


class CliContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_source_sync_requires_database_and_data_source(self) -> None:
        source = self.parser.parse_args(
            [
                "source",
                "sync",
                "--database-id",
                "db-id",
                "--data-source-id",
                "ds-id",
            ]
        )
        self.assertEqual(source.source_command, "sync")
        self.assertEqual(source.database_id, "db-id")
        self.assertEqual(source.data_source_id, "ds-id")
        self.assertEqual(source.parent_property, "상위 항목")

    def test_collect_requires_document_or_all(self) -> None:
        document = self.parser.parse_args(["collect", "--document", "doc-1"])
        collect_all = self.parser.parse_args(["collect", "--all"])
        self.assertEqual(document.document, "doc-1")
        self.assertTrue(collect_all.all)
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["collect"])

    def test_status_watch_and_live_smoke_are_available(self) -> None:
        status = self.parser.parse_args(["status", "--document", "doc-1"])
        watch = self.parser.parse_args(["watch", "--once", "--interval", "1"])
        smoke = self.parser.parse_args(["live-smoke", "--allow-write"])
        self.assertEqual(status.command, "status")
        self.assertTrue(watch.once)
        self.assertEqual(watch.interval, 1.0)
        self.assertTrue(smoke.allow_write)


if __name__ == "__main__":
    unittest.main()
