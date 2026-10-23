"""Ingestion pipeline: parse -> redact -> chunk -> embed -> upsert (-> extract entities).

Idempotent by content hash: unchanged chunks are skipped, so the same code serves the
initial backfill (Step Functions Map / Durable Functions fan-out) and event-driven updates
(S3 + EventBridge / Blob + Event Grid).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..providers.base import Embedder
from ..types import Chunk, Document
from ..vectorstore.base import VectorStore
from .parsers import parse_path
from .pii import PIIRedactor

log = logging.getLogger(__name__)


@dataclass
class IngestStats:
    documents: int = 0
    chunks: int = 0
    embedded: int = 0
    skipped_unchanged: int = 0
    pii_redactions: int = 0
    entities: int = 0
    errors: list[str] = field(default_factory=list)


class IngestPipeline:
    def __init__(self, chunker, embedder: Embedder, store: VectorStore, *, redactor: PIIRedactor | None = None,
                 kg_builder=None, batch_size: int = 32):
        self.chunker, self.embedder, self.store = chunker, embedder, store
        self.redactor, self.kg, self.batch = redactor, kg_builder, batch_size

    def ingest_documents(self, docs: list[Document]) -> IngestStats:
        stats = IngestStats()
        pending: list[Chunk] = []
        for doc in docs:
            stats.documents += 1
            try:
                if self.redactor:
                    doc.text, found = self.redactor.redact(doc.text)
                    stats.pii_redactions += len(found)
                chunks = self.chunker.chunk(doc)
                stats.chunks += len(chunks)
                for c in chunks:
                    if self.store.has_hash(c.content_hash):
                        stats.skipped_unchanged += 1
                    else:
                        pending.append(c)
                if self.kg is not None:
                    stats.entities += self.kg.add_chunks(chunks)
            except Exception as e:  # keep the batch going; report per document
                log.exception("failed to ingest %s", doc.source)
                stats.errors.append(f"{doc.source}: {e}")
        for i in range(0, len(pending), self.batch):
            batch = pending[i : i + self.batch]
            vecs = self.embedder.embed([c.embed_text() for c in batch])
            for c, v in zip(batch, vecs):
                c.embedding = v
            self.store.upsert(batch)
            stats.embedded += len(batch)
        return stats

    def ingest_paths(self, paths: list[str]) -> IngestStats:
        docs: list[Document] = []
        for p in paths:
            docs.extend(parse_path(p))
        return self.ingest_documents(docs)
