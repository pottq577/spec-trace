from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .content_store import ContentStore
from .db import Database
from .errors import ResourceNotFound, StateConflict, ValidationError
from .repositories import RepositoryService
from .util import new_id, utc_now

CONTRACT_VERSION = "1"


class DevFlowService:
    def __init__(
        self,
        database: Database,
        content_store: ContentStore,
        workspace_root: Path,
        exports_dir: Path,
    ):
        self.database = database
        self.content_store = content_store
        self.workspace_root = workspace_root
        self.exports_dir = exports_dir

    def export(self, final_spec_revision_id: str) -> Path:
        connection = self.database.connect()
        try:
            revision = connection.execute(
                """
                SELECT fsr.*, fs.planning_document_id, fs.current_revision_id
                FROM final_spec_revisions fsr JOIN final_specs fs ON fs.final_spec_id = fsr.final_spec_id
                WHERE fsr.final_spec_revision_id = ?
                """,
                (final_spec_revision_id,),
            ).fetchone()
            if revision is None:
                raise ResourceNotFound(
                    f"FinalSpecRevision not found: {final_spec_revision_id}"
                )
            if revision["current_revision_id"] != final_spec_revision_id:
                raise StateConflict(
                    "DevFlow export requires the current FinalSpecRevision"
                )
            document = connection.execute(
                "SELECT current_snapshot_id FROM planning_documents WHERE planning_document_id = ?",
                (revision["planning_document_id"],),
            ).fetchone()
            snapshots = connection.execute(
                """
                SELECT planning_document_snapshot_id FROM final_spec_revision_snapshots
                WHERE final_spec_revision_id = ? ORDER BY planning_document_snapshot_id
                """,
                (final_spec_revision_id,),
            ).fetchall()
            snapshot_ids = [row["planning_document_snapshot_id"] for row in snapshots]
            if document["current_snapshot_id"] not in snapshot_ids:
                raise StateConflict(
                    "FinalSpecRevision is stale against the current PlanningDocumentSnapshot"
                )
            decision_rows = connection.execute(
                """
                SELECT d.* FROM decisions d JOIN final_spec_revision_decisions fsrd
                ON fsrd.decision_id = d.decision_id
                WHERE fsrd.final_spec_revision_id = ? ORDER BY d.created_at, d.decision_id
                """,
                (final_spec_revision_id,),
            ).fetchall()
            blocker_rows = connection.execute(
                """
                SELECT b.* FROM blockers b JOIN findings f ON f.finding_id = b.finding_id
                JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                WHERE rc.planning_document_id = ? AND b.status = 'ACTIVE'
                ORDER BY b.created_at, b.blocker_id
                """,
                (revision["planning_document_id"],),
            ).fetchall()
            decisions = [
                self._decision_payload(connection, row) for row in decision_rows
            ]
            blockers = [self._blocker_payload(connection, row) for row in blocker_rows]
            traceability = self._traceability_payload(
                connection, revision["planning_document_id"], final_spec_revision_id
            )
        finally:
            connection.close()

        target = self.exports_dir / "devflow" / final_spec_revision_id
        target.mkdir(parents=True, exist_ok=True)
        final_spec_bytes = self.content_store.read_relative(revision["content_ref"])
        if self._sha256(final_spec_bytes) != revision["content_hash"]:
            raise StateConflict("FinalSpec content hash mismatch")
        (target / "final-spec.md").write_bytes(final_spec_bytes)
        self._write_json(target / "decisions.json", {"decisions": decisions})
        self._write_json(target / "blockers.json", {"blockers": blockers})
        self._write_json(target / "traceability.json", traceability)
        self._write_json(
            target / "implementation-receipt.schema.json", self._receipt_schema()
        )
        manifest = {
            "contract_version": CONTRACT_VERSION,
            "export_id": final_spec_revision_id,
            "planning_document_id": revision["planning_document_id"],
            "final_spec_revision_id": final_spec_revision_id,
            "final_spec_content_hash": revision["content_hash"],
            "planning_snapshot_ids": snapshot_ids,
            "decision_ids": [row["decision_id"] for row in decision_rows],
            "active_blocker_ids": [row["blocker_id"] for row in blocker_rows],
            "generated_at": utc_now(),
            "source_workspace": str(self.workspace_root),
        }
        self._write_json(target / "manifest.json", manifest)
        return target

    def import_receipt(self, path: Path) -> str:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(f"invalid implementation receipt: {path}") from exc
        required = {
            "contract_version",
            "final_spec_revision_id",
            "repository",
            "commit_sha",
            "path_refs",
            "decision_ids",
        }
        missing = required - payload.keys()
        if missing:
            raise ValidationError(
                f"receipt missing fields: {', '.join(sorted(missing))}"
            )
        if payload["contract_version"] != CONTRACT_VERSION:
            raise ValidationError("unsupported implementation receipt contract version")
        connection = self.database.connect()
        try:
            revision = connection.execute(
                "SELECT 1 FROM final_spec_revisions WHERE final_spec_revision_id = ?",
                (payload["final_spec_revision_id"],),
            ).fetchone()
            if revision is None:
                raise ResourceNotFound("FinalSpecRevision not found")
            allowed_decisions = {
                row["decision_id"]
                for row in connection.execute(
                    "SELECT decision_id FROM final_spec_revision_decisions WHERE final_spec_revision_id = ?",
                    (payload["final_spec_revision_id"],),
                ).fetchall()
            }
        finally:
            connection.close()
        requested_decisions = set(payload["decision_ids"])
        if not requested_decisions.issubset(allowed_decisions):
            raise ValidationError(
                "receipt decision_ids must be included in the FinalSpecRevision"
            )
        repository = self._resolve_repository(str(payload["repository"]))
        repo_service = RepositoryService(self.database, self.workspace_root)
        commit_sha = repo_service.verify_commit(
            repository.repository_id, str(payload["commit_sha"])
        )
        normalized_paths: list[dict[str, str | None]] = []
        for item in payload["path_refs"]:
            if isinstance(item, str):
                path_value = item
                symbol = None
            else:
                path_value = str(item.get("path") or "")
                symbol = item.get("symbol")
            if not path_value or not repo_service.path_exists_at_commit(
                repository.repository_id, commit_sha, path_value
            ):
                raise ValidationError(f"path does not exist at commit: {path_value}")
            normalized_paths.append({"path": path_value, "symbol": symbol})
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT implementation_ref_id FROM implementation_refs
                WHERE final_spec_revision_id = ? AND repository_id = ? AND commit_sha = ?
                  AND work_ref IS ?
                """,
                (
                    payload["final_spec_revision_id"],
                    repository.repository_id,
                    commit_sha,
                    payload.get("work_ref"),
                ),
            ).fetchone()
            if existing:
                return existing["implementation_ref_id"]
            implementation_ref_id = new_id()
            connection.execute(
                """
                INSERT INTO implementation_refs(
                    implementation_ref_id, final_spec_revision_id, repository_id,
                    commit_sha, work_ref, decision_ids_json, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    implementation_ref_id,
                    payload["final_spec_revision_id"],
                    repository.repository_id,
                    commit_sha,
                    payload.get("work_ref"),
                    json.dumps(payload["decision_ids"], separators=(",", ":")),
                    payload.get("verified_at") or utc_now(),
                ),
            )
            for item in normalized_paths:
                connection.execute(
                    "INSERT INTO implementation_paths(implementation_ref_id, path, symbol) VALUES (?, ?, ?)",
                    (implementation_ref_id, item["path"], item["symbol"]),
                )
            return implementation_ref_id

    def _resolve_repository(self, value: str):
        service = RepositoryService(self.database, self.workspace_root)
        for repository in service.list():
            if value in {
                repository.repository_id,
                repository.name,
                repository.remote_identity,
            }:
                return repository
        raise ResourceNotFound(f"repository not registered: {value}")

    def _decision_payload(self, connection, row) -> dict[str, Any]:
        evidence = connection.execute(
            """
            SELECT er.* FROM evidence_refs er JOIN decision_evidence de ON de.evidence_ref_id = er.evidence_ref_id
            WHERE de.decision_id = ? ORDER BY er.evidence_ref_id
            """,
            (row["decision_id"],),
        ).fetchall()
        return {
            "decision_id": row["decision_id"],
            "owner": row["owner"],
            "adopted_option": row["adopted_option"],
            "rationale": row["rationale"],
            "evidence_refs": [
                {
                    "evidence_ref_id": item["evidence_ref_id"],
                    "type": item["evidence_type"],
                    "payload": json.loads(item["payload_json"]),
                }
                for item in evidence
            ],
            "supersedes_decision_id": row["supersedes_decision_id"],
        }

    @staticmethod
    def _blocker_payload(connection, row) -> dict[str, Any]:
        scopes = connection.execute(
            "SELECT * FROM blocked_scopes WHERE blocker_id = ? ORDER BY blocked_scope_id",
            (row["blocker_id"],),
        ).fetchall()
        return {
            "blocker_id": row["blocker_id"],
            "reason": row["reason"],
            "resume_condition": row["resume_condition"],
            "available_work": json.loads(row["available_work_json"] or "[]"),
            "blocked_scopes": [dict(scope) for scope in scopes],
        }

    @staticmethod
    def _traceability_payload(
        connection, planning_document_id: str, revision_id: str
    ) -> dict[str, Any]:
        cycles = connection.execute(
            "SELECT review_cycle_id, target_planning_snapshot_id, baseline_planning_snapshot_id FROM review_cycles WHERE planning_document_id = ?",
            (planning_document_id,),
        ).fetchall()
        findings = connection.execute(
            """
            SELECT f.* FROM findings f JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
            WHERE rc.planning_document_id = ?
            """,
            (planning_document_id,),
        ).fetchall()
        decisions = connection.execute(
            "SELECT decision_id FROM final_spec_revision_decisions WHERE final_spec_revision_id = ?",
            (revision_id,),
        ).fetchall()
        return {
            "planning_document_id": planning_document_id,
            "final_spec_revision_id": revision_id,
            "review_cycles": [dict(row) for row in cycles],
            "findings": [dict(row) for row in findings],
            "decision_ids": [row["decision_id"] for row in decisions],
        }

    @staticmethod
    def _receipt_schema() -> dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": [
                "contract_version",
                "final_spec_revision_id",
                "repository",
                "commit_sha",
                "path_refs",
                "decision_ids",
            ],
            "properties": {
                "contract_version": {"const": CONTRACT_VERSION},
                "final_spec_revision_id": {"type": "string"},
                "repository": {"type": "string"},
                "commit_sha": {"type": "string"},
                "path_refs": {"type": "array"},
                "work_ref": {"type": ["string", "null"]},
                "decision_ids": {"type": "array", "items": {"type": "string"}},
                "verified_at": {"type": "string"},
            },
        }

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _sha256(payload: bytes) -> str:
        import hashlib

        return hashlib.sha256(payload).hexdigest()
