"""资料检索工具的离线测试：用假检索器代替真实模型和索引。python -m unittest tests.test_search_tool -v"""

import json
import tempfile
import unittest
from unittest.mock import patch

import tools
from tools import run_tool


class FakeSearcher:
    """假检索器：记下收到的 query 和 k，返回固定的片段。"""

    def __init__(self):
        self.calls = []

    def search(self, query, k):
        self.calls.append((query, k))
        chunk = {"chunk_id": "d.pdf#p22#3", "file": "d.pdf", "page": 22, "label": "14",
                 "section": "3.3.3 Pretraining", "text": "Pretraining runs for 50 epochs.", "score": 0.83}
        return [chunk] * k


class SearchToolTest(unittest.TestCase):
    def setUp(self):
        self.saved = tools._searcher
        self.fake = FakeSearcher()
        tools._searcher = self.fake

    def tearDown(self):
        tools._searcher = self.saved

    def test_returns_text_with_sources(self):
        r = run_tool("search_documents", '{"query": "pretraining epochs"}')
        self.assertTrue(r["ok"])
        self.assertEqual(self.fake.calls, [("pretraining epochs", 3)])      # 默认 k = 3
        first = r["results"][0]
        self.assertEqual((first["file"], first["page"], first["section"]), ("d.pdf", 22, "3.3.3 Pretraining"))
        self.assertIn("50 epochs", first["text"])
        json.dumps(r, ensure_ascii=False)                                  # agent 要把结果转成 JSON 发给模型

    def test_k_within_limits(self):
        self.assertEqual(len(run_tool("search_documents", '{"query": "x", "k": 5}')["results"]), 5)
        for bad in ('0', '6', '"3"', 'true', '2.5'):
            r = run_tool("search_documents", '{"query": "x", "k": %s}' % bad)
            self.assertFalse(r["ok"], bad)

    def test_bad_query_rejected(self):
        for bad in ('""', '"   "', '123', 'null', json.dumps("x" * 501)):
            r = run_tool("search_documents", '{"query": %s}' % bad)
            self.assertFalse(r["ok"], bad)
        self.assertEqual(self.fake.calls, [])                              # 不合法的参数不会进到检索器

    def test_missing_index_explains_how_to_build(self):
        tools._searcher = None
        with tempfile.TemporaryDirectory() as empty, patch.object(tools.config, "INDEX_DIR", empty):
            r = run_tool("search_documents", '{"query": "x"}')
        self.assertFalse(r["ok"])
        self.assertIn("build_index", r["error"])


if __name__ == "__main__":
    unittest.main()
