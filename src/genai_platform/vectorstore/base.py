"""Vector store contract. Implementations: in-memory (dev/tests), OpenSearch Serverless (AWS),
Azure AI Search (Azure). Retrieval code only sees this interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import Chunk, ScoredChunk


class VectorStore(ABC):
    index: str

    @abstractmethod
    def upsert(self, chunks: list[Chunk]) -> None: ...

    @abstractmethod
    def vector_search(self, vector: list[float], k: int, acl: list[str] | None = None,
                      filters: dict | None = None) -> list[ScoredChunk]: ...

    @abstractmethod
    def keyword_search(self, query: str, k: int, acl: list[str] | None = None,
                       filters: dict | None = None) -> list[ScoredChunk]: ...

    @abstractmethod
    def has_hash(self, content_hash: str) -> bool: ...

    @abstractmethod
    def get(self, chunk_id: str) -> Chunk | None: ...

    @abstractmethod
    def count(self) -> int: ...


def acl_allows(chunk_acl: list[str], user_groups: list[str] | None) -> bool:
    """Document-level security: a chunk is visible if it is public or shares a group with the caller."""
    if "public" in chunk_acl:
        return True
    return bool(user_groups) and bool(set(chunk_acl) & set(user_groups))
