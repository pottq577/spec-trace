from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from .collector import SourceCollector
from .config import NotionSourceSettings, SettingsService
from .errors import (
    ExternalServiceError,
    ResourceNotFound,
    SpecTraceError,
    ValidationError,
)
from .notion import NotionPort
from .planning_documents import PlanningDocumentService
from .projection import ProjectionService
from .review_documents import ReviewDocumentService
from .util import utc_now
from .workspace import Workspace

logger = logging.getLogger(__name__)


class StatusService:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.database = workspace.database

    def document(self, planning_document_id: str) -> dict[str, Any]:
        connection = self.database.connect()
        try:
            document = connection.execute(
                "SELECT * FROM planning_documents WHERE planning_document_id = ?",
                (planning_document_id,),
            ).fetchone()
            if document is None:
                raise ResourceNotFound(
                    f"planning document not found: {planning_document_id}"
                )

            change_sets = connection.execute(
                """
                SELECT change_set_id, baseline_snapshot_id, target_snapshot_id,
                       analysis_status, created_at
                FROM change_sets
                WHERE planning_document_id = ? AND analysis_status != 'COMPLETED'
                ORDER BY created_at DESC, change_set_id DESC
                """,
                (planning_document_id,),
            ).fetchall()
            cycle = connection.execute(
                """
                SELECT * FROM review_cycles
                WHERE planning_document_id = ? AND status != 'SUPERSEDED'
                ORDER BY created_at DESC, review_cycle_id DESC LIMIT 1
                """,
                (planning_document_id,),
            ).fetchone()
            findings = connection.execute(
                """
                SELECT f.* FROM findings f
                JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                WHERE rc.planning_document_id = ?
                  AND f.status NOT IN ('RESOLVED', 'SUPERSEDED')
                ORDER BY f.created_at, f.finding_id
                """,
                (planning_document_id,),
            ).fetchall()
            questions = connection.execute(
                """
                SELECT oq.*, (
                    SELECT pa.planner_answer_id FROM planner_answers pa
                    WHERE pa.open_question_id = oq.open_question_id
                    ORDER BY pa.answered_at DESC, pa.planner_answer_id DESC LIMIT 1
                ) AS latest_answer_id
                FROM open_questions oq
                JOIN findings f ON f.finding_id = oq.finding_id
                JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                WHERE rc.planning_document_id = ?
                  AND oq.status NOT IN ('RESOLVED', 'SUPERSEDED')
                ORDER BY oq.created_at, oq.open_question_id
                """,
                (planning_document_id,),
            ).fetchall()
            blockers = connection.execute(
                """
                SELECT b.* FROM blockers b
                JOIN findings f ON f.finding_id = b.finding_id
                JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                WHERE rc.planning_document_id = ? AND b.status = 'ACTIVE'
                ORDER BY b.created_at, b.blocker_id
                """,
                (planning_document_id,),
            ).fetchall()
            final_spec = connection.execute(
                """
                SELECT fs.current_revision_id AS final_spec_revision_id,
                       fsr.content_hash, fsr.created_at
                FROM final_specs fs
                LEFT JOIN final_spec_revisions fsr
                  ON fsr.final_spec_revision_id = fs.current_revision_id
                WHERE fs.planning_document_id = ?
                """,
                (planning_document_id,),
            ).fetchone()
            pending = connection.execute(
                """
                SELECT operation_id, operation_type, subject_ref, dedupe_key,
                       status, attempt, available_at, failure_code, created_at
                FROM pending_operations
                WHERE (subject_ref = ? OR dedupe_key = ?)
                  AND status != 'COMPLETED'
                ORDER BY created_at, operation_id
                """,
                (planning_document_id, f"projection:{planning_document_id}"),
            ).fetchall()
            collection_runs = connection.execute(
                """
                SELECT collection_run_id, status, attempt, failure_code,
                       retryable, started_at, completed_at
                FROM collection_runs
                WHERE planning_document_id = ? AND retryable = 1
                  AND status IN ('SOURCE_UNSTABLE', 'COLLECTION_FAILED')
                ORDER BY started_at DESC LIMIT 5
                """,
                (planning_document_id,),
            ).fetchall()

            blocker_payloads: list[dict[str, Any]] = []
            for blocker in blockers:
                scopes = connection.execute(
                    """
                    SELECT blocked_scope_id, scope_type, target_ref,
                           description, resume_work
                    FROM blocked_scopes WHERE blocker_id = ?
                    ORDER BY blocked_scope_id
                    """,
                    (blocker["blocker_id"],),
                ).fetchall()
                payload = dict(blocker)
                payload["available_work"] = json.loads(
                    payload.pop("available_work_json", "[]") or "[]"
                )
                payload["blocked_scopes"] = [dict(row) for row in scopes]
                blocker_payloads.append(payload)
        finally:
            connection.close()

        return {
            "document": {
                "planning_document_id": document["planning_document_id"],
                "title": document["title"],
                "source_status": document["source_status"],
                "current_snapshot_id": document["current_snapshot_id"],
                "last_collected_at": document["last_collected_at"],
                "attention_required": bool(document["attention_required"]),
            },
            "change_sets": [dict(row) for row in change_sets],
            "review_cycle": dict(cycle) if cycle else None,
            "findings": [dict(row) for row in findings],
            "open_questions": [self._question_payload(row) for row in questions],
            "blockers": blocker_payloads,
            "final_spec_revision": dict(final_spec) if final_spec else None,
            "pending_operations": [dict(row) for row in pending],
            "retryable_collection_runs": [dict(row) for row in collection_runs],
        }

    @staticmethod
    def _question_payload(row) -> dict[str, Any]:
        payload = dict(row)
        payload["options"] = json.loads(payload.pop("options_json"))
        payload["tradeoffs"] = json.loads(payload.pop("tradeoffs_json"))
        return payload


