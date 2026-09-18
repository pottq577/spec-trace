from __future__ import annotations

import unittest

from spec_trace.canonical import canonical_block, page_content_hash


class CanonicalNotionFileTest(unittest.TestCase):
    def test_notion_file_signed_url_rotation_does_not_change_hash(self) -> None:
        before = self._image_block(
            "https://prod-files-secure.s3.us-west-2.amazonaws.com/workspace/file/image.png?X-Amz-Date=20260918T050020Z&X-Amz-Signature=first",
            "2026-09-18T06:00:20.748Z",
        )
        after = self._image_block(
            "https://prod-files-secure.s3.us-west-2.amazonaws.com/workspace/file/image.png?X-Amz-Date=20260918T050423Z&X-Amz-Signature=second",
            "2026-09-18T06:04:23.181Z",
        )

        canonical_before = canonical_block(before)
        canonical_after = canonical_block(after)

        self.assertEqual(canonical_before, canonical_after)
        self.assertEqual(
            canonical_before["value"]["file"],
            {
                "url": "https://prod-files-secure.s3.us-west-2.amazonaws.com/workspace/file/image.png"
            },
        )
        self.assertEqual(
            page_content_hash({"blocks": [canonical_before]}),
            page_content_hash({"blocks": [canonical_after]}),
        )

    def test_notion_file_replacement_changes_hash(self) -> None:
        before = canonical_block(
            self._image_block(
                "https://prod-files-secure.s3.us-west-2.amazonaws.com/workspace/file-a/image.png?X-Amz-Signature=first",
                "2026-09-18T06:00:20.748Z",
            )
        )
        after = canonical_block(
            self._image_block(
                "https://prod-files-secure.s3.us-west-2.amazonaws.com/workspace/file-b/image.png?X-Amz-Signature=second",
                "2026-09-18T06:04:23.181Z",
            )
        )

        self.assertNotEqual(before, after)

    def test_external_url_query_remains_semantic(self) -> None:
        before = canonical_block(
            {
                "type": "image",
                "image": {
                    "caption": [],
                    "type": "external",
                    "external": {"url": "https://example.com/image.png?v=1"},
                },
            }
        )
        after = canonical_block(
            {
                "type": "image",
                "image": {
                    "caption": [],
                    "type": "external",
                    "external": {"url": "https://example.com/image.png?v=2"},
                },
            }
        )

        self.assertNotEqual(before, after)

    @staticmethod
    def _image_block(url: str, expiry_time: str) -> dict:
        return {
            "type": "image",
            "image": {
                "caption": [],
                "type": "file",
                "file": {
                    "url": url,
                    "expiry_time": expiry_time,
                },
            },
        }


if __name__ == "__main__":
    unittest.main()
