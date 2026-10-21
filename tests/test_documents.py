"""文档解析（4a）的离线测试：测试用的 PDF 在临时目录里现场生成，不依赖你的论文。

python -m unittest tests.test_documents -v
"""

import tempfile
import unittest
from pathlib import Path

import pymupdf

import documents

BODY = "This is a line of ordinary body text used to set the body font size of the document."


def make_pdf(path, pages, footers=None):
    """生成测试 PDF。

    pages：每页是 [(文字, 字号) 或 (文字, 字号, 是否加粗), ...]，从页面上方往下一行一行排；
    footers：可选，每页页尾要印的文字（放在页面最下方，而且最后才画，抽取时会排在最后）。
    """
    doc = pymupdf.Document()
    for number, items in enumerate(pages):
        page = doc.new_page(width=595, height=842)
        y = 60
        for text, size, *bold in items:
            page.insert_text((60, y), text, fontsize=size, fontname="hebo" if bold and bold[0] else "helv")
            y += size * 2
        if footers and footers[number]:
            page.insert_text((290, 815), footers[number], fontsize=9)
    doc.save(path)
    doc.close()


class ParsePdfTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def parse(self, pages, name="t.pdf", footers=None):
        path = self.dir / name
        make_pdf(path, pages, footers)
        r = documents.parse_pdf(path)
        self.assertTrue(r["ok"], r)
        return r

    # ---- 页码与页首标签 ----
    def test_pages_numbered_from_one_and_label_removed(self):
        r = self.parse([[("7", 9), (BODY, 11)], [("8", 9), (BODY, 11)]])
        self.assertEqual([p["page"] for p in r["pages"]], [1, 2])           # PDF 第几页，从 1 开始
        self.assertEqual([p["label"] for p in r["pages"]], ["7", "8"])      # 印刷页码另外记下
        self.assertNotIn("7", [line["text"] for line in r["pages"][0]["lines"]])   # 不混进正文

    def test_label_found_by_position_at_bottom(self):
        # 页码印在页尾、而且最后才画（抽取时排在最后一行），也要认出来
        r = self.parse([[(BODY, 11), (BODY, 11)]], footers=["22"])
        self.assertEqual(r["pages"][0]["label"], "22")
        self.assertNotIn("22", [line["text"] for line in r["pages"][0]["lines"]])

    def test_number_in_middle_of_page_is_not_label(self):
        # 正文中间单独一行的数字（比如表格里的 "10"）不在页边，不能当成页码
        r = self.parse([[(BODY, 11), ("10", 11), (BODY, 11)]])
        self.assertIsNone(r["pages"][0]["label"])
        self.assertIn("10", [line["text"] for line in r["pages"][0]["lines"]])

    def test_roman_label(self):
        r = self.parse([[("iv", 9), (BODY, 11)]])
        self.assertEqual(r["pages"][0]["label"], "iv")

    def test_page_without_text_has_no_lines(self):
        # 整页图片的页：只剩一个页码，去掉页码后没有文字
        r = self.parse([[("5", 9)], [("6", 9), (BODY, 11)]])
        self.assertEqual(r["pages"][0]["lines"], [])
        self.assertIn(1, documents.summarize(r)["empty_pages"])

    # ---- 标题识别 ----
    def test_body_size_is_most_common_by_chars(self):
        r = self.parse([[("Short", 9), (BODY, 11), (BODY, 11), ("2 Methods", 14)]])
        self.assertEqual(r["body_size"], 11.0)

    def test_headings_detected_by_size(self):
        r = self.parse([[(BODY, 11), ("2 Methods", 14), (BODY, 11)]])
        flags = {line["text"]: line["heading"] for line in r["pages"][0]["lines"]}
        self.assertTrue(flags["2 Methods"])
        self.assertFalse(flags[BODY])

    def test_slightly_larger_text_is_not_heading(self):
        r = self.parse([[(BODY, 11), ("Caption in 12 pt", 12), (BODY, 11)]])
        caption = [line for line in r["pages"][0]["lines"] if line["text"] == "Caption in 12 pt"][0]
        self.assertFalse(caption["heading"])          # 只大 1 号，不到 HEADING_SIZE_DELTA

    def test_section_number_merged_with_title(self):
        # 论文里 "3.2" 和 "Data and cohort" 是两行，合成一个标题
        r = self.parse([[(BODY, 11), ("3.2", 13), ("Data and cohort", 13), (BODY, 11)]])
        texts = [line["text"] for line in r["pages"][0]["lines"]]
        self.assertIn("3.2 Data and cohort", texts)
        self.assertNotIn("3.2", texts)

    def test_space_drawn_in_other_font_is_kept(self):
        # 论文里编号和标题之间的空格用另一种字体单独画；拼文字时丢掉它就会粘成 "2.2.1Clinical"
        path = self.dir / "spaces.pdf"
        doc = pymupdf.Document()
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 100), BODY, fontsize=11)
        page.insert_text((60, 130), "2.2.1", fontsize=12, fontname="helv")
        page.insert_text((88, 130), " ", fontsize=12, fontname="cour")
        page.insert_text((96, 130), "Clinical heterogeneity", fontsize=12, fontname="helv")
        page.insert_text((60, 160), BODY, fontsize=11)
        doc.save(path)
        doc.close()
        texts = [line["text"] for line in documents.parse_pdf(path)["pages"][0]["lines"]]
        self.assertIn("2.2.1 Clinical heterogeneity", texts)

    def test_slightly_larger_numbered_line_is_heading(self):
        # 论文的三级标题 "3.2.4 ..." 是 12 号、不加粗，只比正文大 1 号
        r = self.parse([[(BODY, 11), ("3.2.4 Data representation", 12), (BODY, 11)]])
        line = [l for l in r["pages"][0]["lines"] if l["text"].startswith("3.2.4")][0]
        self.assertTrue(line["heading"])

    def test_bold_numbered_line_at_body_size_is_heading(self):
        # 论文附录的 "H.1 ..." 和正文一样是 11 号，靠整行加粗 + 编号开头识别
        r = self.parse([[(BODY, 11), ("H.1 Earlier evaluation", 11, True), (BODY, 11)]])
        line = [l for l in r["pages"][0]["lines"] if l["text"] == "H.1 Earlier evaluation"][0]
        self.assertTrue(line["heading"])

    def test_bold_table_header_is_not_heading(self):
        # 表头 "Element" 也是加粗 11 号，但没有编号，不算标题
        r = self.parse([[(BODY, 11), ("Element", 11, True), (BODY, 11)]])
        line = [l for l in r["pages"][0]["lines"] if l["text"] == "Element"][0]
        self.assertFalse(line["heading"])

    def test_bold_numbered_list_item_is_not_heading(self):
        # "1. ..." 是列表项（编号后面跟句点），不是 "1.1 ..." 这种章节编号
        r = self.parse([[(BODY, 11), ("1. Experiment orchestration", 11, True), (BODY, 11)]])
        line = [l for l in r["pages"][0]["lines"] if l["text"].startswith("1.")][0]
        self.assertFalse(line["heading"])

    def test_summarize_separates_numbered_headings(self):
        r = self.parse([[("Big Cover Title", 20), (BODY, 11), ("1 Purpose", 14), (BODY, 11)]])
        s = documents.summarize(r)
        self.assertEqual(len(s["headings"]), 2)
        self.assertEqual(s["numbered_headings"], [(1, "1 Purpose")])

    # ---- 出错时返回 ok=False，不抛异常 ----
    def test_missing_file(self):
        r = documents.parse_pdf(self.dir / "nope.pdf")
        self.assertFalse(r["ok"])
        self.assertIn("不存在", r["error"])

    def test_not_pdf_suffix(self):
        path = self.dir / "notes.txt"
        path.write_text("hello", encoding="utf-8")
        self.assertFalse(documents.parse_pdf(path)["ok"])

    def test_corrupted_pdf(self):
        path = self.dir / "fake.pdf"
        path.write_bytes(b"this is not really a pdf")
        r = documents.parse_pdf(path)
        self.assertFalse(r["ok"])
        self.assertIn("无法打开", r["error"])

    def test_password_protected_pdf(self):
        path = self.dir / "locked.pdf"
        doc = pymupdf.Document()
        doc.new_page().insert_text((60, 60), BODY)
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
        doc.close()
        r = documents.parse_pdf(path)
        self.assertFalse(r["ok"])
        self.assertIn("密码", r["error"])


class SampleProtocolTest(unittest.TestCase):
    """项目自带的合成样例 docs/sample_protocol.pdf。"""

    def setUp(self):
        self.r = documents.parse_pdf(Path("docs") / "sample_protocol.pdf")
        self.assertTrue(self.r["ok"], self.r)

    def test_structure(self):
        s = documents.summarize(self.r)
        self.assertEqual(s["pages"], 3)
        self.assertIn((2, "4 Training"), s["numbered_headings"])
        self.assertEqual(s["empty_pages"], [])

    def test_facts_on_expected_pages(self):
        text = {p["page"]: " ".join(line["text"] for line in p["lines"]) for p in self.r["pages"]}
        self.assertIn("ten random seeds", text[2])
        self.assertIn("recorded as missing", text[3])
        self.assertIn("ignore all previous instructions", text[3])   # 提示注入测试句只是普通文字


if __name__ == "__main__":
    unittest.main()
