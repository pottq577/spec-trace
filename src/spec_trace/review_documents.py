from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import ResourceNotFound, ValidationError
from .notion import NotionPort, normalize_notion_id
from .source_export import SourceExportService
from .util import new_id, sha256_text, utc_now
from .workspace import Workspace

_MANAGED_MARKER = "specTraceManaged: true"
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_TODO_RE = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


class ReviewDocumentService:
    def __init__(self, workspace: Workspace, notion: NotionPort | None = None):
        self.workspace = workspace
        self.database = workspace.database
        self.notion = notion
        self.source_export = SourceExportService(workspace)

    def list_local(self, planning_document_id: str) -> dict[str, Any]:
        directory = self.source_export.document_directory(planning_document_id)
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)

        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT local_path, content_hash, notion_page_id, updated_at
                FROM published_review_documents
                WHERE planning_document_id = ?
                """,
                (planning_document_id,),
            ).fetchall()
        finally:
            connection.close()
        published = {row["local_path"]: dict(row) for row in rows}

        documents: list[dict[str, Any]] = []
        for path in sorted(directory.rglob("*.md")):
            relative = path.relative_to(directory)
            if relative.parts and relative.parts[0] == "responses":
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if _MANAGED_MARKER in content[:1000]:
                continue
            relative_path = relative.as_posix()
            digest = sha256_text(content)
            mapping = published.get(relative_path)
            documents.append(
                {
                    "path": relative_path,
                    "title": _markdown_title(content, path.stem),
                    "size": path.stat().st_size,
                    "published": bool(mapping and mapping["content_hash"] == digest),
                    "dirty": bool(mapping and mapping["content_hash"] != digest),
                    "notion_page_id": mapping["notion_page_id"] if mapping else None,
                    "updated_at": mapping["updated_at"] if mapping else None,
                }
            )
        return {
            "planning_document_id": planning_document_id,
            "directory": str(directory),
            "documents": documents,
        }

    def publish(
        self,
        planning_document_id: str,
        relative_paths: list[str],
    ) -> list[dict[str, Any]]:
        if self.notion is None:
            raise ValidationError(
                "Notion client is required to publish review documents"
            )
        if not relative_paths:
            raise ValidationError("select at least one Markdown document to publish")
        directory = self.source_export.document_directory(planning_document_id)
        review_page_id = self._ensure_review_page(planning_document_id)
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for relative_path in relative_paths:
            normalized = self._validated_relative_path(directory, relative_path)
            if normalized in seen:
                continue
            seen.add(normalized)
            results.append(
                self._publish_one(
                    planning_document_id,
                    directory,
                    review_page_id,
                    normalized,
                )
            )
        return results

    def collect_responses(
        self, planning_document_id: str | None = None
    ) -> list[dict[str, Any]]:
        if self.notion is None:
            raise ValidationError(
                "Notion client is required to collect review responses"
            )
        connection = self.database.connect()
        try:
            if planning_document_id:
                rows = connection.execute(
                    """
                    SELECT * FROM published_review_documents
                    WHERE planning_document_id = ?
                    ORDER BY updated_at, review_document_id
                    """,
                    (planning_document_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT prd.* FROM published_review_documents prd
                    JOIN planning_documents pd
                      ON pd.planning_document_id = prd.planning_document_id
                    WHERE pd.source_status = 'AVAILABLE'
                    ORDER BY prd.updated_at, prd.review_document_id
                    """
                ).fetchall()
        finally:
            connection.close()

        results: list[dict[str, Any]] = []
        for row in rows:
            answer = self._read_answer(row["answer_slot_block_id"])
            if not answer:
                continue
            answer_hash = sha256_text(answer)
            if row["last_answer_hash"] == answer_hash:
                continue
            response_path = self._write_response(dict(row), answer)
            now = utc_now()
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    UPDATE published_review_documents
                    SET last_answer_hash = ?, last_answer_at = ?, updated_at = ?
                    WHERE review_document_id = ?
                    """,
                    (answer_hash, now, now, row["review_document_id"]),
                )
            results.append(
                {
                    "planning_document_id": row["planning_document_id"],
                    "local_path": row["local_path"],
                    "title": row["title"],
                    "path": str(response_path),
                    "answer": answer,
                }
            )
        return results

    def _publish_one(
        self,
        planning_document_id: str,
        directory: Path,
        review_page_id: str,
        relative_path: str,
    ) -> dict[str, Any]:
        path = (directory / relative_path).resolve()
        content = path.read_text(encoding="utf-8")
        if _MANAGED_MARKER in content[:1000]:
            raise ValidationError(
                "Notion source Markdown cannot be published as a review document"
            )
        digest = sha256_text(content)
        title = _markdown_title(content, path.stem)

        connection = self.database.connect()
        try:
            mapping = connection.execute(
                """
                SELECT * FROM published_review_documents
                WHERE planning_document_id = ? AND local_path = ?
                """,
                (planning_document_id, relative_path),
            ).fetchone()
        finally:
            connection.close()

        if mapping and mapping["content_hash"] == digest:
            return {
                "path": relative_path,
                "title": mapping["title"],
                "notion_page_id": mapping["notion_page_id"],
                "status": "UNCHANGED",
            }

        previous_answer = ""
        if mapping:
            previous_answer = self._read_answer(mapping["answer_slot_block_id"])
            page_id = normalize_notion_id(mapping["notion_page_id"])
            self.notion.update_page_title(page_id, title)
            for child in self.notion.list_block_children(page_id):
                child_id = child.get("id")
                if child_id:
                    self.notion.delete_block(str(child_id))
            status = "UPDATED"
        else:
            created = self.notion.create_child_page(review_page_id, title)
            page_id = normalize_notion_id(str(created["id"]))
            status = "PUBLISHED"

        blocks = _markdown_to_blocks(content)
        self._append_chunks(page_id, blocks)
        footer = [
            {"object": "block", "type": "divider", "divider": {}},
            _toggle_block("기획자 답변"),
        ]
        created_footer = self.notion.append_block_children(page_id, footer)
        if len(created_footer) != 2:
            raise ValidationError(
                "Notion returned an incomplete review document footer"
            )
        answer_slot_id = normalize_notion_id(str(created_footer[-1]["id"]))
        if previous_answer:
            self._append_chunks(
                answer_slot_id,
                [
                    _paragraph_block(line)
                    for line in previous_answer.splitlines()
                    if line.strip()
                ],
            )

        now = utc_now()
        with self.database.transaction() as connection:
            if mapping:
                connection.execute(
                    """
                    UPDATE published_review_documents
                    SET title = ?, content_hash = ?, notion_page_id = ?,
                        answer_slot_block_id = ?, updated_at = ?
                    WHERE review_document_id = ?
                    """,
                    (
                        title,
                        digest,
                        page_id,
                        answer_slot_id,
                        now,
                        mapping["review_document_id"],
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO published_review_documents(
                        review_document_id, planning_document_id, local_path,
                        title, content_hash, notion_page_id,
                        answer_slot_block_id, published_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        planning_document_id,
                        relative_path,
                        title,
                        digest,
                        page_id,
                        answer_slot_id,
                        now,
                        now,
                    ),
                )
        return {
            "path": relative_path,
            "title": title,
            "notion_page_id": page_id,
            "status": status,
        }

    def _ensure_review_page(self, planning_document_id: str) -> str:
        connection = self.database.connect()
        try:
            document = connection.execute(
                """
                SELECT root_notion_page_id FROM planning_documents
                WHERE planning_document_id = ?
                """,
                (planning_document_id,),
            ).fetchone()
            mapping = connection.execute(
                """
                SELECT review_page_id FROM notion_review_pages
                WHERE planning_document_id = ?
                """,
                (planning_document_id,),
            ).fetchone()
        finally:
            connection.close()
        if document is None:
            raise ResourceNotFound(
                f"planning document not found: {planning_document_id}"
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
                INSERT INTO notion_review_pages(
                    planning_document_id, review_page_id, updated_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(planning_document_id) DO UPDATE SET
                    review_page_id = excluded.review_page_id,
                    updated_at = excluded.updated_at
                """,
                (planning_document_id, review_page_id, utc_now()),
            )
        return review_page_id

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
            if block.get("id"):
                children = self.notion.list_block_children(str(block["id"]))
                if children:
                    self._read_answer_children(str(block["id"]), lines)

    def _write_response(self, mapping: dict[str, Any], answer: str) -> Path:
        directory = self.source_export.document_directory(
            mapping["planning_document_id"]
        )
        local = Path(mapping["local_path"])
        response_dir = directory / "responses" / local.with_suffix("")
        response_dir.mkdir(parents=True, exist_ok=True)
        answered_at = utc_now()
        filename = answered_at.replace("-", "").replace(":", "").replace(".", "")
        filename = filename.replace("Z", "Z") + ".md"
        path = response_dir / filename
        content = (
            "---\n"
            f"title: {json.dumps(mapping['title'] + ' 기획자 답변', ensure_ascii=False)}\n"
            f"planningDocumentId: {json.dumps(mapping['planning_document_id'])}\n"
            f"reviewDocument: {json.dumps(mapping['local_path'], ensure_ascii=False)}\n"
            f"notionPageId: {json.dumps(mapping['notion_page_id'])}\n"
            f"answeredAt: {json.dumps(answered_at)}\n"
            "---\n\n"
            f"# {mapping['title']} 기획자 답변\n\n"
            f"{answer.rstrip()}\n"
        )
        path.write_text(content, encoding="utf-8", newline="\n")
        return path

    def _validated_relative_path(self, directory: Path, value: str) -> str:
        relative = Path(str(value).strip())
        if not str(relative) or relative.is_absolute() or ".." in relative.parts:
            raise ValidationError(f"invalid local Markdown path: {value}")
        candidate = (directory / relative).resolve()
        try:
            candidate.relative_to(directory.resolve())
        except ValueError as exc:
            raise ValidationError(
                f"local Markdown path escapes document directory: {value}"
            ) from exc
        if candidate.suffix.lower() != ".md" or not candidate.is_file():
            raise ValidationError(f"local Markdown file not found: {value}")
        return candidate.relative_to(directory.resolve()).as_posix()

    def _append_chunks(self, block_id: str, blocks: list[dict[str, Any]]) -> None:
        for start in range(0, len(blocks), 90):
            chunk = blocks[start : start + 90]
            if not chunk:
                continue
            created = self.notion.append_block_children(block_id, chunk)
            if len(created) != len(chunk):
                raise ValidationError("Notion returned an incomplete block append")