class RuntimeService:
    RETRY_DELAYS = (5.0, 15.0, 30.0)

    def __init__(
        self,
        workspace: Workspace,
        notion: NotionPort,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.workspace = workspace
        self.database = workspace.database
        self.notion = notion
        self.sleeper = sleeper
        self.progress_callback = progress_callback
        self._progress_state: dict[str, Any] = {}

    def _emit_progress(self, **progress: Any) -> None:
        if self.progress_callback is None:
            return
        self._progress_state.update(progress)
        self.progress_callback(dict(self._progress_state))

    @staticmethod
    def recover_interrupted_collections(
        workspace: Workspace,
    ) -> list[dict[str, Any]]:
        now = utc_now()
        with workspace.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT collection_run_id, planning_document_id, started_at
                FROM collection_runs
                WHERE completed_at IS NULL
                ORDER BY started_at, collection_run_id
                """
            ).fetchall()
            if rows:
                connection.execute(
                    """
                    UPDATE collection_runs
                    SET status = 'COLLECTION_FAILED',
                        failure_code = 'INTERRUPTED',
                        retryable = 1,
                        completed_at = ?
                    WHERE completed_at IS NULL
                    """,
                    (now,),
                )

        for row in rows:
            logger.warning(
                "recovered interrupted collection run=%s document=%s started_at=%s",
                row["collection_run_id"],
                row["planning_document_id"],
                row["started_at"],
            )
        return [dict(row) for row in rows]

    def collect_document(
        self, planning_document_id: str, *, title: str | None = None
    ) -> dict[str, Any]:
        collector = SourceCollector(
            self.database, self.workspace.content_store, self.notion
        )
        title = title or self._document_title(planning_document_id)
        started = time.monotonic()
        attempts = 1
        logger.info(
            "collection start document=%s title=%r attempt=%d",
            planning_document_id,
            title,
            attempts,
        )
        result = collector.collect(planning_document_id)
        for delay in self.RETRY_DELAYS:
            if result.status != "SOURCE_UNSTABLE":
                break
            logger.warning(
                "collection retry document=%s title=%r attempt=%d delay=%.1fs",
                planning_document_id,
                title,
                attempts + 1,
                delay,
            )
            self.sleeper(delay)
            attempts += 1
            result = collector.collect(planning_document_id)
        duration_seconds = time.monotonic() - started
        logger.info(
            "collection complete document=%s title=%r status=%s attempts=%d duration=%.2fs",
            planning_document_id,
            title,
            result.status,
            attempts,
            duration_seconds,
        )
        return {
            **result.__dict__,
            "attempts": attempts,
            "title": title,
            "duration_seconds": round(duration_seconds, 3),
        }

    def _document_title(self, planning_document_id: str) -> str:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT title FROM planning_documents WHERE planning_document_id = ?",
                (planning_document_id,),
            ).fetchone()
        finally:
            connection.close()
        return str(row["title"]) if row else planning_document_id

    def collect_all(self, *, incremental: bool = False) -> list[dict[str, Any]]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT planning_document_id, root_notion_page_id, title,
                       current_snapshot_id, source_last_edited_time
                FROM planning_documents
                WHERE source_status = 'AVAILABLE'
                ORDER BY created_at, planning_document_id
                """
            ).fetchall()
        finally:
            connection.close()

        total = len(rows)
        batch_started = time.monotonic()
        skipped = 0
        collected = 0
        failed = 0
        logger.info(
            "collection batch start total=%d mode=%s",
            total,
            "incremental" if incremental else "full",
        )
        self._emit_progress(
            phase="collect_all",
            phase_label="Notion 문서 확인/수집",
            state="RUNNING",
            current=0,
            processed=0,
            total=total,
            title=None,
            item_status=None,
            item_started_at=None,
            duration_seconds=None,
            skipped=0,
            collected=0,
            failed=0,
            elapsed_seconds=0.0,
        )
        results: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            document_id = row["planning_document_id"]
            title = str(row["title"])
            item_started = time.monotonic()
            item_started_at = utc_now()
            logger.info(
                "collection batch progress current=%d total=%d document=%s title=%r",
                index,
                total,
                document_id,
                title,
            )
            self._emit_progress(
                current=index,
                processed=index - 1,
                title=title,
                item_status="CHECKING",
                item_started_at=item_started_at,
                duration_seconds=None,
                elapsed_seconds=round(time.monotonic() - batch_started, 3),
            )
            if incremental and self._snapshot_matches_source(row):
                duration_seconds = round(time.monotonic() - item_started, 3)
                skipped += 1
                logger.info(
                    "collection skipped unchanged document=%s title=%r snapshot=%s duration=%.2fs",
                    document_id,
                    title,
                    row["current_snapshot_id"],
                    duration_seconds,
                )
                item = {
                    "status": "SKIPPED_UNCHANGED",
                    "planning_document_id": document_id,
                    "snapshot_id": row["current_snapshot_id"],
                    "change_set_id": None,
                    "failure_code": None,
                    "failure_detail": None,
                    "attempts": 0,
                    "title": title,
                    "duration_seconds": duration_seconds,
                }
                results.append(item)
                self._emit_progress(
                    processed=index,
                    item_status=item["status"],
                    duration_seconds=duration_seconds,
                    skipped=skipped,
                    collected=collected,
                    failed=failed,
                    elapsed_seconds=round(time.monotonic() - batch_started, 3),
                )
                continue
            self._emit_progress(item_status="COLLECTING")
            item = self.collect_document(document_id, title=title)
            results.append(item)
            if item["status"] in {
                "SOURCE_UNAVAILABLE",
                "SOURCE_UNSTABLE",
                "COLLECTION_FAILED",
            }:
                failed += 1
            else:
                collected += 1
            self._emit_progress(
                processed=index,
                item_status=item["status"],
                duration_seconds=item["duration_seconds"],
                skipped=skipped,
                collected=collected,
                failed=failed,
                elapsed_seconds=round(time.monotonic() - batch_started, 3),
            )
        logger.info("collection batch complete total=%d", total)
        self._emit_progress(
            state="COMPLETED",
            current=total,
            processed=total,
            title=None,
            item_status=None,
            item_started_at=None,
            duration_seconds=None,
            skipped=skipped,
            collected=collected,
            failed=failed,
            elapsed_seconds=round(time.monotonic() - batch_started, 3),
        )
        return results

    def _snapshot_matches_source(self, document) -> bool:
        snapshot_id = document["current_snapshot_id"]
        current_root_edited = str(document["source_last_edited_time"] or "")
        if not snapshot_id or not current_root_edited:
            return False

        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT psp.notion_page_id, psp.role, sp.source_page_id,
                       sp.last_collected_notion_edited_time,
                       sps.raw_content_ref
                FROM planning_snapshot_pages psp
                JOIN source_pages sp ON sp.source_page_id = psp.source_page_id
                JOIN source_page_snapshots sps
                  ON sps.source_page_snapshot_id = psp.source_page_snapshot_id
                WHERE psp.planning_document_snapshot_id = ?
                ORDER BY psp.role DESC, psp.notion_page_id
                """,
                (snapshot_id,),
            ).fetchall()
        finally:
            connection.close()
        if not rows:
            return False

        root_seen = False
        backfill: list[tuple[str, str]] = []
        for row in rows:
            baseline = str(row["last_collected_notion_edited_time"] or "")
            if not baseline:
                baseline = self._snapshot_edited_time(row["raw_content_ref"])
                if not baseline:
                    return False
                backfill.append((baseline, row["source_page_id"]))

            if row["role"] == "ROOT":
                root_seen = True
                if row["notion_page_id"] != document["root_notion_page_id"]:
                    return False
                if baseline != current_root_edited:
                    return False
                continue

            try:
                current = self.notion.retrieve_page(row["notion_page_id"])
            except (ResourceNotFound, ExternalServiceError):
                return False
            if current.get("archived") or current.get("in_trash"):
                return False
            if str(current.get("last_edited_time") or "") != baseline:
                return False

        if not root_seen:
            return False
        if backfill:
            with self.database.transaction() as connection:
                connection.executemany(
                    """
                    UPDATE source_pages
                    SET last_collected_notion_edited_time = ?
                    WHERE source_page_id = ?
                      AND last_collected_notion_edited_time IS NULL
                    """,
                    backfill,
                )
        return True

    def _snapshot_edited_time(self, raw_content_ref: str | None) -> str:
        if not raw_content_ref:
            return ""
        try:
            payload = self.workspace.content_store.read_json(raw_content_ref)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return ""
        if not isinstance(payload, dict):
            return ""
        page = payload.get("page") or {}
        if not isinstance(page, dict):
            return ""
        return str(page.get("last_edited_time") or "")

    def reconcile_pending(self) -> list[dict[str, Any]]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM pending_operations
                WHERE operation_type = 'PROJECT_DOCUMENT'
                  AND status IN ('PENDING', 'FAILED')
                ORDER BY created_at, operation_id
                """
            ).fetchall()
        finally:
            connection.close()

        results: list[dict[str, Any]] = []
        for row in rows:
            with self.database.transaction() as connection:
                current = connection.execute(
                    "SELECT status FROM pending_operations WHERE operation_id = ?",
                    (row["operation_id"],),
                ).fetchone()
                if current is None or current["status"] not in {"PENDING", "FAILED"}:
                    continue
                connection.execute(
                    """
                    UPDATE pending_operations
                    SET status = 'RUNNING', failure_code = NULL
                    WHERE operation_id = ?
                    """,
                    (row["operation_id"],),
                )
            try:
                projection = ProjectionService(self.database, self.notion).sync(
                    row["subject_ref"]
                )
                results.append(
                    {
                        "operation_id": row["operation_id"],
                        "status": "COMPLETED",
                        "result": projection,
                    }
                )
            except SpecTraceError as exc:
                results.append(
                    {
                        "operation_id": row["operation_id"],
                        "status": "FAILED",
                        "error": str(exc),
                    }
                )
        return results

    def collect_answers(self) -> list[dict[str, Any]]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT planning_document_id FROM planning_documents
                WHERE source_status = 'AVAILABLE'
                ORDER BY created_at, planning_document_id
                """
            ).fetchall()
        finally:
            connection.close()

        projection = ProjectionService(self.database, self.notion)
        results: list[dict[str, Any]] = []
        for row in rows:
            count = projection.collect_answers(row["planning_document_id"])
            results.append(
                {
                    "planning_document_id": row["planning_document_id"],
                    "answers_collected": count,
                }
            )
        return results

    def sync_source(self) -> dict[str, Any]:
        settings_service = SettingsService(self.workspace)
        source = settings_service.load().notion_source
        if source is None:
            source = self._infer_source_settings()
            if source is None:
                return {"status": "SKIPPED", "reason": "SOURCE_NOT_CONFIGURED"}
            settings_service.set_notion_source(
                source.database_id,
                source.data_source_id,
                parent_property=source.parent_property,
            )
            logger.info(
                "inferred Notion source database=%s data_source=%s",
                source.database_id,
                source.data_source_id,
            )
        result = PlanningDocumentService(self.database, self.notion).sync_data_source(
            source.database_id,
            source.data_source_id,
            parent_property=source.parent_property,
        )
        return {"status": "COMPLETED", **result}

    def _infer_source_settings(self) -> NotionSourceSettings | None:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT DISTINCT notion_database_id, notion_data_source_id
                FROM planning_documents
                WHERE notion_data_source_id IS NOT NULL
                ORDER BY notion_database_id, notion_data_source_id
                """
            ).fetchall()
        finally:
            connection.close()
        if len(rows) != 1:
            return None
        row = rows[0]
        return NotionSourceSettings(
            database_id=row["notion_database_id"],
            data_source_id=row["notion_data_source_id"],
        )

    def run_cycle(self) -> dict[str, Any]:
        self._emit_progress(
            phase="source_sync",
            phase_label="Notion 메뉴 동기화",
            state="RUNNING",
            title=None,
            item_status=None,
            item_started_at=None,
            duration_seconds=None,
        )
        logger.info("cycle phase=source_sync start")
        source_sync = self.sync_source()
        logger.info(
            "cycle phase=source_sync complete status=%s active_pages=%s",
            source_sync.get("status"),
            source_sync.get("active_pages", 0),
        )

        self._emit_progress(
            phase="recovery",
            phase_label="실패한 projection 복구",
            state="RUNNING",
            title=None,
            item_status=None,
            item_started_at=None,
            duration_seconds=None,
        )
        logger.info("cycle phase=recovery start")
        recovery = self.reconcile_pending()
        logger.info("cycle phase=recovery complete operations=%d", len(recovery))

        incremental = source_sync.get("status") == "COMPLETED"
        logger.info(
            "cycle phase=collect_all start incremental=%s",
            incremental,
        )
        collections = self.collect_all(incremental=incremental)
        logger.info(
            "cycle phase=collect_all complete collections=%d",
            len(collections),
        )

        self._emit_progress(
            phase="collect_answers",
            phase_label="구조화 답변 확인",
            state="RUNNING",
            title=None,
            item_status=None,
            item_started_at=None,
            duration_seconds=None,
        )
        logger.info("cycle phase=collect_answers start")
        answers = self.collect_answers()
        logger.info(
            "cycle phase=collect_answers complete documents=%d answers=%d",
            len(answers),
            sum(item.get("answers_collected", 0) for item in answers),
        )

        self._emit_progress(
            phase="review_responses",
            phase_label="검토 문서 답변 확인",
            state="RUNNING",
            title=None,
            item_status=None,
            item_started_at=None,
            duration_seconds=None,
        )
        logger.info("cycle phase=review_responses start")
        review_responses = ReviewDocumentService(
            self.workspace, self.notion
        ).collect_responses()
        logger.info(
            "cycle phase=review_responses complete responses=%d",
            len(review_responses),
        )
        logger.info("cycle complete")
        self._emit_progress(
            phase="completed",
            phase_label="전체 최신화 완료",
            state="COMPLETED",
            title=None,
            item_status=None,
            item_started_at=None,
            duration_seconds=None,
        )

        return {
            "source_sync": source_sync,
            "recovery": recovery,
            "collections": collections,
            "answers": answers,
            "review_responses": review_responses,
        }

    @staticmethod
    def validate_interval(interval: float) -> float:
        if interval <= 0:
            raise ValidationError("watch interval must be greater than zero")
        return interval
