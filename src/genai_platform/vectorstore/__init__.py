from ..config import ModelConfig, VectorStoreConfig
from .base import VectorStore, acl_allows
from .memory import MemoryVectorStore

__all__ = ["MemoryVectorStore", "VectorStore", "acl_allows", "build_store"]


def build_store(cfg: VectorStoreConfig, models: ModelConfig) -> VectorStore:
    if cfg.kind == "opensearch":
        from .opensearch import OpenSearchVectorStore

        return OpenSearchVectorStore(cfg.endpoint or "", cfg.index, models.region or "us-east-1", models.embedding_dim)
    if cfg.kind == "azure_search":
        from .azure_search import AzureSearchVectorStore

        return AzureSearchVectorStore(cfg.endpoint or "", cfg.index)
    if cfg.kind == "pgvector":
        raise NotImplementedError("pgvector adapter is planned for Phase 1 sprint 3 (see docs/IMPLEMENTATION_PLAN.md)")
    return MemoryVectorStore(cfg.index, cfg.persist_path)
