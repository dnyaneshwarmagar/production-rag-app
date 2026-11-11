"""
In-memory vector store with metadata filters, used by the test pipeline.

Search is exact: the query is compared with every stored vector.
The API uses pgvector instead. Filters run before scoring.

Filter syntax (a small subset of what Qdrant / Chroma / pgvector + SQL let you do):
    {"department": "support"}                  equals
    {"status": {"$ne": "archived"}}            not equal
    {"department": {"$in": ["hr", "finance"]}}  one of
    {"access": {"$any": ["public", "employees"]}}  list field shares at least one value (ACL!)
    {"effective_date": {"$gte": "2025-01-01"}} comparisons ($gt, $gte, $lt, $lte)
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .documents import Chunk


def matches(meta: dict[str, Any], where: dict[str, Any] | None) -> bool:
    if not where:
        return True
    for key, cond in where.items():
        value = meta.get(key)
        if isinstance(cond, dict):
            for op, arg in cond.items():
                if op == "$ne" and value == arg:
                    return False
                if op == "$in" and value not in arg:
                    return False
                if op == "$nin" and value in arg:
                    return False
                if op == "$any":
                    have = value if isinstance(value, (list, tuple, set)) else [value]
                    if not set(have) & set(arg):
                        return False
                if op in {"$gt", "$gte", "$lt", "$lte"}:
                    if value is None:
                        return False
                    if op == "$gt" and not value > arg:
                        return False
                    if op == "$gte" and not value >= arg:
                        return False
                    if op == "$lt" and not value < arg:
                        return False
                    if op == "$lte" and not value <= arg:
                        return False
        elif value != cond:
            return False
    return True


class InMemoryVectorStore:
    def __init__(self, dim: int, embedder_fingerprint: str = "unknown"):
        self.dim = dim
        self.fingerprint = embedder_fingerprint
        self.chunks: list[Chunk] = []
        self.vectors = np.zeros((0, dim), dtype=np.float32)

    def __len__(self) -> int:
        return len(self.chunks)

    def add(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        if vectors.shape[1] != self.dim:
            raise ValueError(f"vector dim {vectors.shape[1]} != store dim {self.dim}")
        # upsert: replace chunks that already exist (idempotent ingestion)
        existing = {c.chunk_id: i for i, c in enumerate(self.chunks)}
        new_chunks, new_vecs = [], []
        for c, v in zip(chunks, vectors):
            if c.chunk_id in existing:
                self.vectors[existing[c.chunk_id]] = v
                self.chunks[existing[c.chunk_id]] = c
            else:
                new_chunks.append(c)
                new_vecs.append(v)
        if new_chunks:
            self.chunks.extend(new_chunks)
            self.vectors = np.vstack([self.vectors, np.asarray(new_vecs, dtype=np.float32)])

    def delete_document(self, doc_id: str) -> int:
        keep = [i for i, c in enumerate(self.chunks) if c.doc_id != doc_id]
        removed = len(self.chunks) - len(keep)
        self.chunks = [self.chunks[i] for i in keep]
        self.vectors = self.vectors[keep]
        return removed

    def allowed_indices(self, where: dict | None) -> np.ndarray:
        return np.array([i for i, c in enumerate(self.chunks) if matches(c.metadata, where)], dtype=int)

    def search(self, query_vector: np.ndarray, k: int = 5, where: dict | None = None
               ) -> list[tuple[Chunk, float]]:
        """Return the k most similar chunks as (chunk, cosine_similarity)."""
        idx = self.allowed_indices(where)
        if len(idx) == 0:
            return []
        scores = self.vectors[idx] @ query_vector  # cosine, because all vectors have length 1
        top = np.argsort(-scores)[:k]
        return [(self.chunks[idx[t]], float(scores[t])) for t in top]
