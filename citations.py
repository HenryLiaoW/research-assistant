"""
citations.py — 证据编号与引用校验（阶段 4e）。

做法：search_documents 返回的每个片段由程序编上证据编号（E1、E2……）再交给模型；
模型回答时只写编号（如 [E1]），文件名和页码由程序根据编号补上，模型没有机会自己拼出处。
回答里出现不存在的编号时标为无效引用。

编号在整段会话里连续（下一个编号 = 历史里已有的最大编号 + 1），同一个 chunk 再次被检索到时沿用原编号，
所以历史里不会出现同一个编号指向两个不同片段的情况。
注意：程序只能检查"引用是否来自检索结果"，不能检查"这段原文是否真的支持回答"，后者要靠端到端评估。
"""

import json
import re

_CITATION = re.compile(r"\[\s*(E\d+(?:\s*[,，、]\s*E\d+)*)\s*\]")   # [E1]、[E1, E3]、[E2，E5]


def _numbered_results(messages: list):
    """逐个给出历史里已编号的检索结果：(那次检索的工具结果, 其中一个片段)。"""
    for m in messages:
        if m.get("role") != "tool":
            continue
        try:
            data = json.loads(m.get("content") or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            continue
        for r in data["results"]:
            if isinstance(r, dict) and "evidence_id" in r:
                yield data, r


def collect_evidence(messages: list) -> dict:
    """从历史里的工具结果收集已编号的证据：{"E1": {chunk_id, file, page, label, section}, ...}。"""
    return {r["evidence_id"]: {k: r.get(k) for k in ("chunk_id", "file", "page", "label", "section")}
            for _, r in _numbered_results(messages)}


def collect_passages(messages: list) -> dict:
    """和 collect_evidence 一样，但多带原文、相似度和检索词。谁调用：webapp.py（侧边栏"看原文"）。
    原文就是当时作为工具结果交给模型的那段文字，不重新去索引里查。"""
    return {r["evidence_id"]: {**{k: r.get(k) for k in ("chunk_id", "file", "page", "label", "section")},
                               "score": r.get("score"), "text": r.get("text"), "query": data.get("query")}
            for data, r in _numbered_results(messages)}


def assign_ids(results: list, messages: list) -> None:
    """给一次检索的结果编号（就地修改 results）。已经编过号的 chunk 沿用原编号，新的接着往后编。"""
    known = collect_evidence(messages)
    by_chunk = {src["chunk_id"]: eid for eid, src in known.items()}
    next_n = max((int(eid[1:]) for eid in known), default=0) + 1
    for i, r in enumerate(results):
        eid = by_chunk.get(r["chunk_id"])
        if eid is None:
            eid = f"E{next_n}"
            next_n += 1
            by_chunk[r["chunk_id"]] = eid
        results[i] = {"evidence_id": eid, **r}


def extract_citations(answer: str) -> list:
    """按第一次出现的顺序列出回答里引用的编号，不重复。"""
    cited = []
    for group in _CITATION.findall(answer or ""):
        for eid in re.split(r"\s*[,，、]\s*", group):
            if eid not in cited:
                cited.append(eid)
    return cited


def describe_source(src: dict) -> str:
    text = f"{src['file']} 第 {src['page']} 页"
    if src.get("label"):
        text += f"（印刷页 {src['label']}）"
    if src.get("section"):
        text += f" · {src['section']}"
    return text


def render(answer: str, evidence: dict) -> tuple:
    """返回 (给用户看的回答, 有效编号, 无效编号)。有效编号的出处由程序列在回答后面。"""
    cited = extract_citations(answer)
    valid = [eid for eid in cited if eid in evidence]
    invalid = [eid for eid in cited if eid not in evidence]
    shown = answer
    if valid:
        shown += "\n\n来源：\n" + "\n".join(f"[{eid}] {describe_source(evidence[eid])}" for eid in valid)
    if invalid:
        shown += "\n\n无效引用（不在检索结果中）：" + "、".join(invalid)
    return shown, valid, invalid
