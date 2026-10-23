# ADR 0001: One LLM gateway for all model calls

- Status: Proposed
- Date: 2026-10-23

## Context
Use cases call chat and embedding models on Bedrock and Azure OpenAI. Without a single entry point, retries, fallbacks, caching and cost tracking get re-implemented per team.

## Decision
All model calls go through `providers.LLMGateway` (in-process library), built from the profile. It provides ordered fallbacks, retries with backoff, exact and semantic caching, and per-call cost metering from a configured rate card. The semantic cache is scoped by the caller's ACL groups and filters.

## Consequences
- Swapping models is a profile change, gated by the eval suite.
- A shared cache store (Redis) is needed for multi-replica deployments (story 2.6).
- If the organisation adopts a network gateway (e.g. LiteLLM proxy, APIM AI gateway), the library calls it as just another provider.
