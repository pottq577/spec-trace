from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .config import SettingsService
from .errors import ResourceNotFound, ValidationError
from .workspace import Workspace

_MANAGED_MARKER = "specTraceManaged: true"
_PREFIX_RE = re.compile(r"^\s*\d+[_\-\s]*")
_INVALID_FILENAME_RE = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")


class SourceExportService:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.database = workspace.database
        self.content_store = workspace.content_store
        self.settings = SettingsService(workspace)

    def export(
        self, planning_document_id: str, output_root: Path | None = None
    ) -> dict[str, Any]:
        document, snapshot, pages = self._load_snapshot(planning_document_id)
        root = (
            (output_root or self.settings.resolve_export_root()).expanduser().resolve()
        )
        if not root.is_dir():
            raise ValidationError(f"document export root is not a directory: {root}")

        target_dir = self._resolve_document_directory(
            root,
            document["root_notion_page_id"],
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        target = self._managed_target(target_dir, document["title"])

        page_map = {row["notion_page_id"]: row for row in pages}
        root_page = page_map.get(document["root_notion_page_id"])
        if root_page is None:
            raise ResourceNotFound(
                "current snapshot does not contain the root Notion page"
            )

        markdown = self._render_document(document, snapshot, root_page, page_map)
        self._atomic_write(target, markdown)
        return {
            "planning_document_id": planning_document_id,
            "snapshot_id": snapshot["planning_document_snapshot_id"],
            "path": str(target),
            "pages": len(page_map),
        }

    def document_directory(self, planning_document_id: str) -> Path:
        connection = self.database.connect()
        try:
            document = connection.execute(
                """
                SELECT root_notion_page_id FROM planning_documents
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
        root = self.settings.resolve_export_root()
        return self._resolve_document_directory(root, document["root_notion_page_id"])

    def _load_snapshot(self, planning_document_id: str):
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
            snapshot_id = document["current_snapshot_id"]
            if not snapshot_id:
                raise ValidationError("document has no collected snapshot yet")
            snapshot = connection.execute(
                """
                SELECT * FROM planning_document_snapshots
                WHERE planning_document_snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchone()
            pages = connection.execute(
                """
                SELECT psp.notion_page_id, psp.parent_notion_page_id, psp.role,
                       sp.title, sps.content_ref
                FROM planning_snapshot_pages psp
                JOIN source_pages sp ON sp.source_page_id = psp.source_page_id
                JOIN source_page_snapshots sps
                  ON sps.source_page_snapshot_id = psp.source_page_snapshot_id
                WHERE psp.planning_document_snapshot_id = ?
                ORDER BY psp.role DESC, sp.title, psp.notion_page_id
                """,
                (snapshot_id,),
            ).fetchall()
        finally:
            connection.close()
        if snapshot is None:
            raise ResourceNotFound(f"snapshot not found: {snapshot_id}")
        materialized = []
        for row in pages:
            item = dict(row)
            item["content"] = self.content_store.read_json(item.pop("content_ref"))
            materialized.append(item)
        return dict(document), dict(snapshot), materialized

    def _resolve_document_directory(self, root: Path, notion_page_id: str) -> Path:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT root_notion_page_id, title, menu_parent_notion_page_id
                FROM planning_documents
                """
            ).fetchall()
        finally:
            connection.close()
        by_id = {row["root_notion_page_id"]: dict(row) for row in rows}
        lineage: list[str] = []
        current = by_id.get(notion_page_id)
        seen: set[str] = set()
        while current is not None:
            current_id = current["root_notion_page_id"]
            if current_id in seen:
                raise ValidationError("Notion menu hierarchy contains a cycle")
            seen.add(current_id)
            lineage.append(current["title"])
            parent_id = current["menu_parent_notion_page_id"]
            current = by_id.get(parent_id) if parent_id else None
        lineage.reverse()

        directory = root
        for title in lineage:
            directory = self._match_or_create_child(directory, title)
        return directory

    def _match_or_create_child(self, parent: Path, title: str) -> Path:
        safe_title = self._safe_component(title)
        exact = parent / safe_title
        if exact.is_dir():
            return exact
        normalized = self._normalized_name(safe_title)
        matches = (
            [
                child
                for child in parent.iterdir()
                if child.is_dir() and self._normalized_name(child.name) == normalized
            ]
            if parent.is_dir()
            else []
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValidationError(
                f"multiple local directories match Notion menu '{title}': "
                + ", ".join(str(path) for path in matches)
            )
        exact.mkdir(parents=True, exist_ok=True)
        return exact

    def _managed_target(self, directory: Path, title: str) -> Path:
        stem = self._safe_component(title)
        primary = directory / f"{stem}.md"
        alternate = directory / f"{stem}.notion.md"
        for candidate in (primary, alternate):
            if candidate.exists() and self._is_managed(candidate):
                return candidate
        if not primary.exists():
            return primary
        if not alternate.exists():
            return alternate
        raise ValidationError(
            f"refusing to overwrite unmanaged Markdown files: {primary}, {alternate}"
        )

    @staticmethod
    def _is_managed(path: Path) -> bool:
        try:
            head = path.read_text(encoding="utf-8")[:1000]
        except OSError:
            return False
        return _MANAGED_MARKER in head

    def render_canonical_page(self, content: dict[str, Any]) -> str:
        title = str(content.get("title") or "").strip()
        output = [f"# {title}", ""] if title else []
        output.extend(
            self._render_blocks(
                content.get("blocks") or [],
                {},
                page_depth=0,
                visited=set(),
            )
        )
        return "\n".join(output).rstrip() + "\n"

    def _render_document(
        self,
        document: dict[str, Any],
        snapshot: dict[str, Any],
        root_page: dict[str, Any],
        page_map: dict[str, dict[str, Any]],
    ) -> str:
        metadata = [
            "---",
            _yaml_line("title", document["title"]),
            "specTraceManaged: true",
            _yaml_line("planningDocumentId", document["planning_document_id"]),
            _yaml_line("notionPageId", document["root_notion_page_id"]),
            _yaml_line("snapshotId", snapshot["planning_document_snapshot_id"]),
            _yaml_line("capturedAt", snapshot["captured_at"]),
            "source: notion",
            "---",
            "",
        ]
        visited: set[str] = set()
        body = self._render_page(
            root_page,
            page_map,
            depth=0,
            visited=visited,
        )
        orphaned = [
            page for page_id, page in page_map.items() if page_id not in visited
        ]
        if orphaned:
            body += "\n## 하위 문서\n\n"
            for page in sorted(orphaned, key=lambda value: value["title"]):
                body += self._render_page(page, page_map, depth=1, visited=visited)
        return "\n".join(metadata) + body.rstrip() + "\n"

    def _render_page(
        self,
        page: dict[str, Any],
        page_map: dict[str, dict[str, Any]],
        *,
        depth: int,
        visited: set[str],
    ) -> str:
        page_id = page["notion_page_id"]
        if page_id in visited:
            return ""
        visited.add(page_id)
        heading_level = min(6, depth + 1)
        output = [f"{'#' * heading_level} {page['title']}", ""]
        blocks = (page.get("content") or {}).get("blocks") or []
        output.extend(
            self._render_blocks(
                blocks,
                page_map,
                page_depth=depth,
                visited=visited,
            )
        )
        return "\n".join(output).rstrip() + "\n\n"

    def _render_blocks(
        self,
        blocks: list[dict[str, Any]],
        page_map: dict[str, dict[str, Any]],
        *,
        page_depth: int,
        visited: set[str],
        indent: int = 0,
    ) -> list[str]:
        output: list[str] = []
        number = 1
        for block in blocks:
            block_type = str(block.get("type") or "")
            if block_type == "child_page":
                child_id = str(block.get("notion_page_id") or "")
                child = page_map.get(child_id)
                if child is not None:
                    output.append(
                        self._render_page(
                            child,
                            page_map,
                            depth=page_depth + 1,
                            visited=visited,
                        ).rstrip()
                    )
                    output.append("")
                continue

            value = block.get("value") if isinstance(block.get("value"), dict) else {}
            rich = _rich_text(value.get("rich_text") or [])
            prefix = "  " * indent
            if block_type == "paragraph":
                output.extend([prefix + rich if rich else "", ""])
            elif block_type.startswith("heading_"):
                level = int(block_type[-1]) if block_type[-1:].isdigit() else 1
                markdown_level = min(6, page_depth + level + 1)
                output.extend([f"{'#' * markdown_level} {rich}", ""])
            elif block_type == "bulleted_list_item":
                output.append(f"{prefix}- {rich}")
            elif block_type == "numbered_list_item":
                output.append(f"{prefix}{number}. {rich}")
                number += 1
            elif block_type == "to_do":
                checked = "x" if value.get("checked") else " "
                output.append(f"{prefix}- [{checked}] {rich}")
            elif block_type == "toggle":
                output.extend([f"{prefix}<details><summary>{rich}</summary>", ""])
            elif block_type == "quote":
                lines = rich.splitlines() or [""]
                output.extend([prefix + "> " + line for line in lines])
                output.append("")
            elif block_type == "callout":
                output.extend([f"{prefix}> {rich}", ""])
            elif block_type == "code":
                language = str(value.get("language") or "")
                output.extend([f"{prefix}```{language}", rich, f"{prefix}```", ""])
            elif block_type == "divider":
                output.extend([prefix + "---", ""])
            elif block_type == "bookmark":
                url = str(value.get("url") or "")
                caption = _rich_text(value.get("caption") or [])
                output.extend([f"[{caption or url}]({url})" if url else caption, ""])
            elif block_type in {"image", "file", "pdf", "video"}:
                url = _file_url(value)
                caption = _rich_text(value.get("caption") or [])
                if url:
                    if block_type == "image":
                        output.extend([f"![{caption}]({url})", ""])
                    else:
                        output.extend([f"[{caption or block_type}]({url})", ""])
            elif block_type == "equation":
                expression = str(value.get("expression") or "")
                output.extend([f"$$\n{expression}\n$$", ""])
            elif block_type == "table":
                output.extend(_render_table(block.get("children") or [], value))
            elif rich:
                output.extend([prefix + rich, ""])

            children = block.get("children") or []
            if children and block_type != "table":
                output.extend(
                    self._render_blocks(
                        children,
                        page_map,
                        page_depth=page_depth,
                        visited=visited,
                        indent=indent + 1,
                    )
                )
                if block_type == "toggle":
                    output.extend([f"{prefix}</details>", ""])
        return output

    @staticmethod
    def _safe_component(value: str) -> str:
        cleaned = _INVALID_FILENAME_RE.sub("_", str(value)).strip().rstrip(".")
        if cleaned in {"", ".", ".."}:
            raise ValidationError(f"invalid document title for local path: {value!r}")
        return cleaned

    @staticmethod
    def _normalized_name(value: str) -> str:
        return _PREFIX_RE.sub("", value).replace(" ", "").replace("-", "_").casefold()

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _yaml_line(key: str, value: Any) -> str:
    return f"{key}: {json.dumps(str(value), ensure_ascii=False)}"


def _rich_text(values: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in values:
        text_value = item.get("text")
        text_payload = text_value if isinstance(text_value, dict) else {}
        text = str(item.get("plain_text") or text_payload.get("content") or "")
        if not text:
            continue
        annotations = item.get("annotations") or {}
        if annotations.get("code"):
            text = "`" + text.replace("`", "\\`") + "`"
        if annotations.get("bold"):
            text = f"**{text}**"
        if annotations.get("italic"):
            text = f"*{text}*"
        if annotations.get("strikethrough"):
            text = f"~~{text}~~"
        link_value = text_payload.get("link")
        link_payload = link_value if isinstance(link_value, dict) else {}
        href = item.get("href") or link_payload.get("url")
        if href:
            text = f"[{text}]({href})"
        parts.append(text)
    return "".join(parts)


def _file_url(value: dict[str, Any]) -> str:
    block_type = value.get("type")
    if block_type and isinstance(value.get(block_type), dict):
        return str(value[block_type].get("url") or "")
    for key in ("external", "file"):
        if isinstance(value.get(key), dict) and value[key].get("url"):
            return str(value[key]["url"])
    return ""


def _render_table(children: list[dict[str, Any]], value: dict[str, Any]) -> list[str]:
    rows: list[list[str]] = []
    for child in children:
        if child.get("type") != "table_row":
            continue
        row_value = child.get("value") or {}
        cells = row_value.get("cells") or []
        rows.append([_rich_text(cell) for cell in cells])
    if not rows:
        return []
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    output = ["| " + " | ".join(header) + " |"]
    output.append("| " + " | ".join("---" for _ in range(width)) + " |")
    for row in rows[1:]:
        output.append("| " + " | ".join(row) + " |")
    output.append("")
    return output
