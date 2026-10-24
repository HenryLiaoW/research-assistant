"""
eval_retrieval.py — 检索评估（阶段 4d）：用 eval/retrieval_questions.json 的 28 道题给检索打分。

用法（项目目录、已激活 .venv、已运行过 python build_index.py）：
    python eval_retrieval.py            跑完所有题，打印每题结果和总分，结果另存到 eval/local/
    python eval_retrieval.py --k 5      每题取前 5 个结果（默认 3 = 交给模型的 chunk 数）

指标：
    完整召回@k（主指标）：一道题的所有证据组都在前 k 个结果里找到才得 1 分，再对所有题求平均
    召回@k：每道题找到的证据组数 / 总组数，再求平均
    MRR：每道题第一个相关结果排名的倒数（第 1 名 = 1，第 2 名 = 0.5，没找到 = 0），再求平均
按页判断：结果的文件对、页码落在某个证据组里就算找到。偏乐观：页对了不代表那个 chunk 里有答案。

全部在本机运行，不调用任何模型 API。结果文件只有页码、章节名和分数，不含原文；eval/local/ 不进 git。
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import config
import retrieval

QUESTIONS_PATH = Path("eval/retrieval_questions.json")
RESULTS_DIR = Path("eval/local")
DEFAULT_K = 3


def score_question(pages: list, groups: list) -> dict:
    """给一道题打分。
    pages：检索结果的页码，按排名从高到低；其他文件的结果已换成 None（永远不算找到）
    groups：证据组，如 [[22], [23]]。每组是一处必需的证据，组内任意一页都算找到了这一组
    返回 {"full": 所有组都找到为 1、否则为 0, "recall": 找到的组数 / 总组数, "rr": 第一个相关结果排名的倒数，没有为 0.0}
    """

    found = 0
    for group in groups:
        if any(page in group for page in pages):
            found += 1
   
    rr = 0.0
    for rank, page in enumerate(pages, start=1):
        if any(page in group for group in groups):
            rr = 1 / rank
            break
    
    return {"full": 1 if found == len(groups) else 0, "recall": found / len(groups), "rr": rr}


def evaluate(questions: list, searcher, k: int) -> tuple:
    """每道题检索一次并打分。返回 (每题结果, 总分)。"""
    rows = []
    for q in questions:
        results = searcher.search(q["question"], k)
        pages = [r["page"] if r["file"] == q["file"] else None for r in results]   # 其他文件的结果不算
        rows.append({"id": q["id"], "question": q["question"], "groups": q["groups"], "pages": pages,
                     "sections": [r["section"] for r in results], "scores": [r["score"] for r in results],
                     **score_question(pages, q["groups"])})
    n = len(rows)
    summary = {"questions": n,
               "full_recall": sum(r["full"] for r in rows) / n,
               "recall": sum(r["recall"] for r in rows) / n,
               "mrr": sum(r["rr"] for r in rows) / n}
    return rows, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="用评估题给检索打分（只在本机运行，不调用模型 API）")
    parser.add_argument("--k", type=int, default=DEFAULT_K, help=f"每题取前几个结果（默认 {DEFAULT_K}）")
    args = parser.parse_args()

    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    print(f"加载嵌入模型 {config.EMBEDDING_MODEL} 和索引 {config.INDEX_DIR}/ ……")
    searcher = retrieval.DocumentSearch(config.INDEX_DIR, retrieval.Embedder(config.EMBEDDING_MODEL))
    rows, summary = evaluate(questions, searcher, args.k)

    for r in rows:
        mark = "✅" if r["full"] else ("◐" if r["recall"] > 0 else "❌")   # ◐ = 多处证据只找到一部分
        print(f"{mark} {r['id']}  取到的页 {r['pages']}  应找到 {r['groups']}  {r['question']}")
    full = sum(r["full"] for r in rows)
    print(f"\n{config.EMBEDDING_MODEL}，k = {args.k}，{summary['questions']} 题："
          f"完整召回@{args.k} {summary['full_recall']:.3f}（{full}/{summary['questions']}）"
          f"  召回@{args.k} {summary['recall']:.3f}  MRR {summary['mrr']:.3f}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"retrieval_{config.EMBEDDING_MODEL.split('/')[-1]}_k{args.k}_{stamp}.json"
    run = {"index": searcher.manifest, "k": args.k, "summary": summary, "rows": rows,
           "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    out.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"结果已保存：{out}")


if __name__ == "__main__":
    main()
