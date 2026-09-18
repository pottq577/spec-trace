from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from spec_trace.errors import ExternalServiceError, ResourceNotFound
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


def menu_page(
    page_id: str,
    title: str,
    data_source_id: str,
    parent_page_id: str | None = None,
) -> dict[str, Any]:
    item = page(page_id, title, data_source_id=data_source_id)
    item["properties"]["상위 항목"] = {
        "id": "parent",
        "type": "relation",
        "relation": [{"id": parent_page_id}] if parent_page_id else [],
    }
    return item


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
        self._next_generated_id = 100000
        self.fail_next_write = False

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

    def query_data_source(self, data_source_id: str) -> list[dict[str, Any]]:
        data_source_id = normalize_notion_id(data_source_id)
        results = []
        for item in self.pages.values():
            parent = item.get("parent") or {}
            if parent.get("type") != "data_source_id":
                continue
            parent_id = normalize_notion_id(
                str(parent.get("data_source_id") or "")
            )
            if parent_id == data_source_id:
                results.append(deepcopy(item))
        return sorted(results, key=lambda value: str(value.get("id") or ""))

    def create_child_page(self, parent_page_id: str, title: str) -> dict[str, Any]:
        self._maybe_fail_write()
        page_id = notion_id(self._next_generated_id)
        self._next_generated_id += 1
        parent_page_id = normalize_notion_id(parent_page_id)
        created = page(page_id, title)
        created["parent"] = {"type": "page_id", "page_id": parent_page_id}
        self.pages[page_id] = created
        self.children[page_id] = []
        self.children.setdefault(parent_page_id, []).append(child_page(page_id, title))
        return deepcopy(created)

    def append_block_children(self, block_id: str, children: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._maybe_fail_write()
        block_id = normalize_notion_id(block_id)
        created: list[dict[str, Any]] = []
        for source in children:
            item = deepcopy(source)
            item_id = notion_id(self._next_generated_id)
            self._next_generated_id += 1
            item["id"] = item_id
            item.setdefault("object", "block")
            item.setdefault("has_children", False)
            if item.get("type") == "toggle":
                item["has_children"] = bool(item.get("_captured_children"))
                self.children[item_id] = []
            self.children.setdefault(block_id, []).append(item)
            created.append(item)
        return deepcopy(created)

    def update_block(self, block_id: str, block_type: str, value: dict[str, Any]) -> dict[str, Any]:
        self._maybe_fail_write()
        block_id = normalize_notion_id(block_id)
        for blocks in self.children.values():
            for item in blocks:
                if normalize_notion_id(str(item.get("id"))) == block_id:
                    item["type"] = block_type
                    item[block_type] = deepcopy(value)
                    return deepcopy(item)
        raise ResourceNotFound(block_id)

    def list_block_children(self, block_id: str) -> list[dict[str, Any]]:
        return deepcopy(self.children.get(normalize_notion_id(block_id), []))

    def _maybe_fail_write(self) -> None:
        if self.fail_next_write:
            self.fail_next_write = False
            raise ExternalServiceError("fake write failure")
