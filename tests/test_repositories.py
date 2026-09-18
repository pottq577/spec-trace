import subprocess
import tempfile
import unittest
from pathlib import Path

from spec_trace.repositories import RepositoryService
from spec_trace.workspace import Workspace


class RepositoryServiceTest(unittest.TestCase):
    def test_add_is_idempotent_and_resolves_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "product"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Test"], check=True
            )
            (repo / "README.md").write_text("hello\n")
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-qm", "init"], check=True
            )

            workspace = Workspace(root / "workspace")
            workspace.initialize()
            service = RepositoryService(workspace.database, workspace.root)
            first = service.add("product", str(repo))
            second = service.add("renamed", str(repo))
            self.assertEqual(first.repository_id, second.repository_id)
            self.assertEqual(len(first.default_ref), 40)
            self.assertEqual(len(service.list()), 1)
