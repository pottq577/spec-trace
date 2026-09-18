import tempfile
import unittest
from pathlib import Path

from spec_trace.content_store import ContentStore


class ContentStoreTest(unittest.TestCase):
    def test_put_json_is_content_addressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ContentStore(Path(tmp) / "content")
            digest1, ref1 = store.put_json({"b": 2, "a": 1})
            digest2, ref2 = store.put_json({"a": 1, "b": 2})
            self.assertEqual(digest1, digest2)
            self.assertEqual(ref1, ref2)
            self.assertEqual(store.read_json(ref1), {"a": 1, "b": 2})
