"""In-memory vector + BM25 store for local development, tests and CI evals."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import numpy as np

from ..providers.local import tokenize
from ..types import Chunk, ScoredChunk
from .base import VectorStore, acl_allows


class MemoryVectorStore(VectorStore):
    def __init__(self, index: str = "kb-local", persist_path: str | None = None, k1: float = 1.5, b: float = 0.75):
        self.index = index
        self.persist_path = persist_path
        self.k1, self.b = k1, b
        self._chunks: dict[str, Chunk] = {}
        self._hashes: set[str] = set()
        self._tf: dict[str, Counter] = {}
        self._df: Counter = Counter()
        self._matrix: np.ndarray | None = None
        self._ids: list[str] = []
        if persist_path and Path(persist_path).exists():
            self.upsert([Chunk.model_validate(c) for c in json.loads(Path(persist_path).read_text())])

    def upsert(self, chunks: list[Chunk]) -> None:
        for c in chunks:
            if c.id in self._tf:
                self._df.subtract(set(self._tf[c.id]))
            self._chunks[c.id] = c
            self._hashes.add(c.content_hash)
            tf = Counter(tokenize(c.embed_text()))
            self._tf[c.id] = tf
            self._df.update(set(tf))
        self._matrix = None
        if self.persist_path:
            Path(self.persist_path).write_text(json.dumps([c.model_dump() for c in self._chunks.values()]))

    def _ensure_matrix(self):
        if self._matrix is None:
            self._ids = [cid for cid, c in self._chunks.items() if c.embedding is not None]
            self._matrix = np.array([self._chunks[i].embedding for i in self._ids], dtype=np.float32) \
                if self._ids else np.zeros((0, 1), dtype=np.float32)

    def _visible(self, c: Chunk, acl, filters) -> bool:
        if not acl_allows(c.acl, acl):
            return False
        return all(c.metadata.get(k) == v for k, v in (filters or {}).items())

    def vector_search(self, vector, k, acl=None, filters=None) -> list[ScoredChunk]:
        self._ensure_matrix()
        if not self._ids:
            return []
        q = np.array(vector, dtype=np.float32)
        sims = self._matrix @ q / (np.linalg.norm(self._matrix, axis=1) * (np.linalg.norm(q) or 1) + 1e-9)
        out = []
        for i in np.argsort(-sims):
            c = self._chunks[self._ids[i]]
            if self._visible(c, acl, filters):
                out.append(ScoredChunk(chunk=c, score=float(sims[i]), retriever="vector"))
                if len(out) >= k:
                    break
        return out

    def keyword_search(self, query, k, acl=None, filters=None) -> list[ScoredChunk]:
        q = tokenize(query)
        n = len(self._chunks) or 1
        avgdl = sum(sum(tf.values()) for tf in self._tf.values()) / n
        scores = []
        for cid, tf in self._tf.items():
            dl = sum(tf.values())
            s = 0.0
            for t in q:
                if t in tf:
                    idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                    s += idf * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * dl / avgdl))
            if s > 0:
                scores.append((s, cid))
        scores.sort(reverse=True)
        out = []
        for s, cid in scores:
            c = self._chunks[cid]
            if self._visible(c, acl, filters):
                out.append(ScoredChunk(chunk=c, score=s, retriever="bm25"))
                if len(out) >= k:
                    break
        return out

    def has_hash(self, content_hash: str) -> bool:
        return content_hash in self._hashes

    def get(self, chunk_id: str) -> Chunk | None:
        return self._chunks.get(chunk_id)

    def count(self) -> int:
        return len(self._chunks)
