from __future__ import annotations

import json
import os
import subprocess
import unittest
from unittest.mock import patch

from spec_trace.errors import ExternalServiceError, ResourceNotFound
from spec_trace.notion import NotionCliClient, RateLimiter, normalize_notion_id


class NoWaitLimiter(RateLimiter):
    def __init__(self):
        pass

    def wait(self) -> None:
        return None


class FakeRunner:
    def __init__(self, responses: list[subprocess.CompletedProcess[str]]):
        self.responses = responses
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        return self.responses.pop(0)


class NotionClientTest(unittest.TestCase):
    def test_normalize_notion_id_accepts_uuid_format(self) -> None:
        value = "12345678-1234-1234-1234-1234567890ab"
        self.assertEqual(
            normalize_notion_id(value), "123456781234123412341234567890ab"
        )

    def test_from_environment_uses_ntn_session_configuration(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"NTN_BIN": "ntn", "NOTION_VERSION": "2026-03-11"},
                clear=True,
            ),
            patch("spec_trace.notion.shutil.which", return_value="/usr/bin/ntn"),
        ):
            client = NotionCliClient.from_environment()

        self.assertEqual(client.binary, "ntn")
        self.assertEqual(client.notion_version, "2026-03-11")

    def test_retrieve_page_uses_ntn_api(self) -> None:
        runner = FakeRunner(
            [subprocess.CompletedProcess([], 0, '{"object":"page"}\n', "")]
        )
        client = NotionCliClient("ntn", limiter=NoWaitLimiter(), runner=runner)

        result = client.retrieve_page("12345678-1234-1234-1234-1234567890ab")

        self.assertEqual(result["object"], "page")
        self.assertEqual(
            runner.calls[0][0],
            [
                "ntn",
                "api",
                "v1/pages/123456781234123412341234567890ab",
                "--notion-version",
                "2026-03-11",
            ],
        )

    def test_block_children_consumes_ntn_pagination(self) -> None:
        runner = FakeRunner(
            [
                subprocess.CompletedProcess(
                    [],
                    0,
                    '{"results":[{"id":"a"}],"has_more":true,"next_cursor":"next"}',
                    "",
                ),
                subprocess.CompletedProcess(
                    [],
                    0,
                    '{"results":[{"id":"b"}],"has_more":false,"next_cursor":null}',
                    "",
                ),
            ]
        )
        client = NotionCliClient("ntn", limiter=NoWaitLimiter(), runner=runner)

        result = client.list_block_children(
            "12345678-1234-1234-1234-1234567890ab"
        )

        self.assertEqual([item["id"] for item in result], ["a", "b"])
        self.assertIn("page_size==100", runner.calls[0][0])
        self.assertIn("start_cursor==next", runner.calls[1][0])

    def test_data_source_query_consumes_pagination(self) -> None:
        runner = FakeRunner(
            [
                subprocess.CompletedProcess(
                    [],
                    0,
                    '{"results":[{"id":"a"}],"has_more":true,"next_cursor":"next"}',
                    "",
                ),
                subprocess.CompletedProcess(
                    [],
                    0,
                    '{"results":[{"id":"b"}],"has_more":false,"next_cursor":null}',
                    "",
                ),
            ]
        )
        client = NotionCliClient("ntn", limiter=NoWaitLimiter(), runner=runner)

        result = client.query_data_source(
            "12345678-1234-1234-1234-1234567890ab"
        )

        self.assertEqual([item["id"] for item in result], ["a", "b"])
        first_command = runner.calls[0][0]
        second_command = runner.calls[1][0]
        self.assertIn("-X", first_command)
        self.assertIn("POST", first_command)
        first_body = json.loads(first_command[first_command.index("--data") + 1])
        second_body = json.loads(second_command[second_command.index("--data") + 1])
        self.assertEqual(first_body, {"page_size": 100})
        self.assertEqual(
            second_body,
            {"page_size": 100, "start_cursor": "next"},
        )

    def test_retryable_get_uses_backoff(self) -> None:
        runner = FakeRunner(
            [
                subprocess.CompletedProcess([], 1, "", "HTTP 429 rate limited"),
                subprocess.CompletedProcess([], 0, '{"object":"page"}', ""),
            ]
        )
        sleeps: list[float] = []
        client = NotionCliClient(
            "ntn",
            limiter=NoWaitLimiter(),
            runner=runner,
            sleeper=sleeps.append,
            random_source=lambda: 0.0,
        )

        result = client.retrieve_page("123456781234123412341234567890ab")

        self.assertEqual(result["object"], "page")
        self.assertEqual(sleeps, [1.0])

    def test_write_failure_is_not_retried(self) -> None:
        runner = FakeRunner(
            [subprocess.CompletedProcess([], 1, "", "HTTP 503 unavailable")]
        )
        client = NotionCliClient("ntn", limiter=NoWaitLimiter(), runner=runner)

        with self.assertRaises(ExternalServiceError):
            client.create_child_page(
                "123456781234123412341234567890ab", "개발 검토"
            )

        self.assertEqual(len(runner.calls), 1)

    def test_404_is_resource_not_found(self) -> None:
        runner = FakeRunner(
            [subprocess.CompletedProcess([], 1, "", "HTTP 404 object_not_found")]
        )
        client = NotionCliClient("ntn", limiter=NoWaitLimiter(), runner=runner)

        with self.assertRaises(ResourceNotFound):
            client.retrieve_page("123456781234123412341234567890ab")


if __name__ == "__main__":
    unittest.main()
