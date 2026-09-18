from __future__ import annotations

from .db import Database
from .util import new_id, utc_now


class PendingOperationService:
    def __init__(self, database: Database):
        self.database = database

    def schedule(self, operation_type: str, subject_ref: str, dedupe_key: str) -> str:
        now = utc_now()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT operation_id FROM pending_operations WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE pending_operations
                    SET status = 'PENDING', available_at = ?, failure_code = NULL, completed_at = NULL
                    WHERE operation_id = ? AND status != 'RUNNING'
                    """,
                    (now, existing["operation_id"]),
                )
                return existing["operation_id"]
            operation_id = new_id()
            connection.execute(
                """
                INSERT INTO pending_operations(
                    operation_id, operation_type, subject_ref, dedupe_key,
                    status, attempt, available_at, created_at
                ) VALUES (?, ?, ?, ?, 'PENDING', 0, ?, ?)
                """,
                (operation_id, operation_type, subject_ref, dedupe_key, now, now),
            )
            return operation_id

    def complete(self, dedupe_key: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE pending_operations
                SET status = 'COMPLETED', completed_at = ?, failure_code = NULL
                WHERE dedupe_key = ?
                """,
                (utc_now(), dedupe_key),
            )

    def fail(self, dedupe_key: str, failure_code: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE pending_operations
                SET status = 'FAILED', failure_code = ?, attempt = attempt + 1
                WHERE dedupe_key = ?
                """,
                (failure_code, dedupe_key),
            )
