"""Amazon OpenSearch Serverless (vector collection) store: k-NN (HNSW, faiss) + BM25 in one engine.

The index name carries the embedding model and version (e.g. ``kb-titan-v2-1024-v3``) so a
model change re-embeds into a new index and traffic switches by alias (blue/green).
"""

from __future__ import annotations

from ..types import Chunk, ScoredChunk
from .base import VectorStore

INDEX_BODY = {
    "settings": {"index": {"knn": True}},
    "mappings": {
        "properties": {
            "embedding": {"type": "knn_vector", "dimension": 1024,
                          "method": {"name": "hnsw", "engine": "faiss", "space_type": "innerproduct",
                                     "parameters": {"m": 16, "ef_construction": 256}}},
            "text": {"type": "text"}, "context_header": {"type": "text"},
            "acl": {"type": "keyword"}, "doc_id": {"type": "keyword"}, "content_hash": {"type": "keyword"},
            "source": {"type": "keyword"}, "section": {"type": "text"},
        }
    },
}


class OpenSearchVectorStore(VectorStore):
    def __init__(self, endpoint: str, index: str, region: str, dim: int = 1024, create: bool = True):
        import boto3
        from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

        auth = AWSV4SignerAuth(boto3.Session().get_credentials(), region, "aoss")
        self.client = OpenSearch(hosts=[{"host": endpoint.replace("https://", ""), "port": 443}], http_auth=auth,
                                 use_ssl=True, verify_certs=True, connection_class=RequestsHttpConnection,
                                 timeout=30)
        self.index = index
        if create and not self.client.indices.exists(index=index):
            body = INDEX_BODY.copy()
            body["mappings"]["properties"]["embedding"]["dimension"] = dim
            self.client.indices.create(index=index, body=body)

    def upsert(self, chunks: list[Chunk]) -> None:
        from opensearchpy.helpers import bulk

        actions = [{"_op_type": "index", "_index": self.index, "_id": c.id, **c.model_dump()} for c in chunks]
        bulk(self.client, actions)

    @staticmethod
    def _filter(acl, filters):
        f = [{"terms": {"acl": ["public", *(acl or [])]}}]
        f += [{"term": {f"metadata.{k}": v}} for k, v in (filters or {}).items()]
        return f

    def _hits(self, resp, retriever):
        return [ScoredChunk(chunk=Chunk.model_validate(h["_source"]), score=h["_score"], retriever=retriever)
                for h in resp["hits"]["hits"]]

    def vector_search(self, vector, k, acl=None, filters=None):
        q = {"size": k, "_source": {"excludes": ["embedding"]},
             "query": {"knn": {"embedding": {"vector": vector, "k": k,
                                             "filter": {"bool": {"filter": self._filter(acl, filters)}}}}}}
        return self._hits(self.client.search(index=self.index, body=q), "vector")

    def keyword_search(self, query, k, acl=None, filters=None):
        q = {"size": k, "_source": {"excludes": ["embedding"]},
             "query": {"bool": {"must": {"multi_match": {"query": query,
                                                         "fields": ["text", "context_header^0.5", "section"]}},
                                "filter": self._filter(acl, filters)}}}
        return self._hits(self.client.search(index=self.index, body=q), "bm25")

    def has_hash(self, content_hash):
        r = self.client.search(index=self.index, body={"size": 0, "query": {"term": {"content_hash": content_hash}}})
        return r["hits"]["total"]["value"] > 0

    def get(self, chunk_id):
        r = self.client.search(index=self.index, body={"query": {"ids": {"values": [chunk_id]}}})
        hits = r["hits"]["hits"]
        return Chunk.model_validate(hits[0]["_source"]) if hits else None

    def count(self):
        return self.client.count(index=self.index)["count"]
