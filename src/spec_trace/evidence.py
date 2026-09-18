from __future__ import annotations

import json
from typing import Any

from .db import Database
from .util import canonical_json_bytes, new_id, sha256_bytes, utc_now


class EvidenceService:
    def __init__(self, database: Database):
        self.database = database

    def ensure(
        self, evidence_type: str, payload: dict[str, Any], connection=None
    ) -> str:
        payload_json = canonical_json_bytes(payload).decode("utf-8")
        payload_hash = sha256_bytes(payload_json.encode("utf-8"))
        owns_connection = connection is None
        if owns_connection:
            connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT evidence_ref_id FROM evidence_refs WHERE evidence_type = ? AND payload_hash = ?",
                (evidence_type, payload_hash),
            ).fetchone()
            if row:
                return row["evidence_ref_id"]
            evidence_ref_id = new_id()
            connection.execute(
                """
                INSERT INTO evidence_refs(evidence_ref_id, evidence_type, payload_json, payload_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (evidence_ref_id, evidence_type, payload_json, payload_hash, utc_now()),
            )
            if owns_connection:
                connection.commit()
            return evidence_ref_id
        finally:
            if owns_connection:
                connection.close()

    @staticmethod
    def decode(row) -> dict[str, Any]:
        return {
            "type": row["evidence_type"],
            "payload": json.loads(row["payload_json"]),
        }
