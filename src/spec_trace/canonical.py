from __future__ import annotations

import unicodedata
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .notion import normalize_notion_id
from .util import canonical_json_bytes, sha256_bytes

_VOLATILE_KEYS = {
    "id",
    "created_time",
    "last_edited_time",
    "created_by",
    "last_edited_by",
    "archived",
    "in_trash",
    "request_id",
}
_PRESENTATION_KEYS = {"color"}


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def extract_page_title(page: dict[str, Any]) -> str:
    properties = page.get("properties") or {}
    for prop in properties.values():
        if prop.get("type") != "title":
            continue
        parts = prop.get("title") or []
        title = "".join(_rich_text_plain_text(part) for part in parts)
        return normalize_text(title).strip()
    child_page = page.get("child_page")
    if isinstance(child_page, dict):
        return normalize_text(str(child_page.get("title") or "")).strip()
    return ""


def canonical_page(title: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "title": normalize_text(title),
        "blocks": [canonical_block(block) for block in blocks],
    }


def canonical_block(block: dict[str, Any]) -> dict[str, Any]:
    block_type = str(block.get("type") or "unknown")
    result: dict[str, Any] = {"type": block_type}
    if block_type == "child_page":
        result["notion_page_id"] = normalize_notion_id(str(block["id"]))
        result["title"] = normalize_text(str((block.get("child_page") or {}).get("title") or ""))
    else:
        payload = block.get(block_type)
        if payload is not None:
            result["value"] = _semantic_value(payload)
    children = block.get("_captured_children")
    if children:
        result["children"] = [canonical_block(child) for child in children]
    return result


def page_content_hash(canonical: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(canonical))


def _semantic_value(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, list):
        return [_semantic_value(item) for item in value]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        value_type = value.get("type")
        for key in sorted(value):
            if key in _VOLATILE_KEYS or key in _PRESENTATION_KEYS:
                continue
            if value_type == "file" and key == "file" and isinstance(value[key], dict):
                result[key] = _semantic_notion_file(value[key])
                continue
            result[key] = _semantic_value(value[key])
        return result
    return value


def _semantic_notion_file(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in sorted(value):
        if key == "expiry_time":
            continue
        if key == "url" and isinstance(value[key], str):
            result[key] = _stable_notion_file_url(value[key])
            continue
        result[key] = _semantic_value(value[key])
    return result


def _stable_notion_file_url(value: str) -> str:
    normalized = normalize_text(value)
    parsed = urlsplit(normalized)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return normalized


def _rich_text_plain_text(value: dict[str, Any]) -> str:
    plain = value.get("plain_text")
    if plain is not None:
        return str(plain)
    text = value.get("text") or {}
    return str(text.get("content") or "")
