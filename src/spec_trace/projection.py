from __future__ import annotations

import json
from typing import Any

from .db import Database
from .errors import ExternalServiceError, ResourceNotFound
from .notion import NotionPort, normalize_notion_id
from .pending import PendingOperationService
from .util import canonical_json_bytes, new_id, sha256_bytes, utc_now


class ProjectionService:
    def __init__(self, database: Database, notion: NotionPort):
        self.database = database
        self.notion = notion
        self.pending = PendingOperationService(database)

    def sync(self, planning_document_id: str) -> dict[str, Any]:
        dedupe_key = f"projection:{planning_document_id}"
        run_id = self._start_run(planning_document_id)
        answers = self.collect_answers(planning_document_id)
        try:
            review_page_id = self._ensure_review_page(planning_document_id)
            self._sync_status(planning_document_id, review_page_id)
            decisions = self._sync_decisions(planning_document_id, review_page_id)
            questions = self._sync_questions(planning_document_id, review_page_id)
            blockers = self._sync_blockers(planning_document_id, review_page_id)
            self._finish_run(run_id, "COMPLETED")
            self.pending.complete(dedupe_key)
            return {
                "review_page_id": review_page_id,
                "answers_collected": answers,
                "decisions_projected": decisions,
                "questions_projected": questions,
                "blockers_projected": blockers,
            }
        except Exception as exc:
            self._finish_run(run_id, "FAILED", type(exc).__name__)
            self.pending.schedule("PROJECT_DOCUMENT", planning_document_id, dedupe_key)
            self.pending.fail(dedupe_key, type(exc).__name__)
            raise

    def collect_answers(self, planning_document_id: str) -> int:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT oq.open_question_id, oq.finding_id, nqb.answer_slot_block_id, f.review_cycle_id
                FROM open_questions oq
                JOIN findings f ON f.finding_id = oq.finding_id
                JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                JOIN notion_question_blocks nqb ON nqb.open_question_id = oq.open_question_id
                WHERE rc.planning_document_id = ?
                  AND oq.status IN ('OPEN','ANSWERED','VERIFYING','REOPENED')
                """,
                (planning_document_id,),
            ).fetchall()
        finally:
            connection.close()
        collected = 0
        for row in rows:
            answer = self._read_answer(row["answer_slot_block_id"])
            if not answer:
                continue
            answer_hash = sha256_bytes(canonical_json_bytes({"answer": answer}))
            with self.database.transaction() as connection:
                existing = connection.execute(
                    """
                    SELECT planner_answer_id FROM planner_answers
                    WHERE open_question_id = ? AND answer_hash = ?
                    """,
                    (row["open_question_id"], answer_hash),
                ).fetchone()
                if existing:
                    continue
                previous = connection.execute(
                    """
                    SELECT planner_answer_id FROM planner_answers
                    WHERE open_question_id = ? ORDER BY answered_at DESC LIMIT 1
                    """,
                    (row["open_question_id"],),
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO planner_answers(
                        planner_answer_id, open_question_id, answer, answer_hash,
                        answered_at, supersedes_answer_id
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        row["open_question_id"],
                        answer,
                        answer_hash,
                        utc_now(),
                        previous["planner_answer_id"] if previous else None,
                    ),
                )
                connection.execute(
                    "UPDATE open_questions SET status = 'ANSWERED' WHERE open_question_id = ?",
                    (row["open_question_id"],),
                )
                connection.execute(
                    "UPDATE review_cycles SET status = 'REVERIFYING' WHERE review_cycle_id = ?",
                    (row["review_cycle_id"],),
                )
                collected += 1
        return collected

    def _ensure_review_page(self, planning_document_id: str) -> str:
        connection = self.database.connect()
        try:
            document = connection.execute(
                "SELECT root_notion_page_id FROM planning_documents WHERE planning_document_id = ?",
                (planning_document_id,),
            ).fetchone()
            mapping = connection.execute(
                "SELECT review_page_id FROM notion_review_pages WHERE planning_document_id = ?",
                (planning_document_id,),
            ).fetchone()
        finally:
            connection.close()
        if document is None:
            raise ResourceNotFound(
                f"PlanningDocument not found: {planning_document_id}"
            )
        if mapping:
            try:
                page = self.notion.retrieve_page(mapping["review_page_id"])
                if not page.get("archived") and not page.get("in_trash"):
                    return normalize_notion_id(mapping["review_page_id"])
            except ResourceNotFound:
                pass
        created = self.notion.create_child_page(
            document["root_notion_page_id"], "개발 검토"
        )
        review_page_id = normalize_notion_id(str(created["id"]))
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO notion_review_pages(planning_document_id, review_page_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(planning_document_id) DO UPDATE SET
                    review_page_id = excluded.review_page_id,
                    updated_at = excluded.updated_at
                """,
                (planning_document_id, review_page_id, utc_now()),
            )
        return review_page_id

    def _sync_status(self, planning_document_id: str, review_page_id: str) -> None:
        text = self._status_text(planning_document_id)
        digest = sha256_bytes(text.encode("utf-8"))
        connection = self.database.connect()
        try:
            mapping = connection.execute(
                "SELECT * FROM notion_status_blocks WHERE planning_document_id = ?",
                (planning_document_id,),
            ).fetchone()
        finally:
            connection.close()
        value = _rich_text_value(f"현재 상태: {text}")
        if mapping is None:
            created = self.notion.append_block_children(
                review_page_id, [_paragraph_block(f"현재 상태: {text}")]
            )
            if not created:
                raise ExternalServiceError("Notion did not return the status block")
            block_id = normalize_notion_id(str(created[0]["id"]))
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO notion_status_blocks(planning_document_id, status_block_id, content_hash, updated_at) VALUES (?, ?, ?, ?)",
                    (planning_document_id, block_id, digest, utc_now()),
                )
        elif mapping["content_hash"] != digest:
            self.notion.update_block(mapping["status_block_id"], "paragraph", value)
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE notion_status_blocks SET content_hash = ?, updated_at = ? WHERE planning_document_id = ?",
                    (digest, utc_now(), planning_document_id),
                )

    def _sync_decisions(self, planning_document_id: str, review_page_id: str) -> int:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT d.* FROM decisions d JOIN findings f ON f.finding_id = d.finding_id
                JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                LEFT JOIN notion_decision_blocks ndb ON ndb.decision_id = d.decision_id
                WHERE rc.planning_document_id = ? AND d.status = 'ADOPTED'
                  AND ndb.decision_id IS NULL
                ORDER BY d.created_at, d.decision_id
                """,
                (planning_document_id,),
            ).fetchall()
        finally:
            connection.close()
        count = 0
        for row in rows:
            text = f"개발 결정: {row['adopted_option']}\n이유: {row['rationale']}"
            created = self.notion.append_block_children(
                review_page_id, [_paragraph_block(text)]
            )
            block_id = normalize_notion_id(str(created[0]["id"]))
            digest = sha256_bytes(text.encode("utf-8"))
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO notion_decision_blocks(decision_id, block_id, content_hash, updated_at) VALUES (?, ?, ?, ?)",
                    (row["decision_id"], block_id, digest, utc_now()),
                )
            count += 1
        return count

    def _sync_questions(self, planning_document_id: str, review_page_id: str) -> int:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT oq.* FROM open_questions oq JOIN findings f ON f.finding_id = oq.finding_id
                JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                LEFT JOIN notion_question_blocks nqb ON nqb.open_question_id = oq.open_question_id
                WHERE rc.planning_document_id = ?
                  AND oq.status IN ('OPEN','ANSWERED','VERIFYING','REOPENED')
                  AND nqb.open_question_id IS NULL
                ORDER BY oq.created_at, oq.open_question_id
                """,
                (planning_document_id,),
            ).fetchall()
        finally:
            connection.close()
        count = 0
        for row in rows:
            options = json.loads(row["options_json"])
            tradeoffs = json.loads(row["tradeoffs_json"])
            details = [
                _heading_block(row["question"]),
                _paragraph_block(
                    "선택지: " + " / ".join(str(value) for value in options)
                ),
                _paragraph_block(
                    "트레이드오프: " + " / ".join(str(value) for value in tradeoffs)
                ),
                _paragraph_block("개발 권장: " + row["developer_recommendation"]),
                _toggle_block("답변"),
            ]
            created = self.notion.append_block_children(review_page_id, details)
            if len(created) != len(details):
                raise ExternalServiceError(
                    "Notion returned an incomplete question projection"
                )
            section_id = normalize_notion_id(str(created[0]["id"]))
            slot_id = normalize_notion_id(str(created[-1]["id"]))
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO notion_question_blocks(
                        open_question_id, question_section_block_id, answer_slot_block_id, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (row["open_question_id"], section_id, slot_id, utc_now()),
                )
            count += 1
        return count

    def _sync_blockers(self, planning_document_id: str, review_page_id: str) -> int:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT b.* FROM blockers b JOIN findings f ON f.finding_id = b.finding_id
                JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                WHERE rc.planning_document_id = ? AND b.status = 'ACTIVE'
                ORDER BY b.created_at, b.blocker_id
                """,
                (planning_document_id,),
            ).fetchall()
        finally:
            connection.close()
        count = 0
        for row in rows:
            connection = self.database.connect()
            try:
                scopes = connection.execute(
                    "SELECT * FROM blocked_scopes WHERE blocker_id = ? ORDER BY blocked_scope_id",
                    (row["blocker_id"],),
                ).fetchall()
                mapping = connection.execute(
                    "SELECT * FROM notion_blocker_blocks WHERE blocker_id = ?",
                    (row["blocker_id"],),
                ).fetchone()
            finally:
                connection.close()
            scope_text = ", ".join(
                scope["description"] or scope["target_ref"] for scope in scopes
            )
            available = ", ".join(json.loads(row["available_work_json"] or "[]"))
            text = (
                f"Blocker: {row['reason']}\n막힌 범위: {scope_text}\n"
                f"현재 가능한 작업: {available or 'BlockedScope 외 작업'}\n재개 조건: {row['resume_condition']}"
            )
            if mapping is None:
                created = self.notion.append_block_children(
                    review_page_id, [_paragraph_block(text)]
                )
                block_id = normalize_notion_id(str(created[0]["id"]))
                with self.database.transaction() as connection:
                    connection.execute(
                        "INSERT INTO notion_blocker_blocks(blocker_id, blocker_section_block_id, updated_at) VALUES (?, ?, ?)",
                        (row["blocker_id"], block_id, utc_now()),
                    )
            else:
                self.notion.update_block(
                    mapping["blocker_section_block_id"],
                    "paragraph",
                    _rich_text_value(text),
                )
                with self.database.transaction() as connection:
                    connection.execute(
                        "UPDATE notion_blocker_blocks SET updated_at = ? WHERE blocker_id = ?",
                        (utc_now(), row["blocker_id"]),
                    )
            count += 1
        return count

    def _status_text(self, planning_document_id: str) -> str:
        connection = self.database.connect()
        try:
            cycle = connection.execute(
                """
                SELECT status FROM review_cycles WHERE planning_document_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (planning_document_id,),
            ).fetchone()
            open_questions = connection.execute(
                """
                SELECT COUNT(*) AS c FROM open_questions oq JOIN findings f ON f.finding_id = oq.finding_id
                JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                WHERE rc.planning_document_id = ? AND oq.status IN ('OPEN','ANSWERED','VERIFYING','REOPENED')
                """,
                (planning_document_id,),
            ).fetchone()["c"]
            blockers = connection.execute(
                """
                SELECT COUNT(*) AS c FROM blockers b JOIN findings f ON f.finding_id = b.finding_id
                JOIN review_cycles rc ON rc.review_cycle_id = f.review_cycle_id
                WHERE rc.planning_document_id = ? AND b.status = 'ACTIVE'
                """,
                (planning_document_id,),
            ).fetchone()["c"]
        finally:
            connection.close()
        status = cycle["status"] if cycle else "PENDING"
        return f"{status} · 확인 필요 {open_questions}건 · Blocker {blockers}건"

    def _read_answer(self, block_id: str) -> str:
        lines: list[str] = []
        self._read_answer_children(block_id, lines)
        normalized = [
            " ".join(line.split()) for line in lines if " ".join(line.split())
        ]
        return "\n".join(normalized).strip()

    def _read_answer_children(self, block_id: str, lines: list[str]) -> None:
        for block in self.notion.list_block_children(block_id):
            block_type = block.get("type")
            payload = block.get(block_type) if block_type else None
            if isinstance(payload, dict):
                rich_text = payload.get("rich_text") or []
                text = "".join(
                    str(
                        item.get("plain_text")
                        or (item.get("text") or {}).get("content")
                        or ""
                    )
                    for item in rich_text
                )
                if text:
                    lines.append(text)
            if block.get("has_children") and block.get("id"):
                self._read_answer_children(str(block["id"]), lines)

    def _start_run(self, planning_document_id: str) -> str:
        run_id = new_id()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO projection_runs(projection_run_id, planning_document_id, status, started_at) VALUES (?, ?, 'RUNNING', ?)",
                (run_id, planning_document_id, utc_now()),
            )
        return run_id

    def _finish_run(
        self, run_id: str, status: str, failure_code: str | None = None
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE projection_runs SET status = ?, failure_code = ?, completed_at = ? WHERE projection_run_id = ?",
                (status, failure_code, utc_now(), run_id),
            )


def _rich_text_value(text: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}


def _paragraph_block(text: str) -> dict[str, Any]:
    return {"object": "block", "type": "paragraph", "paragraph": _rich_text_value(text)}


def _heading_block(text: str) -> dict[str, Any]:
    return {"object": "block", "type": "heading_2", "heading_2": _rich_text_value(text)}


def _toggle_block(text: str) -> dict[str, Any]:
    return {"object": "block", "type": "toggle", "toggle": _rich_text_value(text)}