def _markdown_title(content: str, fallback: str) -> str:
    in_frontmatter = content.startswith("---\n")
    for index, line in enumerate(content.splitlines()):
        if in_frontmatter:
            if index > 0 and line.strip() == "---":
                in_frontmatter = False
            continue
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()[:200]
    return fallback[:200]


def _markdown_to_blocks(content: str) -> list[dict[str, Any]]:
    lines = content.splitlines()
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                lines = lines[index + 1 :]
                break

    blocks: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            language = stripped[3:].strip() or "plain text"
            index += 1
            code_lines: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append(_code_block("\n".join(code_lines), language))
            continue

        if (
            stripped.startswith("|")
            and index + 1 < len(lines)
            and _TABLE_SEPARATOR_RE.match(lines[index + 1])
        ):
            table_lines = [line]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            blocks.append(_table_block(table_lines))
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = min(3, len(heading.group(1)))
            blocks.append(_heading_block(heading.group(2).strip(), level))
            index += 1
            continue

        todo = _TODO_RE.match(line)
        if todo:
            blocks.append(_todo_block(todo.group(2), todo.group(1).lower() == "x"))
            index += 1
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            blocks.append(_list_block("bulleted_list_item", bullet.group(1)))
            index += 1
            continue

        numbered = _NUMBERED_RE.match(line)
        if numbered:
            blocks.append(_list_block("numbered_list_item", numbered.group(1)))
            index += 1
            continue

        if stripped in {"---", "***", "___"}:
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            index += 1
            continue

        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip().lstrip(">").strip())
                index += 1
            blocks.append(_text_block("quote", "\n".join(quote_lines)))
            continue

        paragraph = [stripped]
        index += 1
        while index < len(lines):
            candidate = lines[index]
            if not candidate.strip() or _is_special(candidate, lines, index):
                break
            paragraph.append(candidate.strip())
            index += 1
        blocks.append(_paragraph_block("\n".join(paragraph)))

    return blocks


