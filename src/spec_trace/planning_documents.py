from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    notion_data_source_id: str | None
    menu_parent_notion_page_id: str | None


class PlanningDocumentService:
    def __init__(self, database: Database, notion: NotionPort):
        self.database = database
        self.notion = notion

    def register(
        self, notion_database_id: str, root_page_id: str
    ) -> PlanningDocumentRecord:
        database_id = normalize_notion_id(notion_database_id)
        page_id = normalize_notion_id(root_page_id)
        page = self.notion.retrieve_page(page_id)
        if page.get("archived") or page.get("in_trash"):
            raise ResourceNotFound("Notion root page is archived or in trash")
        self._validate_parent_database(page, database_id)
        title = extract_page_title(page)
        if not title:
            raise ValidationError("Notion root page has no title")
        source_last_edited_time = str(page.get("last_edited_time") or "")
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
                        planning_document_id, notion_database_id,
                        root_notion_page_id, title, source_status, created_at,
                        source_last_edited_time
                    ) VALUES (?, ?, ?, ?, 'AVAILABLE', ?, ?)
                    """,
                    (
                        document_id,
                        database_id,
                        page_id,
                        title,
                        now,
                        source_last_edited_time,
                    ),
                )
            else:
                document_id = row["planning_document_id"]
                connection.execute(
                    """
                    UPDATE planning_documents
                    SET title = ?, source_status = 'AVAILABLE',
                        source_last_edited_time = ?
                    WHERE planning_document_id = ?
                    """,
                    (title, source_last_edited_time, document_id),
                )
        return self.get(document_id)

    def sync_data_source(
        self,
        notion_database_id: str,
        data_source_id: str,
        *,
        parent_property: str = "상위 항목",
    ) -> dict[str, Any]:
        database_id = normalize_notion_id(notion_database_id)
        normalized_data_source_id = normalize_notion_id(data_source_id)
        database = self.notion.retrieve_database(database_id)
        self._validate_data_source(database, normalized_data_source_id)

        pages = self.notion.query_data_source(normalized_data_source_id)
        candidates: list[tuple[str, str, str | None, str]] = []
        seen_page_ids: set[str] = set()
        skipped = 0

        for page in pages:
            if page.get("object") != "page":
                skipped += 1
                continue
            if page.get("archived") or page.get("in_trash"):
                skipped += 1
                continue

            page_id = normalize_notion_id(str(page.get("id") or ""))
            if page_id in seen_page_ids:
                raise ValidationError(
                    f"Notion data source returned duplicate page: {page_id}"
                )
            seen_page_ids.add(page_id)

            self._validate_parent_data_source(page, normalized_data_source_id)
            title = extract_page_title(page)
            if not title:
                raise ValidationError(f"Notion page has no title: {page_id}")
            parent_page_id = self._menu_parent(page, parent_property)
            candidates.append(
                (
                    page_id,
                    title,
                    parent_page_id,
                    str(page.get("last_edited_time") or ""),
                )
            )

        created = 0
        updated = 0
        unchanged = 0
        unavailable = 0
        now = utc_now()

        with self.database.transaction() as connection:
            existing_for_source = connection.execute(
                """
                SELECT planning_document_id, root_notion_page_id, source_status
                FROM planning_documents
                WHERE notion_data_source_id = ?
                """,
                (normalized_data_source_id,),
            ).fetchall()

            active_page_ids: set[str] = set()
            for page_id, title, parent_page_id, source_last_edited_time in candidates:
                active_page_ids.add(page_id)
                row = connection.execute(
                    """
                    SELECT * FROM planning_documents
                    WHERE notion_database_id = ? AND root_notion_page_id = ?
                    """,
                    (database_id, page_id),
                ).fetchone()

                if row is None:
                    connection.execute(
                        """
                        INSERT INTO planning_documents(
                            planning_document_id, notion_database_id,
                            root_notion_page_id, title, source_status,
                            created_at, notion_data_source_id,
                            menu_parent_notion_page_id,
                            source_last_edited_time
                        ) VALUES (?, ?, ?, ?, 'AVAILABLE', ?, ?, ?, ?)
                        """,
                        (
                            new_id(),
                            database_id,
                            page_id,
                            title,
                            now,
                            normalized_data_source_id,
                            parent_page_id,
                            source_last_edited_time,
                        ),
                    )
                    created += 1
                    continue

                changed = (
                    row["title"] != title
                    or row["source_status"] != "AVAILABLE"
                    or row["notion_data_source_id"] != normalized_data_source_id
                    or row["menu_parent_notion_page_id"] != parent_page_id
                    or row["source_last_edited_time"] != source_last_edited_time
                )
                connection.execute(
                    """
                    UPDATE planning_documents
                    SET title = ?, source_status = 'AVAILABLE',
                        notion_data_source_id = ?,
                        menu_parent_notion_page_id = ?,
                        source_last_edited_time = ?
                    WHERE planning_document_id = ?
                    """,
                    (
                        title,
                        normalized_data_source_id,
                        parent_page_id,
                        source_last_edited_time,
                        row["planning_document_id"],
                    ),
                )
                if changed:
                    updated += 1
                else:
                    unchanged += 1

            for row in existing_for_source:
                if row["root_notion_page_id"] in active_page_ids:
                    continue
                if row["source_status"] != "UNAVAILABLE":
                    unavailable += 1
                connection.execute(
                    """
                    UPDATE planning_documents
                    SET source_status = 'UNAVAILABLE'
                    WHERE planning_document_id = ?
                    """,
                    (row["planning_document_id"],),
                )

        return {
            "database_id": database_id,
            "data_source_id": normalized_data_source_id,
            "pages_seen": len(pages),
            "active_pages": len(candidates),
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "unavailable": unavailable,
            "skipped": skipped,
        }

    def _validate_parent_database(self, page: dict, database_id: str) -> None:
        parent = page.get("parent") or {}
        parent_type = parent.get("type")
        if parent_type == "database_id":
            if normalize_notion_id(str(parent.get("database_id") or "")) != database_id:
                raise ValidationError(
                    "Notion page does not belong to the requested database"
                )
            return
        if parent_type == "data_source_id":
            data_source_id = normalize_notion_id(
                str(parent.get("data_source_id") or "")
            )
            database = self.notion.retrieve_database(database_id)
            data_sources = database.get("data_sources") or []
            ids = {
                normalize_notion_id(str(item.get("id") or ""))
                for item in data_sources
                if item.get("id")
            }
            if data_source_id not in ids:
                raise ValidationError(
                    "Notion page data source does not belong to the requested database"
                )
            return
        raise ValidationError("Notion root page must be a database row")

    @staticmethod
    def _validate_data_source(database: dict[str, Any], data_source_id: str) -> None:
        data_sources = database.get("data_sources") or []
        ids = {
            normalize_notion_id(str(item.get("id") or ""))
            for item in data_sources
            if item.get("id")
        }
        if data_source_id not in ids:
            raise ValidationError(
                "Notion data source does not belong to the requested database"
            )

    @staticmethod
    def _validate_parent_data_source(page: dict[str, Any], data_source_id: str) -> None:
        parent = page.get("parent") or {}
        if parent.get("type") != "data_source_id":
            raise ValidationError("Notion source sync result is not a data source row")
        actual = normalize_notion_id(str(parent.get("data_source_id") or ""))
        if actual != data_source_id:
            raise ValidationError(
                "Notion source sync result belongs to another data source"
            )

    @staticmethod
    def _menu_parent(page: dict[str, Any], property_name: str) -> str | None:
        prop = (page.get("properties") or {}).get(property_name)
        if prop is None:
            raise ValidationError(
                f"Notion page is missing relation property: {property_name}"
            )
        if prop.get("type") != "relation":
            raise ValidationError(f"Notion property is not a relation: {property_name}")
        relations = prop.get("relation") or []
        if len(relations) > 1:
            raise ValidationError(
                f"Notion menu parent must contain at most one relation: {property_name}"
            )
        if not relations:
            return None
        return normalize_notion_id(str(relations[0].get("id") or ""))

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
            raise ResourceNotFound(
                f"planning document not found: {planning_document_id}"
            )
        return PlanningDocumentRecord(
            planning_document_id=row["planning_document_id"],
            notion_database_id=row["notion_database_id"],
            root_notion_page_id=row["root_notion_page_id"],
            title=row["title"],
            source_status=row["source_status"],
            current_snapshot_id=row["current_snapshot_id"],
            notion_data_source_id=row["notion_data_source_id"],
            menu_parent_notion_page_id=row["menu_parent_notion_page_id"],
        )
