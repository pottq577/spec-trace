from __future__ import annotations

from typing import Any

from .collector import SourceCollector
from .errors import StateConflict, ValidationError
from .notion import NotionPort, normalize_notion_id
from .planning_documents import PlanningDocumentService
from .projection import ProjectionService
from .workspace import Workspace


class LiveSmokeService:
    def __init__(self, workspace: Workspace, notion: NotionPort):
        self.workspace = workspace
        self.notion = notion

    def run(
        self,
        database_id: str,
        page_id: str,
        *,
        allow_write: bool = False,
    ) -> dict[str, Any]:
        normalized_database = normalize_notion_id(database_id)
        normalized_page = normalize_notion_id(page_id)
        page = self.notion.retrieve_page(normalized_page)
        database = self.notion.retrieve_database(normalized_database)
        self._validate_membership(page, database, normalized_database)
        children = self.notion.list_block_children(normalized_page)

        result: dict[str, Any] = {
            "database_id": normalized_database,
            "page_id": normalized_page,
            "page_accessible": bool(page),
            "database_accessible": bool(database),
            "top_level_block_count": len(children),
            "write_checked": False,
        }
        if not allow_write:
            return result

        document = PlanningDocumentService(
            self.workspace.database, self.notion
        ).register(normalized_database, normalized_page)
        collector = SourceCollector(
            self.workspace.database, self.workspace.content_store, self.notion
        )
        first_collection = collector.collect(document.planning_document_id)
        if first_collection.status not in {"SNAPSHOT_CREATED", "UNCHANGED"}:
            raise StateConflict(
                f"live smoke source collection failed: {first_collection.status}"
            )
        projection = ProjectionService(self.workspace.database, self.notion)
        first_projection = projection.sync(document.planning_document_id)
        second_projection = projection.sync(document.planning_document_id)
        after_projection = collector.collect(document.planning_document_id)

        if after_projection.status != "UNCHANGED":
            raise StateConflict(
                "live smoke projection changed the tracked source snapshot"
            )
        if any(
            second_projection[key]
            for key in (
                "decisions_projected",
                "questions_projected",
            )
        ):
            raise StateConflict("live smoke projection is not idempotent")
        if first_projection["review_page_id"] != second_projection["review_page_id"]:
            raise StateConflict("live smoke created more than one review page")

        result.update(
            {
                "write_checked": True,
                "planning_document_id": document.planning_document_id,
                "initial_collection": first_collection.__dict__,
                "first_projection": first_projection,
                "second_projection": second_projection,
                "post_projection_collection": after_projection.__dict__,
            }
        )
        return result

    @staticmethod
    def _validate_membership(
        page: dict[str, Any], database: dict[str, Any], database_id: str
    ) -> None:
        parent = page.get("parent") or {}
        if parent.get("type") == "database_id":
            parent_id = normalize_notion_id(str(parent.get("database_id") or ""))
            if parent_id == database_id:
                return
        if parent.get("type") == "data_source_id":
            parent_id = normalize_notion_id(str(parent.get("data_source_id") or ""))
            data_sources = database.get("data_sources") or []
            ids = {
                normalize_notion_id(str(item.get("id") or ""))
                for item in data_sources
                if item.get("id")
            }
            if parent_id in ids:
                return
        raise ValidationError(
            "live smoke page does not belong to the configured database"
        )
