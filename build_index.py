"""
build_index.py — 资料检索的离线步骤（阶段 4）。

用法（项目目录、已激活 .venv）：
    python build_index.py --check     只解析 docs/ 里的 PDF 并打印解析报告，不建索引（4a）

先看报告、确认文字抽得对，再建索引（建索引在 4c 加上）。解析坏了还建索引，检索质量会悄悄变差。
这一步完全在本机运行，不调用任何模型 API。
"""

import argparse
from pathlib import Path

import config
import documents


def pdf_files(docs_dir: Path) -> list:
    """docs 目录里的 PDF，按文件名排序。用后缀判断，大小写都认。"""
    return sorted(p for p in docs_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")


def print_report(parsed: dict) -> None:
    s = documents.summarize(parsed)
    print(f"\n=== {s['file']} ===")
    print(f"页数 {s['pages']}，抽出文字 {s['chars']} 字，正文字号 {s['body_size']}")
    if s["chars"] < 100:
        print("[警告] 几乎没有文字层，可能是扫描件；本项目不做 OCR，这份文件检索不到内容")
    if s["labels"]:
        pairs = "，".join(f"PDF 第 {p} 页 = 印刷页 {lab}" for p, lab in s["labels"][:3])
        print(f"页码示例：{pairs}（页面上印的页码；引用一律用 PDF 第几页）")
    print(f"几乎没有文字的页（多半是整页图片）：{s['empty_pages'] or '无'}")
    numbered = s["numbered_headings"]
    print(f"识别出的标题 {len(s['headings'])} 个，其中带编号的章节标题 {len(numbered)} 个"
          f"（其余多为封面等页的大字）。请和文档自己的目录逐条对照，看有没有漏、有没有多：")
    for page, text in numbered:
        print(f"  p.{page}  {text}")
    pages = parsed["pages"]
    for fraction in (0.25, 0.5, 0.85):          # 前、中、后各抽一页正文，看有没有断词、乱码
        start = int(len(pages) * fraction)
        sample = next((p for p in pages[start:] if len(p["lines"]) > 5), None)
        if sample:
            text = " ".join(line["text"] for line in sample["lines"])
            print(f"抽样 p.{sample['page']}：{text[:200]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="解析 docs/ 里的 PDF；建索引前先用 --check 检查")
    parser.add_argument("--check", action="store_true", help="只打印解析报告，不建索引")
    args = parser.parse_args()

    docs_dir = Path(config.DOCS_DIR)
    if not docs_dir.is_dir():
        print(f"资料目录不存在：{docs_dir}")
        return
    files = pdf_files(docs_dir)
    if not files:
        print(f"{docs_dir}/ 里没有 PDF")
        return
    if not args.check:
        print("建索引在 4c 实现；现在请用 --check")
        return

    for path in files:
        parsed = documents.parse_pdf(path)
        if not parsed["ok"]:
            print(f"\n=== {path.name} ===\n[失败] {parsed['error']}")
            continue
        print_report(parsed)


if __name__ == "__main__":
    main()
