"""
retrieval.py — 向量索引（阶段 4c）：把 chunk 转成向量存到本地，检索时再读出来。

全部在本机运行，不调用任何模型 API。只有第一次加载嵌入模型时，会从 Hugging Face 下载模型文件。
索引目录（config.INDEX_DIR）不进 git：里面有资料原文的片段。
"""

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

    def search(self, query: str, k: int = 3) -> list:
        """返回最相近的 k 个 chunk，从高到低；每个是 chunk 字典的副本，多一个 "score"（余弦相似度）。"""
        q = self.embedder.embed_query(query)
        scores = self.vectors @ q
        results = []
        top = np.argsort(-scores)[:k]
        for i in top:
            results.append({**self.chunks[i], "score": round(float(scores[i]), 4)})

        return results
