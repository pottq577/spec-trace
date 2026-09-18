from __future__ import annotations

import json
import re
from typing import Any, Iterator

from .notion import normalize_notion_id

_NOTION_ID_PATTERN = re.compile(r"(?i)([0-9a-f]{32})(?:\?|$)")


def extract_block_references(blocks: list[dict[str, Any]]) -> list[dict[str, str]]:
    found: dict[tuple[str, str, str], dict[str, str]] = {}
    for path, value in _walk(blocks, ()):
        reference = _reference_from_value(value)
        if reference is None:
            continue
        target, reference_type = reference
        location = json.dumps(list(path), separators=(",", ":"))
        key = (target, reference_type, location)
        found[key] = {
            "target_notion_page_id": target,
            "reference_type": reference_type,
            "location_json": location,
        }
    return list(found.values())


def _walk(value: Any, path: tuple[str | int, ...]) -> Iterator[tuple[tuple[str | int, ...], Any]]:
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            if key == "_captured_children":
                continue
            yield from _walk(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, path + (index,))


def _reference_from_value(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    if value.get("type") == "mention":
        mention = value.get("mention") or {}
        if mention.get("type") == "page" and (mention.get("page") or {}).get("id"):
            return normalize_notion_id(str(mention["page"]["id"])), "MENTION"
    if value.get("type") == "link_to_page":
        payload = value.get("link_to_page") or {}
        if payload.get("type") == "page_id" and payload.get("page_id"):
            return normalize_notion_id(str(payload["page_id"])), "LINK_TO_PAGE"
    href = value.get("href")
    if isinstance(href, str):
        compact = href.replace("-", "")
        match = _NOTION_ID_PATTERN.search(compact)
        if match:
            return normalize_notion_id(match.group(1)), "LINK"
    return None
