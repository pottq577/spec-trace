from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .notion import normalize_notion_id
from .workspace import Workspace


@dataclass(frozen=True)
class NotionSourceSettings:
    database_id: str
    data_source_id: str
    parent_property: str = "상위 항목"


@dataclass(frozen=True)
class WorkspaceSettings:
    notion_source: NotionSourceSettings | None = None
    export_root: str | None = None

    def to_dict(self) -> dict[str, Any]:
        source = None
        if self.notion_source is not None:
            source = {
                "database_id": self.notion_source.database_id,
                "data_source_id": self.notion_source.data_source_id,
                "parent_property": self.notion_source.parent_property,
            }
        return {
            "notion_source": source,
            "export_root": self.export_root,
        }


class SettingsService:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.path = workspace.config_path

    def load(self) -> WorkspaceSettings:
        if not self.path.exists():
            return WorkspaceSettings()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(f"invalid workspace config: {self.path}") from exc
        if not isinstance(payload, dict):
            raise ValidationError("workspace config must be a JSON object")

        source_payload = payload.get("notion_source")
        source = None
        if source_payload is not None:
            if not isinstance(source_payload, dict):
                raise ValidationError("notion_source must be a JSON object")
            source = NotionSourceSettings(
                database_id=normalize_notion_id(str(source_payload.get("database_id") or "")),
                data_source_id=normalize_notion_id(str(source_payload.get("data_source_id") or "")),
                parent_property=self._parent_property(source_payload.get("parent_property")),
            )

        export_root = payload.get("export_root")
        if export_root is not None:
            export_root = str(export_root).strip() or None

        return WorkspaceSettings(notion_source=source, export_root=export_root)

    def set_notion_source(
        self,
        database_id: str,
        data_source_id: str,
        *,
        parent_property: str = "상위 항목",
    ) -> WorkspaceSettings:
        current = self.load()
        source = NotionSourceSettings(
            database_id=normalize_notion_id(database_id),
            data_source_id=normalize_notion_id(data_source_id),
            parent_property=self._parent_property(parent_property),
        )
        updated = WorkspaceSettings(
            notion_source=source,
            export_root=current.export_root,
        )
        self.save(updated)
        return updated

    def set_export_root(self, path: str, *, create: bool = False) -> WorkspaceSettings:
        current = self.load()
        resolved = self._resolve_export_root(path, create=create)
        updated = WorkspaceSettings(
            notion_source=current.notion_source,
            export_root=str(resolved),
        )
        self.save(updated)
        return updated

    def clear_export_root(self) -> WorkspaceSettings:
        current = self.load()
        updated = WorkspaceSettings(notion_source=current.notion_source)
        self.save(updated)
        return updated

    def suggested_export_root(self) -> str | None:
        candidates = (
            self.workspace.root.parent / "PEOPLO" / "docs" / "PRD_Notion",
            self.workspace.root / "docs" / "PRD_Notion",
        )
        for candidate in candidates:
            if candidate.is_dir():
                return str(candidate.resolve())
        return None

    def resolve_export_root(self) -> Path:
        settings = self.load()
        if settings.export_root:
            return self._resolve_export_root(settings.export_root, create=False)
        suggested = self.suggested_export_root()
        if suggested:
            return Path(suggested)
        raise ValidationError(
            "document export root is not configured; choose a local directory first"
        )

    def save(self, settings: WorkspaceSettings) -> None:
        self.workspace.state_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            settings.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        fd, temporary = tempfile.mkstemp(
            prefix=".config.",
            suffix=".json",
            dir=self.workspace.state_dir,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _resolve_export_root(self, value: str, *, create: bool) -> Path:
        text = str(value).strip()
        if not text:
            raise ValidationError("document export root is required")
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = self.workspace.root / path
        path = path.resolve()
        if create:
            path.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            raise ValidationError(f"document export root does not exist: {path}")
        if not path.is_dir():
            raise ValidationError(f"document export root is not a directory: {path}")
        if not os.access(path, os.W_OK):
            raise ValidationError(f"document export root is not writable: {path}")
        return path

    @staticmethod
    def _parent_property(value: Any) -> str:
        text = str(value or "상위 항목").strip()
        if not text:
            raise ValidationError("Notion parent property is required")
        return text