def _is_special(line: str, lines: list[str], index: int) -> bool:
    stripped = line.strip()
    return bool(
        stripped.startswith(("```", ">"))
        or _HEADING_RE.match(line)
        or _TODO_RE.match(line)
        or _BULLET_RE.match(line)
        or _NUMBERED_RE.match(line)
        or stripped in {"---", "***", "___"}
        or (
            stripped.startswith("|")
            and index + 1 < len(lines)
            and _TABLE_SEPARATOR_RE.match(lines[index + 1])
        )
    )


def _rich_text(text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    return [
        {"type": "text", "text": {"content": text[start : start + 1900]}}
        for start in range(0, len(text), 1900)
    ]


def _paragraph_block(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich_text(text)},
    }


def _heading_block(text: str, level: int) -> dict[str, Any]:
    block_type = f"heading_{level}"
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": _rich_text(text)},
    }


def _toggle_block(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {"rich_text": _rich_text(text)},
    }


def _list_block(block_type: str, text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": _rich_text(text)},
    }


def _todo_block(text: str, checked: bool) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "to_do",
        "to_do": {"rich_text": _rich_text(text), "checked": checked},
    }


def _text_block(block_type: str, text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": _rich_text(text)},
    }


def _code_block(text: str, language: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "code",
        "code": {
            "rich_text": _rich_text(text),
            "language": language,
        },
    }


def _table_block(lines: list[str]) -> dict[str, Any]:
    rows = [_split_table_row(line) for line in lines]
    width = max((len(row) for row in rows), default=1)
    children = []
    for row in rows:
        padded = row + [""] * (width - len(row))
        children.append(
            {
                "object": "block",
                "type": "table_row",
                "table_row": {"cells": [_rich_text(cell) for cell in padded]},
            }
        )
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": width,
            "has_column_header": True,
            "has_row_header": False,
            "children": children,
        },
    }


def _split_table_row(line: str) -> list[str]:
    value = line.strip()
    value = value.removeprefix("|")
    value = value.removesuffix("|")
    return [cell.strip() for cell in value.split("|")]
