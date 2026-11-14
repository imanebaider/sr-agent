"""Hybrid index: BM25 + TF-IDF cosine (pure Python, no chromadb/grpc), fused with RRF."""
import math
import re
from collections import Counter
from pathlib import Path

from rank_bm25 import BM25Okapi


def chunk_text(text: str, size: int = 1200, overlap: int = 200) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + size])
        if start + size >= len(text):
            break
        start += size - overlap
    return chunks


def tokenize(s: str) -> list[str]:
    return re.findall(r"\w+", s.lower())


class HybridIndex:
    def __init__(self, persist_dir: str | Path | None = None):
        # persist_dir is kept for compatibility; the index lives in memory.
        self.chunks: list[str] = []
        self.meta: list[dict] = []

    def add(self, doc_id: str, text: str, source_type: str) -> int:
        chunks = chunk_text(text)
        for c in chunks:
            self.chunks.append(c)
            self.meta.append({"doc_id": doc_id, "source": source_type})
        return len(chunks)

    def _tfidf_rank(self, idxs: list[int], query: str) -> list[int]:
        docs = [Counter(tokenize(self.chunks[i])) for i in idxs]
        n = len(docs)
        df: Counter = Counter()
        for d in docs:
            df.update(d.keys())
        idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}

        def vec(counter: Counter) -> dict[str, float]:
            return {t: (1 + math.log(c)) * idf[t] for t, c in counter.items() if t in idf}

        q = vec(Counter(tokenize(query)))
        qn = math.sqrt(sum(v * v for v in q.values())) or 1.0
        scores = []
        for d in docs:
            v = vec(d)
            dn = math.sqrt(sum(x * x for x in v.values())) or 1.0
            dot = sum(w * v.get(t, 0.0) for t, w in q.items())
            scores.append(dot / (dn * qn))
        order = sorted(range(len(idxs)), key=lambda j: -scores[j])
        return [idxs[j] for j in order]

    def search(self, query: str, doc_id: str | None = None, k: int = 4) -> list[str]:
        idxs = [i for i, m in enumerate(self.meta) if doc_id is None or m["doc_id"] == doc_id]
        if not idxs:
            return []
        # sparse ranking (BM25)
        bm25 = BM25Okapi([tokenize(self.chunks[i]) or ["_"] for i in idxs])
        scores = bm25.get_scores(tokenize(query))
        sparse_rank = [idxs[j] for j in sorted(range(len(idxs)), key=lambda j: -scores[j])]
        # second ranking (TF-IDF cosine)
        tfidf_rank = self._tfidf_rank(idxs, query)
        # Reciprocal Rank Fusion
        fused: dict[int, float] = {}
        for ranking in (sparse_rank, tfidf_rank):
            for rank, i in enumerate(ranking):
                fused[i] = fused.get(i, 0.0) + 1.0 / (60 + rank)
        top = sorted(fused, key=lambda i: -fused[i])[:k]
        return [self.chunks[i] for i in top]