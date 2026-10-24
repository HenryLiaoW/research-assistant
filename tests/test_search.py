import tempfile
import unittest
from pathlib import Path

import numpy as np

import retrieval


class FakeQueryEmbedder:
    """测试用：不加载真实模型，问题直接对应到事先定好的向量。"""
    model_name = "fake-model"

    def __init__(self, table):
        self.table = table

    def embed_query(self, text):
        return np.array(self.table[text], dtype=np.float32)


CHUNKS = [
    {"chunk_id": "a.pdf#p1#0", "file": "a.pdf", "page": 1, "label": "1", "section": "1 Intro", "text": "alpha"},
    {"chunk_id": "a.pdf#p2#0", "file": "a.pdf", "page": 2, "label": "2", "section": "2 Methods", "text": "beta"},
    {"chunk_id": "a.pdf#p3#0", "file": "a.pdf", "page": 3, "label": "3", "section": "3 Results", "text": "gamma"},
]
VECTORS = np.array([[1.0, 0.0], [0.0, 1.0], [0.6, 0.8]], dtype=np.float32)   # 都已归一化


class DocumentSearchTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "index"
        retrieval.save_index(self.dir, CHUNKS, VECTORS, {"model": "fake-model"})
        self.searcher = retrieval.DocumentSearch(self.dir, FakeQueryEmbedder({"q": [1.0, 0.0]}))

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_top_k_from_most_similar(self):
        # 和 [1, 0] 的相似度：alpha 1.0，beta 0.0，gamma 0.6 → 从高到低取前 2 个
        results = self.searcher.search("q", k=2)
        self.assertEqual([r["chunk_id"] for r in results], ["a.pdf#p1#0", "a.pdf#p3#0"])

    def test_results_carry_source_and_score(self):
        top = self.searcher.search("q", k=1)[0]
        self.assertEqual((top["page"], top["section"], top["text"]), (1, "1 Intro", "alpha"))
        self.assertAlmostEqual(top["score"], 1.0, places=4)

    def test_k_larger_than_index_returns_all(self):
        self.assertEqual(len(self.searcher.search("q", k=10)), 3)

    def test_search_does_not_change_stored_chunks(self):
        self.searcher.search("q", k=3)
        self.assertNotIn("score", self.searcher.chunks[0])

    def test_index_built_with_other_model_is_refused(self):
        other = Path(self._tmp.name) / "other"
        retrieval.save_index(other, CHUNKS, VECTORS, {"model": "other-model"})
        with self.assertRaisesRegex(ValueError, "build_index"):
            retrieval.DocumentSearch(other, FakeQueryEmbedder({}))
