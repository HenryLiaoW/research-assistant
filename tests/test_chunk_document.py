import unittest
import documents

def line(text, heading=False):
    return {"text": text, "size": 11.0, "bold": False, "heading": heading}

PARSED = {"ok": True, "file": "x.pdf", "body_size": 11.0, "pages": [
    {"page": 1, "label": "1", "lines": [line("1 Intro", heading=True), line("Alpha one. Beta two.")]},
    {"page": 2, "label": "2", "lines": [line("Gamma three."), line("2 Methods", heading=True), line("Delta four.")]},
    {"page": 3, "label": None, "lines": []},
]}

class ChunkDocumentTest(unittest.TestCase):
    def setUp(self):
        self.chunks = documents.chunk_document(PARSED, 100, 30)

    def test_chunks_carry_source_information(self):
        self.assertEqual(self.chunks[0], {"chunk_id": "x.pdf#p1#0", "file": "x.pdf", "page": 1, "label": "1",
                                          "section": "1 Intro", "text": "1 Intro Alpha one. Beta two."})

    def test_section_carries_over_to_next_page(self):
        self.assertEqual((self.chunks[1]["page"], self.chunks[1]["section"], self.chunks[1]["text"]),
                         (2, "1 Intro", "Gamma three."))

    def test_new_heading_starts_new_chunk_with_its_section(self):
        self.assertEqual((self.chunks[2]["chunk_id"], self.chunks[2]["section"], self.chunks[2]["text"]),
                         ("x.pdf#p2#1", "2 Methods", "2 Methods Delta four."))

    def test_page_without_lines_gives_no_chunks(self):
        self.assertEqual(len(self.chunks), 3)
        self.assertNotIn(3, [c["page"] for c in self.chunks])
