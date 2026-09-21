from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from .errors import ExternalServiceError, ResourceNotFound, ValidationError

DEFAULT_NOTION_VERSION = "2026-03-11"
RETRYABLE_STATUS_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "529",
    "rate_limited",
    "internal_server_error",
    "service_unavailable",
)
NOT_FOUND_MARKERS = ("404", "object_not_found", "could not find", "not found")


def normalize_notion_id(value: str) -> str:
    normalized = value.replace("-", "").strip().lower()
    if len(normalized) != 32 or any(c not in "0123456789abcdef" for c in normalized):
        raise ValidationError(f"invalid Notion id: {value}")
    return normalized


class NotionPort(Protocol):
    def retrieve_page(self, page_id: str) -> dict[str, Any]: ...

    def retrieve_database(self, database_id: str) -> dict[str, Any]: ...

    def query_data_source(self, data_source_id: str) -> list[dict[str, Any]]: ...

    def create_child_page(self, parent_page_id: str, title: str) -> dict[str, Any]: ...

    def update_page_title(self, page_id: str, title: str) -> dict[str, Any]: ...

    def delete_block(self, block_id: str) -> dict[str, Any]: ...

    def append_block_children(
        self, block_id: str, children: list[dict[str, Any]]
    ) -> list[dict[str, Any]]: ...

    def update_block(
        self, block_id: str, block_type: str, value: dict[str, Any]
    ) -> dict[str, Any]: ...

    def list_block_children(self, block_id: str) -> list[dict[str, Any]]: ...


class RateLimiter:
    def __init__(
        self,
        rate_per_second: float = 2.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self.interval = 1.0 / rate_per_second
        self.clock = clock
        self.sleeper = sleeper
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        with self._lock:
            now = self.clock()
            delay = max(0.0, self._next_allowed - now)
            if delay:
                self.sleeper(delay)
                now = self.clock()
            self._next_allowed = max(now, self._next_allowed) + self.interval


class NotionCliClient:
    def __init__(
        self,
        binary: str = "ntn",
        *,
        notion_version: str = DEFAULT_NOTION_VERSION,
        timeout_seconds: float = 30.0,
        max_attempts: int = 6,
        limiter: RateLimiter | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        random_source: Callable[[], float] = random.random,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ):
        if not binary:
            raise ValidationError("ntn binary is required")
        self.binary = binary
        self.notion_version = notion_version
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.limiter = limiter or RateLimiter(2.0)
        self.sleeper = sleeper
        self.random_source = random_source
        self.runner = runner

    @classmethod
    def from_environment(cls) -> NotionCliClient:
        binary = os.environ.get("NTN_BIN", "ntn")
        if shutil.which(binary) is None:
            raise ValidationError(
                "ntn command not found; install Notion CLI and run `ntn login`"
            )
        version = (
            os.environ.get("NOTION_API_VERSION")
            or os.environ.get("NOTION_VERSION")
            or DEFAULT_NOTION_VERSION
        )
        return cls(binary, notion_version=version)

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"v1/pages/{normalize_notion_id(page_id)}")

    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return self._request_json(
            "GET", f"v1/databases/{normalize_notion_id(database_id)}"
        )

    def query_data_source(self, data_source_id: str) -> list[dict[str, Any]]:
        normalized = normalize_notion_id(data_source_id)
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            payload = self._request_json(
                "POST",
                f"v1/data_sources/{normalized}/query",
                body,
                retryable=True,
            )
            results.extend(payload.get("results", []))
            if not payload.get("has_more"):
                return results
            cursor = payload.get("next_cursor")
            if not cursor:
                raise ExternalServiceError(
                    "Notion pagination returned has_more without next_cursor"
                )

    def create_child_page(self, parent_page_id: str, title: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "v1/pages",
            {
                "parent": {
                    "type": "page_id",
                    "page_id": normalize_notion_id(parent_page_id),
                },
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"type": "text", "text": {"content": title}}],
                    }
                },
            },
        )

    def update_page_title(self, page_id: str, title: str) -> dict[str, Any]:
        return self._request_json(
            "PATCH",
            f"v1/pages/{normalize_notion_id(page_id)}",
            {
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"type": "text", "text": {"content": title}}],
                    }
                }
            },
        )

    def delete_block(self, block_id: str) -> dict[str, Any]:
        return self._request_json(
            "DELETE", f"v1/blocks/{normalize_notion_id(block_id)}"
        )

    def append_block_children(
        self, block_id: str, children: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        payload = self._request_json(
            "PATCH",
            f"v1/blocks/{normalize_notion_id(block_id)}/children",
            {"children": children},
        )
        return list(payload.get("results") or [])

    def update_block(
        self, block_id: str, block_type: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request_json(
            "PATCH",
            f"v1/blocks/{normalize_notion_id(block_id)}",
            {block_type: value},
        )

    def list_block_children(self, block_id: str) -> list[dict[str, Any]]:
        normalized = normalize_notion_id(block_id)
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            query = ["page_size==100"]
            if cursor:
                query.append(f"start_cursor=={cursor}")
            payload = self._request_json(
                "GET", f"v1/blocks/{normalized}/children", query=query
            )
            results.extend(payload.get("results", []))
            if not payload.get("has_more"):
                return results
            cursor = payload.get("next_cursor")
            if not cursor:
                raise ExternalServiceError(
                    "Notion pagination returned has_more without next_cursor"
                )

    def _request_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        query: list[str] | None = None,
        retryable: bool | None = None,
    ) -> dict[str, Any]:
        command = [
            self.binary,
            "api",
            path,
        ]
        if method != "GET":
            command.extend(["-X", method])
        command.extend(query or [])
        if body is not None:
            command.extend(["--data", json.dumps(body, ensure_ascii=False)])

        last_error: ExternalServiceError | None = None
        for attempt in range(1, self.max_attempts + 1):
            self.limiter.wait()
            completed = self._run(command)
            if completed.returncode == 0:
                try:
                    return json.loads(completed.stdout)
                except json.JSONDecodeError as exc:
                    raise ExternalServiceError("ntn returned invalid JSON") from exc

            message = self._error_message(completed)
            lowered = message.lower()
            if any(marker in lowered for marker in NOT_FOUND_MARKERS):
                raise ResourceNotFound(f"Notion resource not found: {path}")

            last_error = ExternalServiceError(
                f"ntn request failed with exit {completed.returncode}: {message}"
            )
            retry_enabled = method == "GET" if retryable is None else retryable
            should_retry = retry_enabled and any(
                marker in lowered for marker in RETRYABLE_STATUS_MARKERS
            )
            if not should_retry or attempt == self.max_attempts:
                raise last_error
            self.sleeper(self._retry_delay(attempt))
        raise last_error or ExternalServiceError("ntn request failed")

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env={**os.environ, "NOTION_API_VERSION": self.notion_version},
            )
        except FileNotFoundError as exc:
            raise ValidationError(
                "ntn command not found; install Notion CLI and run `ntn login`"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ExternalServiceError(
                f"ntn command timed out after {self.timeout_seconds}s"
            ) from exc
        except OSError as exc:
            raise ExternalServiceError(f"ntn command failed to start: {exc}") from exc

    def _retry_delay(self, attempt: int) -> float:
        base = min(30.0, float(2 ** (attempt - 1)))
        return base + self.random_source() * 0.25

    @staticmethod
    def _error_message(completed: subprocess.CompletedProcess[str]) -> str:
        message = (completed.stderr or completed.stdout or "unknown error").strip()
        return message[:1000]
