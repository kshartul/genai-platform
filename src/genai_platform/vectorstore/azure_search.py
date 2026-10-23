"""Azure AI Search store: HNSW vector field + BM25 text, security trimming on an ``acl`` collection.

The index schema itself is created by Terraform/Bicep or ``scripts/create_azure_index.py``;
this class only reads and writes documents.
"""

from __future__ import annotations

import json

from ..types import Chunk, ScoredChunk
from .base import VectorStore


class AzureSearchVectorStore(VectorStore):
    def __init__(self, endpoint: str, index: str):
        from azure.identity import DefaultAzureCredential
        from azure.search.documents import SearchClient

        self.index = index
        self.client = SearchClient(endpoint, index, DefaultAzureCredential())

    @staticmethod
    def _to_doc(c: Chunk) -> dict:
        d = c.model_dump(exclude={"metadata", "parent_text"})
        d["metadata_json"] = json.dumps(c.metadata)
        d["parent_text"] = c.parent_text or ""
        return d

    @staticmethod
    def _from_doc(d: dict) -> Chunk:
        d = {k: v for k, v in d.items() if not k.startswith("@")}
        d["metadata"] = json.loads(d.pop("metadata_json", "{}") or "{}")
        return Chunk.model_validate(d)

    @staticmethod
    def _filter(acl, filters) -> str:
        groups = ",".join(["public", *(acl or [])])
        parts = [f"acl/any(g: search.in(g, '{groups}'))"]
        parts += [f"{k} eq '{v}'" for k, v in (filters or {}).items()]
        return " and ".join(parts)

    def upsert(self, chunks):
        self.client.merge_or_upload_documents([self._to_doc(c) for c in chunks])

    def vector_search(self, vector, k, acl=None, filters=None):
        from azure.search.documents.models import VectorizedQuery

        r = self.client.search(search_text=None, filter=self._filter(acl, filters), top=k,
                               vector_queries=[VectorizedQuery(vector=vector, k_nearest_neighbors=k,
                                                               fields="embedding")])
        return [ScoredChunk(chunk=self._from_doc(d), score=d["@search.score"], retriever="vector") for d in r]

    def keyword_search(self, query, k, acl=None, filters=None):
        r = self.client.search(search_text=query, filter=self._filter(acl, filters), top=k)
        return [ScoredChunk(chunk=self._from_doc(d), score=d["@search.score"], retriever="bm25") for d in r]

    def has_hash(self, content_hash):
        return self.client.get_document_count() > 0 and any(
            True for _ in self.client.search(search_text="*", filter=f"content_hash eq '{content_hash}'", top=1))

    def get(self, chunk_id):
        try:
            return self._from_doc(self.client.get_document(chunk_id))
        except Exception:  # noqa: BLE001
            return None

    def count(self):
        return self.client.get_document_count()
