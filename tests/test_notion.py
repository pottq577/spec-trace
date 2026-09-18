from __future__ import annotations

import unittest

from spec_trace.notion import HttpResponse, NotionHttpClient, RateLimiter, normalize_notion_id


class NoWaitLimiter(RateLimiter):
    def __init__(self):
        pass

    def wait(self) -> None:
        return None


class NotionClientTest(unittest.TestCase):
    def test_normalize_notion_id_accepts_uuid_format(self) -> None:
        value = "12345678-1234-1234-1234-1234567890ab"
        self.assertEqual(normalize_notion_id(value), "123456781234123412341234567890ab")

    def test_retry_after_is_used_for_retryable_status(self) -> None:
        responses = [
            HttpResponse(429, {"Retry-After": "2"}, b'{"message":"slow down"}'),
            HttpResponse(200, {}, b'{"object":"page"}'),
        ]
        sleeps: list[float] = []

        def transport(request, timeout):
            return responses.pop(0)

        client = NotionHttpClient(
            "token",
            limiter=NoWaitLimiter(),
            sleeper=sleeps.append,
            transport=transport,
        )
        result = client.retrieve_page("123456781234123412341234567890ab")
        self.assertEqual(result["object"], "page")
        self.assertEqual(sleeps, [2.0])
