import tempfile
import unittest
from pathlib import Path

from spec_trace.workspace import Workspace


class WorkspaceTest(unittest.TestCase):
    def test_initialize_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Workspace(Path(tmp))
            workspace.initialize()
            workspace.initialize()
            self.assertTrue(workspace.database_path.exists())
            self.assertTrue(workspace.content_dir.is_dir())
            connection = workspace.database.connect()
            try:
                count = connection.execute(
                    "SELECT COUNT(*) AS c FROM schema_migrations"
                ).fetchone()["c"]
            finally:
                connection.close()
            self.assertEqual(count, 1)
