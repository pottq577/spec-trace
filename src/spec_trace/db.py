from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator

from .errors import InvariantViolation
from .util import utc_now


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def migrate(self) -> None:
        connection = self.connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename TEXT PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
            migration_root = resources.files("spec_trace.migrations")
            migrations = sorted(
                item for item in migration_root.iterdir() if item.name.endswith(".sql")
            )
            for item in migrations:
                sql = item.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
                row = connection.execute(
                    "SELECT checksum FROM schema_migrations WHERE filename = ?",
                    (item.name,),
                ).fetchone()
                if row:
                    if row["checksum"] != checksum:
                        raise InvariantViolation(
                            f"applied migration checksum changed: {item.name}"
                        )
                    continue
                escaped_name = item.name.replace("'", "''")
                escaped_checksum = checksum.replace("'", "''")
                escaped_applied_at = utc_now().replace("'", "''")
                script = (
                    "BEGIN IMMEDIATE;\n"
                    + sql
                    + "\nINSERT INTO schema_migrations(filename, checksum, applied_at) "
                    + f"VALUES ('{escaped_name}', '{escaped_checksum}', "
                    + f"'{escaped_applied_at}');\nCOMMIT;"
                )
                connection.executescript(script)
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
