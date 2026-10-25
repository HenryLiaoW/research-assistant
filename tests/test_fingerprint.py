"""索引指纹的离线测试：资料变了，旧索引要判为过期。python -m unittest tests.test_fingerprint -v"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import documents
import retrieval
import tools


class StaleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.docs = Path(tmp.name) / "docs"
        self.docs.mkdir()
        (self.docs / "a.pdf").write_bytes(b"version 1")
        (self.docs / "b.pdf").write_bytes(b"other")
        self.manifest = {"sources": retrieval.source_fingerprints(documents.pdf_files(self.docs))}   # "建索引时"

    def reasons(self):
        return retrieval.stale_reasons(self.manifest, documents.pdf_files(self.docs))

    def test_unchanged_docs_are_fresh(self):
        self.assertEqual(self.reasons(), [])

    def test_changed_content(self):
        (self.docs / "a.pdf").write_bytes(b"version 2")                # 同名文件换了内容
        self.assertEqual(self.reasons(), ["a.pdf 的内容变了"])

    def test_added_and_removed(self):
        (self.docs / "c.pdf").write_bytes(b"new")
        (self.docs / "b.pdf").unlink()
        self.assertEqual(self.reasons(), ["新增了 c.pdf", "删除了 b.pdf"])

    def test_index_without_fingerprints_is_stale(self):
        self.manifest = {"model": "intfloat/multilingual-e5-large"}    # 加入指纹之前建的索引
        self.assertEqual(len(self.reasons()), 1)

    def test_non_pdf_files_are_ignored(self):
        (self.docs / "notes.txt").write_text("不是资料", encoding="utf-8")
        self.assertEqual(self.reasons(), [])


class SearchToolTest(unittest.TestCase):
    def test_stale_index_is_refused_before_loading_model(self):
        with tempfile.TemporaryDirectory() as d:
            index, docs = Path(d) / "index", Path(d) / "docs"
            index.mkdir()
            docs.mkdir()
            (docs / "a.pdf").write_bytes(b"version 2")
            (index / "manifest.json").write_text(json.dumps({"sources": {"a.pdf": "old"}}), encoding="utf-8")
            with patch.object(tools, "_searcher", None), \
                    patch.object(tools.config, "INDEX_DIR", str(index)), \
                    patch.object(tools.config, "DOCS_DIR", str(docs)), \
                    patch("tools.retrieval.Embedder") as embedder:
                result = tools.search_documents("pretraining epochs")
        self.assertFalse(result["ok"])
        self.assertIn("a.pdf 的内容变了", result["error"])
        embedder.assert_not_called()                                    # 过期就不加载嵌入模型（约 10 秒）


if __name__ == "__main__":
    unittest.main()
