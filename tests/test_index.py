import tempfile
import unittest
from pathlib import Path

import numpy as np
import pymupdf

import build_index
import retrieval


class FakeEmbedder:
    """测试用：不加载真实模型，每段文字固定返回 [长度, 1]，并记下收到了什么。"""
    model_name = "fake-model"

    def __init__(self):
        self.received = []

    def embed_passages(self, texts):
        self.received.extend(texts)
        return np.array([[len(t), 1.0] for t in texts], dtype=np.float32)


def make_pdf(path, text):
    doc = pymupdf.Document()
    doc.new_page().insert_text((72, 100), text, fontsize=11)
    doc.save(path)
    doc.close()


class SaveLoadTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "index"

    def tearDown(self):
        self._tmp.cleanup()

    def test_round_trip(self):
        chunks = [{"chunk_id": "a.pdf#p1#0", "text": "中文 ok"}, {"chunk_id": "a.pdf#p1#1", "text": "b"}]
        vectors = np.array([[0.6, 0.8], [1.0, 0.0]], dtype=np.float32)
        retrieval.save_index(self.dir, chunks, vectors, {"model": "m"})
        got_chunks, got_vectors, manifest = retrieval.load_index(self.dir)
        self.assertEqual(got_chunks, chunks)
        np.testing.assert_array_equal(got_vectors, vectors)
        self.assertEqual((manifest["model"], manifest["chunks"], manifest["dim"]), ("m", 2, 2))

    def test_count_mismatch_refused(self):
        with self.assertRaises(ValueError):
            retrieval.save_index(self.dir, [{"text": "a"}], np.zeros((2, 2), dtype=np.float32), {})

    def test_missing_index_explains_how_to_build(self):
        with self.assertRaisesRegex(FileNotFoundError, "build_index"):
            retrieval.load_index(self.dir)


class BuildTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_chunks_from_all_good_files_skipping_bad_ones(self):
        make_pdf(self.dir / "a.pdf", "Alpha one. Beta two.")
        make_pdf(self.dir / "b.pdf", "Gamma three.")
        (self.dir / "bad.pdf").write_bytes(b"not a pdf")
        embedder = FakeEmbedder()
        chunks, vectors, skipped = build_index.build(
            [self.dir / "a.pdf", self.dir / "bad.pdf", self.dir / "b.pdf"], embedder, 500, 100)
        self.assertEqual([c["chunk_id"] for c in chunks], ["a.pdf#p1#0", "b.pdf#p1#0"])
        self.assertEqual(embedder.received, ["Alpha one. Beta two.", "Gamma three."])
        self.assertEqual(vectors.shape, (2, 2))
        self.assertEqual([name for name, _ in skipped], ["bad.pdf"])
