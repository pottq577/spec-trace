from __future__ import annotations

import json
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Any, Callable, Protocol

from .errors import ExternalServiceError, ResourceNotFound, ValidationError

DEFAULT_NOTION_VERSION = "2026-03-11"
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504, 529}


def normalize_notion_id(value: str) -> str:
    normalized = value.replace("-", "").strip().lower()
    if len(normalized) != 32 or any(c not in "0123456789abcdef" for c in normalized):
        raise ValidationError(f"invalid Notion id: {value}")
    return normalized


class NotionPort(Protocol):
    def retrieve_page(self, page_id: str) -> dict[str, Any]: ...

    def retrieve_database(self, database_id: str) -> dict[str, Any]: ...

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


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Message | dict[str, str]
    body: bytes


Transport = Callable[[urllib.request.Request, float], HttpResponse]


class NotionHttpClient:
    def __init__(
        self,
        token: str,
        *,
        notion_version: str = DEFAULT_NOTION_VERSION,
        timeout_seconds: float = 20.0,
        max_attempts: int = 6,
        limiter: RateLimiter | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        random_source: Callable[[], float] = random.random,
        transport: Transport | None = None,
    ):
        if not token:
            raise ValidationError("NOTION_TOKEN is required")
        self.token = token
        self.notion_version = notion_version
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.limiter = limiter or RateLimiter(2.0)
        self.sleeper = sleeper
        self.random_source = random_source
        self.transport = transport or self._urlopen_transport

    @classmethod
    def from_environment(cls) -> "NotionHttpClient":
        token = os.environ.get("NOTION_TOKEN", "")
        version = os.environ.get("NOTION_VERSION", DEFAULT_NOTION_VERSION)
        return cls(token, notion_version=version)

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/v1/pages/{normalize_notion_id(page_id)}")

    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/v1/databases/{normalize_notion_id(database_id)}")

    def list_block_children(self, block_id: str) -> list[dict[str, Any]]:
        normalized = normalize_notion_id(block_id)
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            path = f"/v1/blocks/{normalized}/children?page_size=100"
            if cursor:
                path += "&start_cursor=" + urllib.parse.quote(cursor)
            payload = self._request_json("GET", path)
            results.extend(payload.get("results", []))
            if not payload.get("has_more"):
                return results
            cursor = payload.get("next_cursor")
            if not cursor:
                raise ExternalServiceError("Notion pagination returned has_more without next_cursor")

    def _request_json(self, method: str, path: str) -> dict[str, Any]:
        url = "https://api.notion.com" + path
        request = urllib.request.Request(
            url,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": self.notion_version,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        last_error: ExternalServiceError | None = None
        for attempt in range(1, self.max_attempts + 1):
            self.limiter.wait()
            response = self.transport(request, self.timeout_seconds)
            if 200 <= response.status < 300:
                try:
                    return json.loads(response.body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ExternalServiceError("Notion returned invalid JSON") from exc
            if response.status == 404:
                raise ResourceNotFound(f"Notion resource not found: {path}")
            message = self._error_message(response)
            last_error = ExternalServiceError(
                f"Notion request failed with HTTP {response.status}: {message}"
            )
            if response.status not in RETRYABLE_HTTP_STATUSES or attempt == self.max_attempts:
                raise last_error
            self.sleeper(self._retry_delay(response, attempt))
        raise last_error or ExternalServiceError("Notion request failed")

    def _retry_delay(self, response: HttpResponse, attempt: int) -> float:
        retry_after = self._header(response.headers, "Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        base = min(30.0, float(2 ** (attempt - 1)))
        return base + self.random_source() * 0.25

    @staticmethod
    def _header(headers: Message | dict[str, str], name: str) -> str | None:
        if isinstance(headers, Message):
            return headers.get(name)
        for key, value in headers.items():
            if key.lower() == name.lower():
                return value
        return None

    @staticmethod
    def _error_message(response: HttpResponse) -> str:
        try:
            payload = json.loads(response.body.decode("utf-8"))
            return str(payload.get("message") or payload.get("code") or "unknown error")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return response.body.decode("utf-8", errors="replace")[:500]

    @staticmethod
    def _urlopen_transport(request: urllib.request.Request, timeout: float) -> HttpResponse:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResponse(
                    status=response.status,
                    headers=response.headers,
                    body=response.read(),
                )
        except urllib.error.HTTPError as exc:
            return HttpResponse(status=exc.code, headers=exc.headers, body=exc.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ExternalServiceError(f"Notion transport failed: {exc}") from exc
