"""
retrieval.py — 向量索引（阶段 4c）：把 chunk 转成向量存到本地，检索时再读出来。

全部在本机运行，不调用任何模型 API。只有第一次加载嵌入模型时，会从 Hugging Face 下载模型文件。
索引目录（config.INDEX_DIR）不进 git：里面有资料原文的片段。
建索引时记下每个资料文件的 SHA-256 指纹；资料变了（新增、删除、内容改动），旧索引判为过期，要求重建。
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


class Embedder:
    """包装本地的 e5 嵌入模型。e5 的要求：问题前加 "query: "，原文前加 "passage: "。输出归一化向量。"""

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer   # 放在这里导入：跑单元测试不用加载 torch
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)

    def embed_passages(self, texts: list) -> np.ndarray:
        vectors = self._model.encode([f"passage: {t}" for t in texts], normalize_embeddings=True,
                                     batch_size=32, show_progress_bar=True)
        return np.asarray(vectors, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        vector = self._model.encode([f"query: {text}"], normalize_embeddings=True)[0]
        return np.asarray(vector, dtype=np.float32)


def save_index(index_dir, chunks: list, vectors: np.ndarray, info: dict) -> dict:
    """写三个文件：chunks.jsonl（原文 + 出处）、embeddings.npy（向量）、manifest.json（怎么建的）。
    第 i 行 chunk 对应第 i 行向量，所以两者数量必须一致。"""
    if len(chunks) != len(vectors):
        raise ValueError(f"chunk 数（{len(chunks)}）和向量数（{len(vectors)}）不一致")
    d = Path(index_dir)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "chunks.jsonl", "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    np.save(d / "embeddings.npy", np.asarray(vectors, dtype=np.float32))
    manifest = {**info, "chunks": len(chunks), "dim": int(vectors.shape[1]),
                "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def load_index(index_dir) -> tuple: # 加载索引
    """读回 (chunks, vectors, manifest)。索引不存在或数量对不上时报错，提示重建。"""
    d = Path(index_dir)
    if not (d / "manifest.json").exists():
        raise FileNotFoundError(f"索引不存在：{d}/，请先运行 python build_index.py")
    with open(d / "chunks.jsonl", encoding="utf-8") as f:
        chunks = [json.loads(line) for line in f]
    vectors = np.load(d / "embeddings.npy")
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    if len(chunks) != len(vectors):
        raise ValueError(f"索引损坏：chunk 数（{len(chunks)}）和向量数（{len(vectors)}）不一致，请重建")
    return chunks, vectors, manifest


def fingerprint(path) -> str:
    """文件内容的 SHA-256。内容差一个字节，指纹就完全不同；只改修改时间、不改内容，指纹不变。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):     # 每次读 1 MB，大文件也不占内存
            h.update(block)
    return h.hexdigest()


def source_fingerprints(files: list) -> dict:
    """{文件名: 指纹}。谁调用：build_index（写进 manifest）、stale_reasons（算现在的）。"""
    return {Path(p).name: fingerprint(p) for p in files}


def stale_reasons(manifest: dict, files: list) -> list:
    """对比建索引时记下的指纹和资料目录现在的文件，返回过期原因；空列表 = 索引和资料一致。"""
    recorded = manifest.get("sources")
    if recorded is None:
        return ["索引建于加入资料指纹之前，无法确认资料有没有变"]
    current = source_fingerprints(files)
    reasons = [f"新增了 {name}" for name in sorted(current.keys() - recorded.keys())]
    reasons += [f"删除了 {name}" for name in sorted(recorded.keys() - current.keys())]
    reasons += [f"{name} 的内容变了" for name in sorted(current.keys() & recorded.keys())
                if current[name] != recorded[name]]
    return reasons


