from __future__ import annotations

import fcntl
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from .content_store import ContentStore
from .db import Database


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def state_dir(self) -> Path:
        return self.root / ".spec-trace"

    @property
    def database_path(self) -> Path:
        return self.state_dir / "spec-trace.db"

    @property
    def content_dir(self) -> Path:
        return self.state_dir / "content"

    @property
    def analysis_requests_dir(self) -> Path:
        return self.state_dir / "analysis" / "requests"

    @property
    def analysis_responses_dir(self) -> Path:
        return self.state_dir / "analysis" / "responses"

    @property
    def exports_dir(self) -> Path:
        return self.state_dir / "exports"

    @property
    def mirror_dir(self) -> Path:
        return self.state_dir / "mirror"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "workspace.lock"

    def initialize(self) -> None:
        for directory in (
            self.state_dir,
            self.content_dir,
            self.analysis_requests_dir,
            self.analysis_responses_dir,
            self.exports_dir,
            self.mirror_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.database.migrate()

    @property
    def database(self) -> Database:
        return Database(self.database_path)

    @property
    def content_store(self) -> ContentStore:
        return ContentStore(self.content_dir)


class WorkspaceLock:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self._handle: IO[str] | None = None

    def __enter__(self) -> "WorkspaceLock":
        self.workspace.state_dir.mkdir(parents=True, exist_ok=True)
        self._handle = self.workspace.lock_path.open("a+", encoding="utf-8")
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None
