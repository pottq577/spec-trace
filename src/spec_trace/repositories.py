from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .db import Database
from .errors import ResourceNotFound, ValidationError
from .util import new_id, utc_now


@dataclass(frozen=True)
class RepositoryRecord:
    repository_id: str
    name: str
    local_path: str
    remote_identity: str | None
    default_ref: str | None


class RepositoryService:
    def __init__(self, database: Database, workspace_root: Path):
        self.database = database
        self.workspace_root = workspace_root

    def _resolve(self, path: str) -> Path:
        value = Path(path)
        if not value.is_absolute():
            value = (self.workspace_root / value).resolve()
        return value

    def _git(self, path: Path, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(path), *args],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ValidationError(f"not a readable Git repository: {path}") from exc
        return result.stdout.strip()

    def add(self, name: str, path: str) -> RepositoryRecord:
        resolved = self._resolve(path)
        self._git(resolved, "rev-parse", "--git-dir")
        default_ref = self._git(resolved, "rev-parse", "HEAD")
        try:
            remote_identity = self._git(resolved, "remote", "get-url", "origin") or None
        except ValidationError:
            remote_identity = None
        stored_path = str(resolved)
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM repositories WHERE local_path = ?", (stored_path,)
            ).fetchone()
            if existing:
                return self._from_row(existing)
            repository_id = new_id()
            connection.execute(
                """
                INSERT INTO repositories(
                    repository_id, name, local_path, remote_identity,
                    default_ref, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    repository_id,
                    name,
                    stored_path,
                    remote_identity,
                    default_ref,
                    utc_now(),
                ),
            )
        return RepositoryRecord(
            repository_id, name, stored_path, remote_identity, default_ref
        )

    def list(self) -> list[RepositoryRecord]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM repositories ORDER BY name, repository_id"
            ).fetchall()
            return [self._from_row(row) for row in rows]
        finally:
            connection.close()

    def resolve_head(self, repository_id: str) -> str:
        repository = self.get(repository_id)
        return self._git(Path(repository.local_path), "rev-parse", "HEAD")

    def verify_commit(self, repository_id: str, commit_sha: str) -> str:
        repository = self.get(repository_id)
        resolved = self._git(
            Path(repository.local_path), "rev-parse", f"{commit_sha}^{{commit}}"
        )
        return resolved

    def path_exists_at_commit(
        self, repository_id: str, commit_sha: str, path: str
    ) -> bool:
        repository = self.get(repository_id)
        try:
            self._git(
                Path(repository.local_path), "cat-file", "-e", f"{commit_sha}:{path}"
            )
            return True
        except ValidationError:
            return False

    def get(self, repository_id: str) -> RepositoryRecord:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM repositories WHERE repository_id = ?",
                (repository_id,),
            ).fetchone()
            if not row:
                raise ResourceNotFound(f"repository not found: {repository_id}")
            return self._from_row(row)
        finally:
            connection.close()

    @staticmethod
    def _from_row(row) -> RepositoryRecord:
        return RepositoryRecord(
            row["repository_id"],
            row["name"],
            row["local_path"],
            row["remote_identity"],
            row["default_ref"],
        )