def ensure_fresh(index_dir, files: list) -> None:
    """检索前的检查：索引不存在、或资料和建索引时不一致，就报错并提示重建。
    只读 manifest.json，不加载向量和模型，所以很快。谁调用：tools._get_searcher()、eval_retrieval.main()。"""
    path = Path(index_dir) / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"索引不存在：{index_dir}/，请先运行 python build_index.py")
    reasons = stale_reasons(json.loads(path.read_text(encoding="utf-8")), files)
    if reasons:
        raise ValueError("索引已过期：" + "；".join(reasons) + "。请重新运行 python build_index.py")


def indexed_files(index_dir) -> list:
    """建索引时收录的文件名（manifest 里的 files）；索引不存在时返回空列表。
    只读 manifest.json，不加载向量和模型。谁调用：tools.py（把文件名写进 search_documents 的工具说明）。"""
    path = Path(index_dir) / "manifest.json"
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("files") or [])


def index_status(index_dir, files: list) -> dict:
    """索引的概况，给网页的"资料"面板：在不在、怎么建的、每个文件切成了几段、和资料目录是否一致。
    只读 manifest.json 和 chunks.jsonl，不加载向量和模型。谁调用：webapp.py。"""
    d = Path(index_dir)
    if not (d / "manifest.json").exists():
        return {"exists": False, "per_file": {}, "stale": ["索引不存在，请先运行 python build_index.py"]}
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    per_file = {}
    if (d / "chunks.jsonl").exists():
        with open(d / "chunks.jsonl", encoding="utf-8") as f:
            for line in f:
                name = json.loads(line).get("file")
                per_file[name] = per_file.get(name, 0) + 1
    return {"exists": True, "model": manifest.get("model"), "built_at": manifest.get("built_at"),
            "chunks": manifest.get("chunks"), "chunk_size": manifest.get("chunk_size"),
            "per_file": per_file, "stale": stale_reasons(manifest, files)}


class DocumentSearch:
    """检索：读入本地索引，把问题转成向量，返回最相近的 k 个 chunk（带出处和相似度）。
    建一次、查多次：索引只在创建时读一次，之后每次 search 只需要把问题转成向量。"""

    def __init__(self, index_dir, embedder):
        chunks, vectors, manifest = load_index(index_dir)
        if manifest.get("model") != embedder.model_name:     # 问题和原文必须用同一个模型转成向量
            raise ValueError(f"索引是用 {manifest.get('model')} 建的，当前模型是 {embedder.model_name}；"
                             f"两者必须一致，请重新运行 python build_index.py")
        self.chunks = chunks          # 第 i 个 chunk ……
        self.vectors = vectors        # …… 对应第 i 行向量，形状 (chunk 数, 向量长度)
        self.manifest = manifest
        self.embedder = embedder
        self.files = sorted({c["file"] for c in chunks})   # 索引里有哪些文件，给 file 参数做校验

    def search(self, query: str, k: int = 3, file: str = None) -> list:
        """返回最相近的 k 个 chunk，从高到低；每个是 chunk 字典的副本，多一个 "score"（余弦相似度）。

        file：只在这一个文件里找。过滤在取前 k 个之前做（先把其他文件的分数置为 -inf，再排序），
        而不是先取全库前 k 个再过滤：资料库里多份文件主题相近时，小文件的段落常常排不进全库前 k 个，
        "先取后滤"会一条都剩不下。file 不是索引里的文件时报 ValueError（工具层应先校验）。
        """
        if file is not None and file not in self.files:
            raise ValueError(f"索引里没有文件 {file!r}，现有文件：{'、'.join(self.files)}")
        q = self.embedder.embed_query(query)
        scores = self.vectors @ q
        if file is not None:
            mask = np.array([c["file"] == file for c in self.chunks])
            scores = np.where(mask, scores, -np.inf)       # 其他文件的段落不参与排序
        results = []
        top = np.argsort(-scores)[:k]
        for i in top:
            if not np.isfinite(scores[i]):                 # 该文件的段落不足 k 个：有几个返回几个
                break
            results.append({**self.chunks[i], "score": round(float(scores[i]), 4)})

        return results
