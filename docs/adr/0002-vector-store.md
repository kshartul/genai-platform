# ADR 0002: Vector store per cloud behind one interface

- Status: Proposed
- Date: 2026-10-23

## Context
Hybrid retrieval (dense + BM25) with document-level ACL filtering is required in both clouds.

## Decision
`VectorStore` interface with three implementations: in-memory (tests/CI), Amazon OpenSearch Serverless (AWS default), Azure AI Search (Azure default). Fusion (RRF) and reranking live in `retrieval/`, not in the store, so behaviour is identical across clouds. Index names carry the embedding model and version; re-embedding uses blue/green indexes plus an alias switch.

## Consequences
- Native hybrid features (e.g. Azure semantic ranker) are used through the reranker option, not by changing the retrieval code.
- pgvector is an optional adapter for small or transactional corpora.
