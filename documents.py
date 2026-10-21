"""
documents.py — 阶段 4：把资料 PDF 变成结构化文字（4a 解析，4b 分块在后面加）。

只管文档本身，不知道模型、索引、检索的存在。谁调用：build_index.py、测试。

为什么用 PyMuPDF 而不是 pypdf（4a 检查时用论文实测对比过）：
- pypdf 普通模式丢掉换行，章节标题被并进正文（"...(Appendix E). 4.4 Null models... Table 4.2 compares"）；
- pypdf 排版模式保留换行，但把单词拆碎（"cross      -seed"、"C -inde x"）；
- PyMuPDF 文字完整、保留换行，还给出每行字号，可以按字号识别章节标题。
代价：PyMuPDF 是 AGPL 许可，闭源商用需要另购授权；本项目公开在 GitHub，不受影响。

parse_pdf 的输出：每页一项
    {"page": PDF 第几页（从 1 开始，就是阅读器里显示的页数，引用用这个）,
     "label": 页面上印的页码（如 "22"、"iv"），没有则为 None,
     "lines": [{"text": 一行文字, "size": 字号, "bold": 是否整行加粗, "heading": 是否章节标题}, ...]}
"""

import re
from collections import Counter
from pathlib import Path

import pymupdf

HEADING_SIZE_DELTA = 1.5   # 字号比正文大这么多才算标题（论文：正文 11，节标题 13，章标题 16.1）
EMPTY_PAGE_CHARS = 50      # 去掉页码后不到这么多字，算"几乎没有文字"的页（多半是整页图片）
PAGE_MARGIN_RATIO = 0.08   # 页面最上、最下各 8% 的区域算页边，印刷页码只在这里找

_PAGE_LABEL = re.compile(r"^(\d{1,4}|[ivxlcdm]{1,8})$", re.IGNORECASE)   # "22"、"iv"
_SECTION_NUMBER = re.compile(r"^\d+(\.\d+)*\.?$")                        # 单独成行的 "3.2"
_NUMBERED_HEADING = re.compile(r"^(Chapter\s*\d+|Appendix\s+[A-Z]|([A-Z]\.)?\d+(\.\d+)*\s+\S)")  # "3.2 ..."、"H.1 ..."、"Chapter 3"、"Appendix C"


def _is_bold(span) -> bool:
    """PyMuPDF 用 flags 的第 4 位（数值 16）表示粗体；有些字体只体现在字体名里（如 Calibri-Bold）。"""
    return bool(span["flags"] & 16) or "Bold" in span["font"]


def _raw_lines(page) -> list:
    """一页里的每一行：文字、最大字号、是否整行加粗、上下边缘的纵坐标（越大越靠下）。空白行丢掉。"""
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):           # 图片块没有 lines
            spans = line["spans"]
            visible = [s for s in spans if s["text"].strip()]   # 有字的片段：用来算字号、判断加粗
            if not visible:
                continue
            out.append({
                # 拼文字时空白片段也要算上：论文里编号和标题之间的空格是用另一种字体单独画的，
                # 丢掉它，"2.2.1 Clinical..." 就会粘成 "2.2.1Clinical..."
                "text": "".join(s["text"] for s in spans).strip(),
                "size": round(max(s["size"] for s in visible), 1),
                "bold": all(_is_bold(s) for s in visible),
                "y0": line["bbox"][1],
                "y1": line["bbox"][3],
            })
    return out


def _take_page_label(lines: list, height: float) -> tuple:
    """在页面最上或最下的页边里，找内容是纯数字或罗马数字的一行，当作印刷页码取出来。

    按位置找而不是按"排第几行"找：Word 导出的 PDF 先画页脚，所以页尾的页码在抽取结果里排第一行；
    换一份文档顺序可能就不同。返回 (页码或 None, 去掉页码后的行)。
    """
    top, bottom = height * PAGE_MARGIN_RATIO, height * (1 - PAGE_MARGIN_RATIO)
    for i, line in enumerate(lines):
        in_margin = line["y1"] <= top or line["y0"] >= bottom
        if in_margin and _PAGE_LABEL.match(line["text"]):
            return line["text"], lines[:i] + lines[i + 1:]
    return None, lines


