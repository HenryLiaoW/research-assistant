"""
build_index.py — 资料检索的离线步骤（阶段 4）。

用法（项目目录、已激活 .venv）：
    python build_index.py --check     只解析 docs/ 里的 PDF 并打印解析报告，不建索引（4a）
    python build_index.py             解析、分块、向量化，写入 index/（4c；只在本机运行，不调用模型 API）
    python build_index.py --no-skip   同上，但不跳过 config.SKIP_SECTIONS 里的章节（用于复现基线）

先看报告、确认文字抽得对，再建索引（建索引在 4c 加上）。解析坏了还建索引，检索质量会悄悄变差。
这一步完全在本机运行，不调用任何模型 API。
"""

import argparse
import time
from pathlib import Path

import config
import documents
import retrieval


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



# 这个函数要做的就是输入path列表（files）、embedder、size、overlap、skip_sections，输出chunks、vectors、failed
def build(files: list, embedder, size: int, overlap: int, skip_sections=()) -> tuple:
    """解析 → 分块 → 向量化。返回 (chunks, vectors, failed)，failed 是解析失败的文件 [(文件名, 错误原因), ...]。"""
    chunks, failed = [], []
    for path in files:
        
        parsed = documents.parse_pdf(path) #拆成一行行的
        
        if not parsed["ok"]:
            failed.append((path.name, parsed["error"]))
            continue
        # 切完chunk后，丢掉 skip_sections 里指定的章节
        chunks.extend(documents.drop_sections(documents.chunk_document(parsed, size, overlap), skip_sections))
   
    texts=[]
    for chunk in chunks:
        texts.append(chunk["text"])
    vectors = embedder.embed_passages(texts)
    return chunks, vectors, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="解析 docs/ 里的 PDF；建索引前先用 --check 检查")
    parser.add_argument("--check", action="store_true", help="只打印解析报告，不建索引")
    parser.add_argument("--no-skip", action="store_true", help="不跳过任何章节（复现基线用）")
    args = parser.parse_args()

    docs_dir = Path(config.DOCS_DIR) # 获取docs地址
    if not docs_dir.is_dir():
        print(f"资料目录不存在：{docs_dir}")
        return
    files = pdf_files(docs_dir) #读取一个Path 输出一个列表，里面是这个文件夹里所有PDF文件的Path对象
    # → [PosixPath('docs/dissertation.pdf'), PosixPath('docs/sample_protocol.pdf')]
    if not files:
        print(f"{docs_dir}/ 里没有 PDF")
        return
    if not args.check:
        build_and_save(files, () if args.no_skip else config.SKIP_SECTIONS)
        return

    for path in files:
        parsed = documents.parse_pdf(path)
        if not parsed["ok"]:
            print(f"\n=== {path.name} ===\n[失败] {parsed['error']}")
            continue
        print_report(parsed)


def build_and_save(files: list, skip_sections=()) -> None:
    print(f"加载嵌入模型 {config.EMBEDDING_MODEL}（只在本机运行；第一次使用会下载模型文件）")
    t0 = time.perf_counter()
    embedder = retrieval.Embedder(config.EMBEDDING_MODEL) # 加载 config 里指定的嵌入模型
    load_seconds = time.perf_counter() - t0
    t0 = time.perf_counter()
    chunks, vectors, failed = build(files, embedder, config.CHUNK_SIZE, config.CHUNK_OVERLAP, skip_sections)
    build_seconds = time.perf_counter() - t0               # 解析 + 分块 + 向量化，不含加载模型
    for name, error in failed:
        print(f"[跳过] {name}：{error}")
    if not chunks:
        print("没有可用的 chunk，未建索引")
        return
    info = {"model": config.EMBEDDING_MODEL, "chunk_size": config.CHUNK_SIZE,
            "chunk_overlap": config.CHUNK_OVERLAP, "skip_sections": list(skip_sections),
            "files": sorted({c["file"] for c in chunks}),
            "model_load_seconds": round(load_seconds, 1), "build_seconds": round(build_seconds, 1)}
    manifest = retrieval.save_index(config.INDEX_DIR, chunks, vectors, info)
    print(f"索引已写入 {config.INDEX_DIR}/：{manifest['chunks']} 个 chunk，向量长度 {manifest['dim']}，"
          f"文件 {', '.join(manifest['files'])}；加载模型 {load_seconds:.1f} 秒，建索引 {build_seconds:.1f} 秒")


if __name__ == "__main__":
    main()
