from __future__ import annotations

import unittest

from spec_trace.references import extract_block_references

from fakes import notion_id


class ReferenceExtractionTest(unittest.TestCase):
    def test_extracts_page_mention_and_link(self) -> None:
        target = notion_id(77)
        blocks = [
            {
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "mention", "mention": {"type": "page", "page": {"id": target}}},
                        {"type": "text", "href": f"https://www.notion.so/Policy-{target}"},
                    ]
                },
            }
        ]
        refs = extract_block_references(blocks)
        self.assertEqual({item["reference_type"] for item in refs}, {"MENTION", "LINK"})
        self.assertEqual({item["target_notion_page_id"] for item in refs}, {target})