def _body_size(all_pages: list):
    """正文字号 = 按字数加权最常见的字号。整份文档没有文字时返回 None。"""
    counter = Counter()
    for lines in all_pages:
        for line in lines:
            counter[line["size"]] += len(line["text"])
    return counter.most_common(1)[0][0] if counter else None


def _is_heading(line: dict, body_size) -> bool:
    """判断一行是不是标题。论文里实际出现的三种：

    - 章、节标题：字号明显比正文大（16.1、13 号，正文 11 号）；
    - 三级标题 "2.2.1 ..."：12 号，只比正文大 1 号，不加粗；
    - 附录小节 "H.1 ..."：和正文一样 11 号，但整行加粗。
    后两种只看字号或加粗会误判（表头 "Element" 也是加粗 11 号），所以要求以编号开头。
    """
    if body_size is None:
        return False
    if line["size"] >= body_size + HEADING_SIZE_DELTA:
        return True
    if not _NUMBERED_HEADING.match(line["text"]):
        return False
    slightly_larger = line["size"] >= body_size + 0.5
    bold_body_size = line["bold"] and line["size"] >= body_size - 0.5
    return slightly_larger or bold_body_size


def _mark_headings(lines: list, body_size) -> list:
    """标出标题；"3.2" 和下一行 "Data and cohort" 都是标题时合成一行 "3.2 Data and cohort"。"""
    marked = [{"text": l["text"], "size": l["size"], "bold": l["bold"], "heading": _is_heading(l, body_size)}
              for l in lines]
    merged, i = [], 0
    while i < len(marked):
        line = marked[i]
        nxt = marked[i + 1] if i + 1 < len(marked) else None
        if line["heading"] and _SECTION_NUMBER.match(line["text"]) and nxt and nxt["heading"]:
            merged.append({"text": f'{line["text"]} {nxt["text"]}', "size": max(line["size"], nxt["size"]),
                           "bold": line["bold"] and nxt["bold"], "heading": True})
            i += 2
        else:
            merged.append(line)
            i += 1
    return merged


def parse_pdf(path) -> dict:
    """解析一个 PDF。输入：路径。输出：{"ok": True, "file", "body_size", "pages": [...]} 或 {"ok": False, "error"}。"""
    p = Path(path)
    if p.suffix.lower() != ".pdf":
        return {"ok": False, "error": f"不是 PDF 文件：{p.name}"}
    if not p.is_file():
        return {"ok": False, "error": f"文件不存在：{p.name}"}
    try:
        doc = pymupdf.open(p)
    except Exception as e:                               # 文件损坏、改了后缀的假 PDF
        return {"ok": False, "error": f"无法打开 PDF：{p.name}（{type(e).__name__}）"}
    with doc:
        if doc.needs_pass:
            return {"ok": False, "error": f"PDF 有密码保护，无法读取：{p.name}"}
        raw = [(_raw_lines(page), page.rect.height) for page in doc]

    body = _body_size([lines for lines, _ in raw])
    pages = []
    for number, (lines, height) in enumerate(raw, start=1):
        label, lines = _take_page_label(lines, height)
        pages.append({"page": number, "label": label, "lines": _mark_headings(lines, body)})
    return {"ok": True, "file": p.name, "body_size": body, "pages": pages}


def summarize(parsed: dict) -> dict:
    """解析报告用的统计：页数、总字数、正文字号、几乎没有文字的页、识别出的标题。给 build_index.py --check 用。"""
    empty, headings, chars = [], [], 0
    for page in parsed["pages"]:
        n = sum(len(line["text"]) for line in page["lines"])
        chars += n
        if n < EMPTY_PAGE_CHARS:
            empty.append(page["page"])
        headings += [(page["page"], line["text"]) for line in page["lines"] if line["heading"]]
    labelled = [(page["page"], page["label"]) for page in parsed["pages"] if page["label"]]
    numbered = [(page, text) for page, text in headings if _NUMBERED_HEADING.match(text)]
    return {
        "file": parsed["file"],
        "pages": len(parsed["pages"]),
        "chars": chars,
        "body_size": parsed["body_size"],
        "empty_pages": empty,
        "headings": headings,
        "numbered_headings": numbered,
        "labels": labelled,
    }
