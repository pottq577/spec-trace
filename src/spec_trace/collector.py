from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .canonical import canonical_page, extract_page_title, page_content_hash
from .content_store import ContentStore
from .db import Database
from .errors import ExternalServiceError, ResourceNotFound, StateConflict
from .notion import NotionPort, normalize_notion_id
from .planning_documents import PlanningDocumentService
from .references import extract_block_references
from .util import canonical_json_bytes, new_id, sha256_bytes, utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapturedPage:
    notion_page_id: str
    parent_notion_page_id: str | None
    role: str
    title: str
    last_edited_time: str
    raw_page: dict[str, Any]
    blocks: list[dict[str, Any]]


@dataclass(frozen=True)
class CaptureTree:
    pages: dict[str, CapturedPage]

    def stability_signature(self) -> tuple[tuple[str, str | None, str, str], ...]:
        return tuple(
            sorted(
                (
                    page.notion_page_id,
                    page.parent_notion_page_id,
                    page.role,
                    page.last_edited_time,
                )
                for page in self.pages.values()
            )
        )


@dataclass(frozen=True)
class CollectionResult:
    status: str
    planning_document_id: str
    snapshot_id: str | None
    change_set_id: str | None = None
    failure_code: str | None = None
    failure_detail: str | None = None


class SourceCollector:
    def __init__(
        self,
        database: Database,
        content_store: ContentStore,
        notion: NotionPort,
    ):
        self.database = database
        self.content_store = content_store
        self.notion = notion

    def collect(
        self, planning_document_id: str, trigger_type: str = "MANUAL"
    ) -> CollectionResult:
        document = PlanningDocumentService(self.database, self.notion).get(
            planning_document_id
        )
        run_id = self._start_run(
            planning_document_id, document.current_snapshot_id, trigger_type
        )
        try:
            excluded = self._system_page_ids(planning_document_id)
            first = self._capture_tree(document.root_notion_page_id, excluded)
            second = self._capture_tree(document.root_notion_page_id, excluded)
            if first.stability_signature() != second.stability_signature():
                self._finish_run(
                    run_id,
                    "SOURCE_UNSTABLE",
                    failure_code="SOURCE_UNSTABLE",
                    retryable=True,
                )
                return CollectionResult(
                    "SOURCE_UNSTABLE",
                    planning_document_id,
                    document.current_snapshot_id,
                    failure_code="SOURCE_UNSTABLE",
                )
            result = self._publish(planning_document_id, first, run_id)
            return result
        except ResourceNotFound:
            self._mark_unavailable(planning_document_id)
            self._finish_run(
                run_id,
                "SOURCE_UNAVAILABLE",
                failure_code="SOURCE_UNAVAILABLE",
                retryable=False,
            )
            return CollectionResult(
                "SOURCE_UNAVAILABLE",
                planning_document_id,
                document.current_snapshot_id,
                failure_code="SOURCE_UNAVAILABLE",
            )
        except ExternalServiceError as exc:
            logger.warning(
                "collection external service failure document=%s error=%s",
                planning_document_id,
                exc,
            )
            self._finish_run(
                run_id,
                "COLLECTION_FAILED",
                failure_code="EXTERNAL_SERVICE",
                retryable=True,
            )
            return CollectionResult(
                "COLLECTION_FAILED",
                planning_document_id,
                document.current_snapshot_id,
                failure_code="EXTERNAL_SERVICE",
                failure_detail=str(exc),
            )
        except Exception:
            self._finish_run(
                run_id, "COLLECTION_FAILED", failure_code="INTERNAL", retryable=False
            )
            raise

    def _capture_tree(self, root_page_id: str, excluded: set[str]) -> CaptureTree:
        pages: dict[str, CapturedPage] = {}
        self._capture_page(
            normalize_notion_id(root_page_id), None, "ROOT", excluded, pages
        )
        return CaptureTree(pages)

    def _capture_page(
        self,
        page_id: str,
        parent_page_id: str | None,
        role: str,
        excluded: set[str],
        pages: dict[str, CapturedPage],
    ) -> None:
        if page_id in excluded:
            return
        page = self.notion.retrieve_page(page_id)
        if page.get("archived") or page.get("in_trash"):
            if role == "ROOT":
                raise ResourceNotFound(f"Notion root page unavailable: {page_id}")
            return
        blocks = self._capture_blocks(page_id, page_id, excluded, pages)
        captured = CapturedPage(
            notion_page_id=page_id,
            parent_notion_page_id=parent_page_id,
            role=role,
            title=extract_page_title(page),
            last_edited_time=str(page.get("last_edited_time") or ""),
            raw_page=page,
            blocks=blocks,
        )
        pages[page_id] = captured

    def _capture_blocks(
        self,
        block_id: str,
        owning_page_id: str,
        excluded: set[str],
        pages: dict[str, CapturedPage],
    ) -> list[dict[str, Any]]:
        captured: list[dict[str, Any]] = []
        for source_block in self.notion.list_block_children(block_id):
            block = dict(source_block)
            block_type = block.get("type")
            if block_type == "child_page":
                child_id = normalize_notion_id(str(block["id"]))
                if child_id in excluded:
                    continue
                captured.append(block)
                self._capture_page(
                    child_id, owning_page_id, "COMPOSED_CHILD", excluded, pages
                )
                continue
            if block.get("has_children"):
                block["_captured_children"] = self._capture_blocks(
                    normalize_notion_id(str(block["id"])),
                    owning_page_id,
                    excluded,
                    pages,
                )
            captured.append(block)
        return captured

    def _publish(
        self, planning_document_id: str, tree: CaptureTree, run_id: str
    ) -> CollectionResult:
        captured_at = utc_now()
        prepared: dict[str, dict[str, Any]] = {}
        aggregate_parts: list[dict[str, Any]] = []
        for page in tree.pages.values():
            canonical = canonical_page(page.title, page.blocks)
            content_hash = page_content_hash(canonical)
            _, content_ref = self.content_store.put_json(canonical)
            _, raw_ref = self.content_store.put_json(
                {"page": page.raw_page, "blocks": page.blocks}
            )
            prepared[page.notion_page_id] = {
                "page": page,
                "content_hash": content_hash,
                "content_ref": content_ref,
                "raw_ref": raw_ref,
                "references": extract_block_references(page.blocks),
            }
            aggregate_parts.append(
                {
                    "notion_page_id": page.notion_page_id,
                    "parent_notion_page_id": page.parent_notion_page_id,
                    "role": page.role,
                    "content_hash": content_hash,
                }
            )
        aggregate_parts.sort(key=lambda value: value["notion_page_id"])
        aggregate_hash = sha256_bytes(canonical_json_bytes(aggregate_parts))

        with self.database.transaction() as connection:
            document = connection.execute(
                "SELECT * FROM planning_documents WHERE planning_document_id = ?",
                (planning_document_id,),
            ).fetchone()
            if document is None:
                raise ResourceNotFound(
                    f"planning document not found: {planning_document_id}"
                )
            baseline_id = document["current_snapshot_id"]
            baseline = None
            if baseline_id:
                baseline = connection.execute(
                    "SELECT aggregate_hash FROM planning_document_snapshots WHERE planning_document_snapshot_id = ?",
                    (baseline_id,),
                ).fetchone()
            if baseline and baseline["aggregate_hash"] == aggregate_hash:
                connection.execute(
                    """
                    UPDATE planning_documents
                    SET source_status = 'AVAILABLE', last_collected_at = ?, attention_required = 0
                    WHERE planning_document_id = ?
                    """,
                    (captured_at, planning_document_id),
                )
                connection.execute(
                    """
                    UPDATE collection_runs
                    SET status = 'UNCHANGED', completed_at = ?
                    WHERE collection_run_id = ?
                    """,
                    (captured_at, run_id),
                )
                return CollectionResult("UNCHANGED", planning_document_id, baseline_id)

            source_rows: dict[str, tuple[str, str]] = {}
            for page_id in sorted(prepared):
                item = prepared[page_id]
                page: CapturedPage = item["page"]
                existing = connection.execute(
                    """
                    SELECT * FROM source_pages
                    WHERE planning_document_id = ? AND notion_page_id = ?
                    """,
                    (planning_document_id, page_id),
                ).fetchone()
                source_page_id = existing["source_page_id"] if existing else new_id()
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO source_pages(
                            source_page_id, planning_document_id, notion_page_id,
                            role, title, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source_page_id,
                            planning_document_id,
                            page_id,
                            page.role,
                            page.title,
                            captured_at,
                        ),
                    )
                previous_snapshot = connection.execute(
                    """
                    SELECT source_page_snapshot_id FROM source_page_snapshots
                    WHERE source_page_id = ? ORDER BY captured_at DESC LIMIT 1
                    """,
                    (source_page_id,),
                ).fetchone()
                snapshot = connection.execute(
                    """
                    SELECT source_page_snapshot_id FROM source_page_snapshots
                    WHERE source_page_id = ? AND content_hash = ?
                    """,
                    (source_page_id, item["content_hash"]),
                ).fetchone()
                source_snapshot_id = (
                    snapshot["source_page_snapshot_id"] if snapshot else new_id()
                )
                if snapshot is None:
                    connection.execute(
                        """
                        INSERT INTO source_page_snapshots(
                            source_page_snapshot_id, source_page_id, previous_snapshot_id,
                            content_hash, content_ref, raw_content_ref, captured_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source_snapshot_id,
                            source_page_id,
                            previous_snapshot["source_page_snapshot_id"]
                            if previous_snapshot
                            else None,
                            item["content_hash"],
                            item["content_ref"],
                            item["raw_ref"],
                            captured_at,
                        ),
                    )
                source_rows[page_id] = (source_page_id, source_snapshot_id)

            for page_id in sorted(prepared):
                page: CapturedPage = prepared[page_id]["page"]
                source_page_id, _ = source_rows[page_id]
                parent_source_page_id = source_rows.get(
                    page.parent_notion_page_id, (None, None)
                )[0]
                connection.execute(
                    """
                    UPDATE source_pages
                    SET current_parent_source_page_id = ?, role = ?, title = ?
                    WHERE source_page_id = ?
                    """,
                    (parent_source_page_id, page.role, page.title, source_page_id),
                )

            snapshot_id = new_id()
            connection.execute(
                """
                INSERT INTO planning_document_snapshots(
                    planning_document_snapshot_id, planning_document_id, previous_snapshot_id,
                    aggregate_hash, captured_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    planning_document_id,
                    baseline_id,
                    aggregate_hash,
                    captured_at,
                ),
            )
            for page_id in sorted(prepared):
                item = prepared[page_id]
                page: CapturedPage = item["page"]
                source_page_id, source_snapshot_id = source_rows[page_id]
                connection.execute(
                    """
                    INSERT INTO planning_snapshot_pages(
                        planning_document_snapshot_id, source_page_id, source_page_snapshot_id,
                        notion_page_id, parent_notion_page_id, role, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        source_page_id,
                        source_snapshot_id,
                        page_id,
                        page.parent_notion_page_id,
                        page.role,
                        item["content_hash"],
                    ),
                )
                for reference in item["references"]:
                    target_document = connection.execute(
                        "SELECT planning_document_id FROM planning_documents WHERE root_notion_page_id = ?",
                        (reference["target_notion_page_id"],),
                    ).fetchone()
                    connection.execute(
                        """
                        INSERT INTO source_references(
                            source_reference_id, planning_document_snapshot_id, source_page_snapshot_id,
                            target_notion_page_id, target_planning_document_id, reference_type, location_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_id(),
                            snapshot_id,
                            source_snapshot_id,
                            reference["target_notion_page_id"],
                            target_document["planning_document_id"]
                            if target_document
                            else None,
                            reference["reference_type"],
                            reference["location_json"],
                        ),
                    )
            connection.execute(
                """
                UPDATE planning_documents
                SET current_snapshot_id = ?, source_status = 'AVAILABLE',
                    last_collected_at = ?, attention_required = 0
                WHERE planning_document_id = ?
                """,
                (snapshot_id, captured_at, planning_document_id),
            )
            change_set_id = None
            if baseline_id:
                change_set_id = self._create_change_set(
                    connection,
                    planning_document_id,
                    baseline_id,
                    snapshot_id,
                    captured_at,
                )
            connection.execute(
                """
                UPDATE collection_runs
                SET status = 'SNAPSHOT_CREATED', created_snapshot_id = ?, completed_at = ?
                WHERE collection_run_id = ?
                """,
                (snapshot_id, captured_at, run_id),
            )
            return CollectionResult(
                "SNAPSHOT_CREATED", planning_document_id, snapshot_id, change_set_id
            )

    def _create_change_set(
        self,
        connection,
        planning_document_id: str,
        baseline_id: str,
        target_id: str,
        now: str,
    ) -> str:
        existing = connection.execute(
            """
            SELECT change_set_id FROM change_sets
            WHERE planning_document_id = ? AND baseline_snapshot_id = ? AND target_snapshot_id = ?
            """,
            (planning_document_id, baseline_id, target_id),
        ).fetchone()
        if existing:
            return existing["change_set_id"]
        change_set_id = new_id()
        connection.execute(
            """
            INSERT INTO change_sets(
                change_set_id, planning_document_id, baseline_snapshot_id,
                target_snapshot_id, analysis_status, created_at
            ) VALUES (?, ?, ?, ?, 'PENDING_SOURCE_DIFF', ?)
            """,
            (change_set_id, planning_document_id, baseline_id, target_id, now),
        )
        baseline = self._snapshot_page_map(connection, baseline_id)
        target = self._snapshot_page_map(connection, target_id)
        for page_id in sorted(set(baseline) | set(target)):
            before = baseline.get(page_id)
            after = target.get(page_id)
            changes: list[str] = []
            if before is None:
                changes.append("PAGE_ADDED")
            elif after is None:
                changes.append("PAGE_REMOVED")
            else:
                if before["content_hash"] != after["content_hash"]:
                    changes.append("CONTENT_CHANGED")
                if before["parent_notion_page_id"] != after["parent_notion_page_id"]:
                    changes.append("PARENT_CHANGED")
                if before["role"] != after["role"]:
                    changes.append("ROLE_CHANGED")
            for change_type in changes:
                connection.execute(
                    """
                    INSERT INTO physical_changes(
                        physical_change_id, change_set_id, notion_page_id, change_type,
                        baseline_source_page_snapshot_id, target_source_page_snapshot_id,
                        baseline_parent_notion_page_id, target_parent_notion_page_id,
                        baseline_content_hash, target_content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        change_set_id,
                        page_id,
                        change_type,
                        before["source_page_snapshot_id"] if before else None,
                        after["source_page_snapshot_id"] if after else None,
                        before["parent_notion_page_id"] if before else None,
                        after["parent_notion_page_id"] if after else None,
                        before["content_hash"] if before else None,
                        after["content_hash"] if after else None,
                    ),
                )
        return change_set_id

    @staticmethod
    def _snapshot_page_map(connection, snapshot_id: str) -> dict[str, Any]:
        rows = connection.execute(
            "SELECT * FROM planning_snapshot_pages WHERE planning_document_snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()
        return {row["notion_page_id"]: row for row in rows}

    def _start_run(
        self,
        planning_document_id: str,
        baseline_snapshot_id: str | None,
        trigger_type: str,
    ) -> str:
        run_id = new_id()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO collection_runs(
                        collection_run_id, planning_document_id, trigger_type,
                        status, baseline_snapshot_id, started_at
                    ) VALUES (?, ?, ?, 'RUNNING', ?, ?)
                    """,
                    (
                        run_id,
                        planning_document_id,
                        trigger_type,
                        baseline_snapshot_id,
                        utc_now(),
                    ),
                )
        except Exception as exc:
            if "one_active_collection_per_document" in str(
                exc
            ) or "UNIQUE constraint failed" in str(exc):
                raise StateConflict(
                    f"collection already active: {planning_document_id}"
                ) from exc
            raise
        return run_id

    def _finish_run(
        self, run_id: str, status: str, *, failure_code: str, retryable: bool
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE collection_runs
                SET status = ?, failure_code = ?, retryable = ?, completed_at = ?
                WHERE collection_run_id = ?
                """,
                (status, failure_code, int(retryable), utc_now(), run_id),
            )

    def _mark_unavailable(self, planning_document_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE planning_documents
                SET source_status = 'UNAVAILABLE', attention_required = 1, last_collected_at = ?
                WHERE planning_document_id = ?
                """,
                (utc_now(), planning_document_id),
            )

    def _system_page_ids(self, planning_document_id: str) -> set[str]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT review_page_id FROM notion_review_pages WHERE planning_document_id = ?",
                (planning_document_id,),
            ).fetchall()
        finally:
            connection.close()
        return {normalize_notion_id(row["review_page_id"]) for row in rows}
