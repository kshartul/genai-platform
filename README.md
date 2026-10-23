# Enterprise GenAI Platform

A reusable GenAI platform for AWS and Azure. It covers RAG, agentic workflows, semantic chunking,
embeddings and vector search, knowledge graphs, MCP integrations, guardrails and evaluation. A
new use case is a YAML profile plus a golden set, not a new stack.

- **Spec → sprints:** [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)
- **Architecture:** [`docs/architecture.md`](docs/architecture.md)
- **Decisions:** [`docs/adr/`](docs/adr)

## Quick start (offline, no cloud account)

```bash
pip install -e ".[dev]"
make test          # 28 tests: ingest, retrieval, guardrails, KG, agents, MCP, API, eval gate
make eval          # golden-set eval; exits 1 if any threshold regresses
make ask Q="How many unused leave days can I carry forward?"
genai agent --scopes kb.read,dw.read "What was the total revenue by region in 2025?"
make run           # API on :8000  (POST /answer, /retrieve, /agent/run, /agent/{id}/approve)
make mcp           # MCP server over stdio (add --http for streamable HTTP on :8001)
```

The offline profile uses a hashed-bag-of-words embedder and an extractive stand-in "LLM" so
that everything is deterministic and free in CI. Switch to real models by choosing the AWS or
Azure profile.

## Run on AWS or Azure

| | AWS | Azure |
| --- | --- | --- |
| Profile | `profiles/knowledge-assistant.aws.yaml` | `profiles/knowledge-assistant.azure.yaml` |
| Infra | `infra/terraform/envs/aws-dev` | `infra/terraform/envs/azure-dev` |
| Models | Amazon Bedrock (Converse, Titan v2) | Azure OpenAI |
| Vector + keyword | OpenSearch Serverless | Azure AI Search |
| Graph | Amazon Neptune (openCypher) | Neo4j |
| Guardrails | Bedrock Guardrails | Azure AI Content Safety |
| Compute | ECS Fargate + Lambda ingest | Container Apps + queue-triggered job |
| Install extra | `pip install -e ".[aws]"` | `pip install -e ".[azure]"` |

The pipeline (`pipelines/azure-pipelines.yml`) runs lint, tests, the offline eval gate, image
build, Terraform for both clouds, then a cloud eval that blocks promotion.

## Layout

```
src/genai_platform/
  config.py         use-case profiles (YAML -> typed config, ${ENV} expansion)
  platform.py       composition root: builds every module for a profile
  providers/        LLM gateway (fallback, retry, ACL-scoped semantic cache, cost) + Bedrock/Azure/local
  ingest/           parsers, chunkers (recursive, semantic, parent-child), PII, idempotent pipeline
  vectorstore/      memory, OpenSearch Serverless, Azure AI Search (hybrid + ACL filters)
  retrieval/        hybrid + RRF, rerank, corrective RAG, GraphRAG route, cited answers
  kg/               ontology, extractors (LLM / pattern), entity resolution, graph stores, Cypher guard
  agents/           LangGraph router, ReAct, plan-and-execute; approval gate; budgets
  mcp/              tool registry (scopes, validation, rate limit, audit) + platform tools
  guardrails/       input/output checks + Bedrock Guardrails / Content Safety
  evals/            metrics, golden-set runner, CI gate
services/           api (FastAPI), mcp_server (MCP), ingest_worker (Lambda / Container Apps job)
profiles/ ontology/ evals/golden/ data/   use-case config, ontology, golden sets, sample corpus
infra/terraform/    modules/{aws,azure} + envs/{aws-dev,azure-dev}
pipelines/          Azure DevOps YAML + templates
```
