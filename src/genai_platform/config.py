"""Use-case profiles: a new use case is a YAML file, not a new stack.

A profile selects the cloud, models, chunker, vector store, retriever, guardrail policy,
agent pattern and eval thresholds. Environment variables in ``${VAR}`` or ``${VAR:-default}``
form are expanded so secrets and endpoints never live in the file.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Cloud = Literal["local", "aws", "azure"]

_ENV = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _expand(value):
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


class ModelConfig(BaseModel):
    provider: Cloud = "local"
    chat_model: str = "local-extractive"
    fallback_models: list[str] = Field(default_factory=list)
    embedding_model: str = "local-hash-384"
    embedding_dim: int = 384
    region: str | None = None
    endpoint: str | None = None
    api_version: str | None = None
    temperature: float = 0.0
    max_tokens: int = 800
    cache: bool = True


class ChunkingConfig(BaseModel):
    strategy: Literal["recursive", "semantic", "parent_child"] = "recursive"
    target_tokens: int = 400
    overlap_tokens: int = 50
    semantic_breakpoint_percentile: float = 90.0
    child_tokens: int = 120
    contextual_headers: bool = True
    redact_pii: bool = True


class VectorStoreConfig(BaseModel):
    kind: Literal["memory", "opensearch", "azure_search", "pgvector"] = "memory"
    index: str = "kb-local-hash-384-v1"
    endpoint: str | None = None
    persist_path: str | None = None


class RetrievalConfig(BaseModel):
    mode: Literal["vector", "hybrid", "graph", "hybrid+graph"] = "hybrid"
    top_k: int = 20
    rerank_top_n: int = 5
    rrf_k: int = 60
    reranker: Literal["none", "lexical", "cohere", "bedrock", "azure_semantic"] = "lexical"
    query_rewrite: bool = False
    corrective: bool = True
    min_relevance: float = 0.15


class GuardrailConfig(BaseModel):
    provider: Literal["local", "bedrock", "azure_content_safety"] = "local"
    guardrail_id: str | None = None
    guardrail_version: str | None = None
    max_input_chars: int = 4000
    denied_topics: list[str] = Field(default_factory=list)
    block_prompt_injection: bool = True
    redact_pii: bool = True
    min_groundedness: float = 0.6
    require_citations: bool = True


class KGConfig(BaseModel):
    enabled: bool = False
    ontology_path: str = "ontology/enterprise.yaml"
    store: Literal["memory", "neo4j", "neptune", "cosmos_gremlin"] = "memory"
    uri: str | None = None


class AgentConfig(BaseModel):
    pattern: Literal["router", "react", "plan_execute", "supervisor"] = "router"
    max_steps: int = 6
    max_cost_usd: float = 0.50
    approval_required_for: list[str] = Field(default_factory=lambda: ["write"])
    tools: list[str] = Field(default_factory=lambda: ["search_knowledge"])


class EvalConfig(BaseModel):
    golden_set: str = "evals/golden/knowledge-assistant.jsonl"
    thresholds: dict[str, float] = Field(
        default_factory=lambda: {"faithfulness": 0.85, "context_recall": 0.80, "hit_rate@5": 0.80}
    )


class Profile(BaseModel):
    name: str
    description: str = ""
    corpus_paths: list[str] = Field(default_factory=list)
    models: ModelConfig = Field(default_factory=ModelConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    vectorstore: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    guardrails: GuardrailConfig = Field(default_factory=GuardrailConfig)
    kg: KGConfig = Field(default_factory=KGConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    evals: EvalConfig = Field(default_factory=EvalConfig)
    prompts: dict[str, str] = Field(default_factory=dict)


def load_profile(path: str | Path) -> Profile:
    data = yaml.safe_load(Path(path).read_text())
    return Profile.model_validate(_expand(data))
