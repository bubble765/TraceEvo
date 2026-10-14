"""S3B-F §11: deterministic retrieval over admitted experience.

§11 asks for local TF-IDF/BM25 rather than an embedding model, and for a hard
rule: when nothing clears the minimum similarity, return `[]`. The temptation
§11 is guarding against is injecting a weak neighbour so the memory arm looks
like it did something, which would turn a null result into a fake one.

BM25 is implemented here rather than pulled in, because the package must stay
runnable offline and because at this corpus size the implementation is shorter
than the dependency list would be.

The query is the text a planner would already have **before the run starts** —
the case's chief complaint and physical examination. Nothing computed during
the run is used, and §11 is explicit that the retrieval key itself is never
injected: it exists to be compared, not shown.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

K1 = 1.5
B = 0.75

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with",
    "was", "were", "is", "are", "be", "been", "being", "his", "her", "their",
    "patient", "at", "as", "by", "from", "that", "this", "these", "those",
    "he", "she", "it", "they", "no", "not", "did", "has", "had", "have",
    "who", "whom", "which", "presented", "history", "year", "years", "old",
}


def tokenize(text: str) -> List[str]:
    return [w for w in _TOKEN_RE.findall(str(text or "").lower())
            if w not in _STOP and len(w) > 2]


def build_retrieval_key(case_text: str, physical_text: str) -> str:
    """§5.1: built from what is visible before the run, and nothing else."""
    return f"{case_text}\n{physical_text}".strip()


class BM25Index:
    """Deterministic: same corpus and query always give the same ranking."""

    def __init__(self, docs: List[Tuple[str, str]]):
        """`docs` is (doc_id, text)."""
        self.ids = [d[0] for d in docs]
        self.docs = [tokenize(d[1]) for d in docs]
        self.n = len(self.docs)
        self.avgdl = (sum(len(d) for d in self.docs) / self.n) if self.n else 0.0
        self.tf: List[Dict[str, int]] = []
        df: Dict[str, int] = {}
        for doc in self.docs:
            counts: Dict[str, int] = {}
            for w in doc:
                counts[w] = counts.get(w, 0) + 1
            self.tf.append(counts)
            for w in counts:
                df[w] = df.get(w, 0) + 1
        self.idf = {
            w: math.log(1 + (self.n - c + 0.5) / (c + 0.5))
            for w, c in df.items()}

    def score(self, query: str, i: int) -> float:
        q = tokenize(query)
        if not q or not self.n:
            return 0.0
        dl = len(self.docs[i]) or 1
        s = 0.0
        for w in q:
            f = self.tf[i].get(w, 0)
            if not f:
                continue
            s += self.idf.get(w, 0.0) * (f * (K1 + 1)) / (
                f + K1 * (1 - B + B * dl / (self.avgdl or 1)))
        return s

    def top_k(self, query: str, k: int = 2,
              min_score: float = 0.0) -> List[Dict[str, Any]]:
        scored = [(self.ids[i], self.score(query, i)) for i in range(self.n)]
        scored = [s for s in scored if s[1] > 0]
        scored.sort(key=lambda x: (-x[1], x[0]))     # id breaks ties: stable
        out = [{"memory_id": mid, "score": round(sc, 4)}
               for mid, sc in scored[:k]]
        # §11: a weak neighbour is not a hit. Report nothing rather than
        # something irrelevant.
        return [h for h in out if h["score"] >= min_score]


def retrieve(store, query: str, k: int = 2,
             min_score: float = 0.5) -> List[Dict[str, Any]]:
    """§11's entry point: query -> at most `k` admitted experiences."""
    docs = [(e["memory_id"], e["task_retrieval_key"])
            for e in store.retrievable()]
    if not docs:
        return []
    index = BM25Index(docs)
    hits = index.top_k(query, k=k, min_score=min_score)
    by_id = {e["memory_id"]: e for e in store.retrievable()}
    return [dict(by_id[h["memory_id"]], retrieval_score=h["score"],
                 rank=i + 1) for i, h in enumerate(hits)]
