from __future__ import annotations

from dataclasses import dataclass

from .canonical import extract_page_title
from .db import Database
from .errors import ResourceNotFound, ValidationError
from .notion import NotionPort, normalize_notion_id
from .util import new_id, utc_now


@dataclass(frozen=True)
class PlanningDocumentRecord:
    planning_document_id: str
    notion_database_id: str
    root_notion_page_id: str
    title: str
    source_status: str
    current_snapshot_id: str | None


class PlanningDocumentService:
    def __init__(self, database: Database, notion: NotionPort):
        self.database = database
        self.notion = notion

    def register(self, notion_database_id: str, root_page_id: str) -> PlanningDocumentRecord:
        database_id = normalize_notion_id(notion_database_id)
        page_id = normalize_notion_id(root_page_id)
        page = self.notion.retrieve_page(page_id)
        if page.get("archived") or page.get("in_trash"):
            raise ResourceNotFound("Notion root page is archived or in trash")
        self._validate_parent_database(page, database_id)
        title = extract_page_title(page)
        if not title:
            raise ValidationError("Notion root page has no title")
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM planning_documents
                WHERE notion_database_id = ? AND root_notion_page_id = ?
                """,
                (database_id, page_id),
            ).fetchone()
            if row is None:
                document_id = new_id()
                connection.execute(
                    """
                    INSERT INTO planning_documents(
                        planning_document_id, notion_database_id, root_notion_page_id,
                        title, source_status, created_at
                    ) VALUES (?, ?, ?, ?, 'AVAILABLE', ?)
                    """,
                    (document_id, database_id, page_id, title, now),
                )
            else:
                document_id = row["planning_document_id"]
                connection.execute(
                    """
                    UPDATE planning_documents
                    SET title = ?, source_status = 'AVAILABLE'
                    WHERE planning_document_id = ?
                    """,
                    (title, document_id),
                )
        return self.get(document_id)

    def _validate_parent_database(self, page: dict, database_id: str) -> None:
        parent = page.get("parent") or {}
        parent_type = parent.get("type")
        if parent_type == "database_id":
            if normalize_notion_id(str(parent.get("database_id") or "")) != database_id:
                raise ValidationError("Notion page does not belong to the requested database")
            return
        if parent_type == "data_source_id":
            data_source_id = normalize_notion_id(str(parent.get("data_source_id") or ""))
            database = self.notion.retrieve_database(database_id)
            data_sources = database.get("data_sources") or []
            ids = {normalize_notion_id(str(item.get("id") or "")) for item in data_sources if item.get("id")}
            if data_source_id not in ids:
                raise ValidationError("Notion page data source does not belong to the requested database")
            return
        raise ValidationError("Notion root page must be a database row")

    def get(self, planning_document_id: str) -> PlanningDocumentRecord:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM planning_documents WHERE planning_document_id = ?",
                (planning_document_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise ResourceNotFound(f"planning document not found: {planning_document_id}")
        return PlanningDocumentRecord(
            planning_document_id=row["planning_document_id"],
            notion_database_id=row["notion_database_id"],
            root_notion_page_id=row["root_notion_page_id"],
            title=row["title"],
            source_status=row["source_status"],
            current_snapshot_id=row["current_snapshot_id"],
        )
