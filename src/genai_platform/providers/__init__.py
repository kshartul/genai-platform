"""Model providers and the factory that builds them from a profile."""

from __future__ import annotations

from ..config import ModelConfig
from .base import LLM, Embedder, LLMResult, approx_tokens
from .gateway import LLMGateway
from .local import ExtractiveLLM, HashEmbedder

__all__ = ["LLM", "Embedder", "LLMGateway", "LLMResult", "approx_tokens", "build_embedder", "build_llm"]


def _one_llm(cfg: ModelConfig, model: str) -> LLM:
    if cfg.provider == "aws":
        from .aws import BedrockLLM

        return BedrockLLM(model, region=cfg.region)
    if cfg.provider == "azure":
        from .azure import AzureOpenAILLM

        return AzureOpenAILLM(model, endpoint=cfg.endpoint or "", api_version=cfg.api_version or "2024-10-21")
    return ExtractiveLLM(model)


def build_llm(cfg: ModelConfig, embedder: Embedder | None = None) -> LLMGateway:
    models = [_one_llm(cfg, m) for m in [cfg.chat_model, *cfg.fallback_models]]
    return LLMGateway(models, semantic_cache=embedder if cfg.cache else None)


def build_embedder(cfg: ModelConfig) -> Embedder:
    if cfg.provider == "aws":
        from .aws import BedrockEmbedder

        return BedrockEmbedder(cfg.embedding_model, dim=cfg.embedding_dim, region=cfg.region)
    if cfg.provider == "azure":
        from .azure import AzureOpenAIEmbedder

        return AzureOpenAIEmbedder(cfg.embedding_model, endpoint=cfg.endpoint or "", dim=cfg.embedding_dim,
                                   api_version=cfg.api_version or "2024-10-21")
    return HashEmbedder(cfg.embedding_dim, cfg.embedding_model)
