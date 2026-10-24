import tempfile
import unittest
from pathlib import Path

import numpy as np
import pymupdf

import build_index
import documents


def chunk(section, text="t"):
    return {"chunk_id": "x", "file": "a.pdf", "page": 1, "label": None, "section": section, "text": text}


class DropSectionsTest(unittest.TestCase):
    def test_listed_sections_are_dropped(self):
        chunks = [chunk("Table of Contents"), chunk("1 Introduction"), chunk("References")]
        kept = documents.drop_sections(chunks, ["Table of Contents", "References"])
        self.assertEqual([c["section"] for c in kept], ["1 Introduction"])

    def test_match_ignores_case_and_spaces(self):
        kept = documents.drop_sections([chunk(" List of Tables "), chunk("Abstract")], ["list of tables"])
        self.assertEqual([c["section"] for c in kept], ["Abstract"])

    def test_whole_name_must_match(self):
        # 附录 G 的标题里也有 Declaration，但它是正文证据，不能被误删
        kept = documents.drop_sections([chunk("Appendix G: Declaration of AI assistance")], ["Declaration"])
        self.assertEqual(len(kept), 1)

    def test_chunk_without_section_is_kept(self):
        self.assertEqual(len(documents.drop_sections([chunk(None)], ["References"])), 1)

    def test_empty_list_keeps_everything(self):
        chunks = [chunk("References"), chunk("Abstract")]
        self.assertEqual(documents.drop_sections(chunks, ()), chunks)


class FakeEmbedder:
    model_name = "fake-model"

    def embed_passages(self, texts):
        return np.ones((len(texts), 2), dtype=np.float32)


class BuildSkipTest(unittest.TestCase):
    def test_build_drops_skipped_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.pdf"
            doc = pymupdf.Document()
            page = doc.new_page()
            page.insert_text((72, 80), "Abstract", fontsize=16)
            page.insert_text((72, 110), "We study clustering stability in detail here.", fontsize=11)
            page = doc.new_page()
            page.insert_text((72, 80), "References", fontsize=16)
            page.insert_text((72, 110), "Smith, J. (2020) A paper about clustering methods.", fontsize=11)
            doc.save(path)
            doc.close()
            chunks, vectors, _ = build_index.build([path], FakeEmbedder(), 500, 100, skip_sections=("References",))
        self.assertEqual([c["section"] for c in chunks], ["Abstract"])
        self.assertEqual(len(vectors), 1)
