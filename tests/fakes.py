from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from spec_trace.errors import ResourceNotFound
from spec_trace.notion import normalize_notion_id


def notion_id(seed: int) -> str:
    return f"{seed:032x}"


def page(page_id: str, title: str, edited: str = "2026-09-18T00:00:00.000Z", data_source_id: str | None = None) -> dict[str, Any]:
    return {
        "object": "page",
        "id": page_id,
        "last_edited_time": edited,
        "archived": False,
        "in_trash": False,
        "parent": {"type": "data_source_id", "data_source_id": data_source_id} if data_source_id else {"type": "page_id", "page_id": notion_id(9999)},
        "properties": {
            "Name": {
                "id": "title",
                "type": "title",
                "title": [{"type": "text", "plain_text": title, "text": {"content": title}}],
            }
        },
    }


def paragraph(block_id: str, text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "id": block_id,
        "type": "paragraph",
        "has_children": False,
        "paragraph": {
            "rich_text": [{"type": "text", "plain_text": text, "text": {"content": text}}],
            "color": "default",
        },
    }


def child_page(block_id: str, title: str) -> dict[str, Any]:
    return {
        "object": "block",
        "id": block_id,
        "type": "child_page",
        "has_children": True,
        "child_page": {"title": title},
    }


class FakeNotion:
    def __init__(self, pages: dict[str, dict[str, Any]], children: dict[str, list[dict[str, Any]]]):
        self.pages = {normalize_notion_id(k): deepcopy(v) for k, v in pages.items()}
        self.children = {normalize_notion_id(k): deepcopy(v) for k, v in children.items()}
        self.root_page_id: str | None = None
        self.root_retrieve_count = 0
        self.on_second_capture: Callable[["FakeNotion"], None] | None = None
        self.databases: dict[str, dict[str, Any]] = {}

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        page_id = normalize_notion_id(page_id)
        if self.root_page_id == page_id:
            self.root_retrieve_count += 1
            if self.root_retrieve_count == 2 and self.on_second_capture:
                callback = self.on_second_capture
                self.on_second_capture = None
                callback(self)
        if page_id not in self.pages:
            raise ResourceNotFound(page_id)
        return deepcopy(self.pages[page_id])

    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        database_id = normalize_notion_id(database_id)
        if database_id not in self.databases:
            raise ResourceNotFound(database_id)
        return deepcopy(self.databases[database_id])

    def list_block_children(self, block_id: str) -> list[dict[str, Any]]:
        return deepcopy(self.children.get(normalize_notion_id(block_id), []))
