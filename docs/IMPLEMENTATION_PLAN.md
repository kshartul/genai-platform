# Implementation Plan — Enterprise GenAI Platform

This plan turns the *Enterprise GenAI Platform — Implementation Spec* into sprint-level work on
this repository. It keeps the spec's 6 phases (0–5) over 24 weeks and breaks them into 12
two-week sprints. Every story names the code it touches and the check that proves it is done.

**Where the repo stands today:** the platform skeleton is built end to end and runs offline. All
modules exist with a local implementation, the AWS and Azure adapters are written, and 28
tests plus the golden-set eval gate pass. Phases 0–5 below take each module from "works on
the sample corpus with stand-in models" to "in production on real data in both clouds".

---

## 1. Repository map (spec module → code)

| Spec module | Code | Local (tested) | AWS adapter | Azure adapter |
| --- | --- | --- | --- | --- |
| `ingest-pipeline` | `src/genai_platform/ingest/` | Markdown/HTML/PDF/DOCX parsers; recursive, semantic, parent-child chunkers; PII redaction; idempotent upserts | Textract parser, Comprehend PII, S3→EventBridge→SQS→Lambda (`services/ingest_worker`, `infra/.../aws/ingest`) | Document Intelligence parser, AI Language PII, Blob→Event Grid→Queue→Container Apps job |
| `embedding-service` | `src/genai_platform/providers/` | Hash embedder | Bedrock Titan v2 / Cohere | Azure OpenAI embeddings |
| Vector index | `src/genai_platform/vectorstore/` | In-memory dense + BM25, ACL filters | OpenSearch Serverless (k-NN + BM25) | Azure AI Search (vector + BM25, ACL filter) |
| `retrieval-service` | `src/genai_platform/retrieval/` | Hybrid + RRF, reranker, corrective RAG, GraphRAG route, citations | Bedrock Rerank | Azure semantic ranker (server-side) |
| `kg-builder` | `src/genai_platform/kg/`, `ontology/` | Ontology YAML, pattern extractor, entity resolution, graph retriever, Cypher guard | Neptune (openCypher / Bolt) | Neo4j (Cosmos DB Gremlin adapter: backlog) |
| `agent-runtime` | `src/genai_platform/agents/` | LangGraph router, ReAct, plan-and-execute; approval gate; step and cost budgets | Same code; Bedrock AgentCore hosting is an option | Same code; AI Foundry Agent Service is an option |
| `mcp-gateway` | `src/genai_platform/mcp/`, `services/mcp_server/` | Tool registry (scopes, validation, rate limit, audit), MCP server (stdio + streamable HTTP) | ECS container behind internal ALB | Container App (internal ingress) |
| `guardrail-layer` | `src/genai_platform/guardrails/` | Prompt-injection, denied topics, PII, groundedness, citation checks | Bedrock Guardrails (`ApplyGuardrail`, grounding) | Azure AI Content Safety |
| `eval-harness` | `src/genai_platform/evals/`, `evals/golden/` | Hit rate, MRR, context recall, faithfulness proxy, refusal, ACL leakage, agent tool accuracy, CI gate | Post-deploy cloud eval stage | Post-deploy cloud eval stage |
| LLM gateway | `src/genai_platform/providers/gateway.py` | Fallbacks, retries, exact and semantic cache (ACL-scoped), cost meter | Bedrock Converse | Azure OpenAI |
| IaC + CI/CD | `infra/terraform/`, `pipelines/` | — | 4 modules + `envs/aws-dev` | 4 modules + `envs/azure-dev` |

"Tested" means covered by `pytest` and the eval gate on the offline profile. Cloud adapters are
written to the SDKs' documented APIs but have not been run against live accounts. Exercising
them is the first job of each phase.

---

## 2. Team and ownership

| Role | People | Owns |
| --- | --- | --- |
| Solution architect / tech lead | 1 | ADRs, reviews, `config.py`, `platform.py`, phase gates |
| AI/LLM engineers | 2–3 | `retrieval/`, `agents/`, `kg/`, `guardrails/`, `evals/` |
| Data engineer | 1 | `ingest/`, connectors, `services/ingest_worker/` |
| Platform / DevOps engineer | 1 | `infra/terraform/`, `pipelines/`, observability, cost |
| Domain SMEs (part-time) | 2 | Golden sets, ontology, acceptance |

---

## 3. Sprint plan

Each story lists **Code**, **Owner** and **Done when**. A sprint is done when its stories meet
their checks and `make test eval` is green on `main`.

### Phase 0 — Foundation and discovery (weeks 1–2, Sprint 1)

| # | Story | Code | Owner | Done when |
| --- | --- | --- | --- | --- |
| 0.1 | Choose the lighthouse use case (knowledge assistant) and the agentic use case (warehouse Q&A) | `profiles/` | Tech lead | Two profiles agreed and committed |
| 0.2 | Build golden set v1: 50–100 real questions with expected answers and sources, plus refusal and ACL cases | `evals/golden/*.jsonl` | SMEs + AI eng | Set signed off; replaces the 20-case sample |
| 0.3 | Landing zone: VPC/VNet, private subnets, KMS, state backends, ECR/ACR, OIDC service connections | `infra/terraform/envs/*` | DevOps | `terraform plan` clean in both clouds |
| 0.4 | Pipeline live: CI, eval gate, image build | `pipelines/` | DevOps | First green run on `main` |
| 0.5 | ADRs 0001–0004 reviewed and accepted | `docs/adr/` | Tech lead | Status changed to Accepted |

**Gate:** golden set signed off · infra plans cleanly · pipeline green.

### Phase 1 — Ingestion, chunking, embeddings, vector search (weeks 3–7, Sprints 2–4)

| # | Story | Code | Owner | Done when |
| --- | --- | --- | --- | --- |
| 1.1 | Connect first real source (S3 or SharePoint) with ACL capture from the source system | `ingest/parsers.py`, new `ingest/connectors/` | Data eng | ACL groups on every chunk match the source |
| 1.2 | Run Textract and Document Intelligence parsers on scanned PDFs and table-heavy docs | `ingest/parsers.py` | Data eng | Tables survive as Markdown; parsing spot-check passes |
| 1.3 | Switch PII to `CloudPIIRedactor` in cloud profiles; add names/addresses tests | `ingest/pii.py` | AI eng | No PII from test fixtures appears in the index |
| 1.4 | Live test `BedrockEmbedder` and `AzureOpenAIEmbedder`; batch limits, retries, throttling | `providers/aws.py`, `providers/azure.py` | AI eng | 10k-chunk backfill completes without errors |
| 1.5 | OpenSearch index creation and alias-based blue/green re-index | `vectorstore/opensearch.py` | AI eng | Model swap re-embeds to a new index; alias flips with zero downtime |
| 1.6 | Azure AI Search index schema script (vector profile, ACL field, semantic config) | `scripts/create_azure_index.py` (new) | AI eng | Index created by pipeline; `AzureSearchVectorStore` round-trips a chunk |
| 1.7 | pgvector adapter for small/transactional corpora | `vectorstore/pgvector.py` (new) | AI eng | Same store contract tests pass |
| 1.8 | Chunking bake-off on the real golden set | `scripts/chunk_bakeoff.py` | AI eng | Winner per corpus recorded in profile + ADR note |
| 1.9 | Event-driven ingest live in dev (S3/EventBridge/SQS/Lambda; Blob/Event Grid/Queue/job) | `services/ingest_worker/`, `infra/.../ingest` | DevOps + Data eng | New file searchable in under 15 min; DLQ alarms wired |

**Gate:** one corpus fully indexed in each cloud · hit rate@5 ≥ 0.80 · incremental re-index < 15 min.

### Phase 2 — RAG, guardrails and evaluation harness (weeks 8–12, Sprints 5–6, plus one week)

| # | Story | Code | Owner | Done when |
| --- | --- | --- | --- | --- |
| 2.1 | Real models through the gateway; set `prices_per_1k` from the rate card; streaming responses | `providers/gateway.py`, `services/api/main.py` | AI eng | `/answer` streams; cost per answer reported |
| 2.2 | Compare naive, advanced (rewrite + hybrid + rerank) and corrective RAG on the golden set | `retrieval/service.py` | AI eng | Comparison table in `docs/`; best config in profiles |
| 2.3 | Bedrock Guardrails + Content Safety live; tune grounding threshold against human labels | `guardrails/`, `infra/.../bedrock` | AI eng | False-block rate < 2% on golden set; red-team set passes |
| 2.4 | LLM-judged metrics: RAGAS faithfulness/relevancy nightly, calibrated against 50 human-labelled answers | `evals/__init__.py`, `pipelines/evals-nightly.yml` (new) | AI eng | Judge agrees with humans ≥ 85% |
| 2.5 | Tracing: Langfuse/LangSmith + OpenTelemetry to CloudWatch / Azure Monitor; cost dashboard | `observability.py` | DevOps | Every request traceable from UI to retrieved chunk ids |
| 2.6 | Move semantic cache to ElastiCache / Azure Cache for Redis (keeps ACL scoping) | `providers/gateway.py` | AI eng | Cache hit rate and zero cross-scope hits reported |
| 2.7 | Pilot with 20–50 users; feedback capture (thumbs, comments) into eval backlog | `services/api/` | Tech lead | Feedback flows into golden-set candidates weekly |

**Gate:** faithfulness ≥ 0.85 (LLM-judged) · p95 TTFT < 2.5 s · red-team passes · pilot ≥ 4/5.

### Phase 3 — Entity extraction, ontology and knowledge graph (weeks 13–16, Sprints 7–8)

| # | Story | Code | Owner | Done when |
| --- | --- | --- | --- | --- |
| 3.1 | Ontology v1 with domain owners (10–20 entity types, 15–30 relations) | `ontology/enterprise.yaml` | SMEs + AI eng | Reviewed and versioned |
| 3.2 | `LLMExtractor` on real models; label 200 entities/relations for scoring | `kg/__init__.py`, `evals/golden/kg-*.jsonl` (new) | AI eng | Entity F1 ≥ 0.80 |
| 3.3 | Neptune (AWS) and Neo4j (Azure) stores live; Cosmos DB Gremlin adapter if chosen | `kg/__init__.py` | AI eng + DevOps | Same graph tests pass against each store |
| 3.4 | GraphRAG global search: community summaries for "across the estate" questions | `kg/communities.py` (new) | AI eng | Multi-hop golden questions improve ≥ 15% vs vector-only |
| 3.5 | Guarded text-to-Cypher route using `validate_cypher` | `kg/`, `retrieval/service.py` | AI eng | Zero write queries reach the store in red-team tests |

**Gate:** entity F1 ≥ 0.80 · multi-hop accuracy +15% over vector-only RAG.

### Phase 4 — Agentic workflows and MCP integrations (weeks 17–21, Sprints 9–10, plus one week)

| # | Story | Code | Owner | Done when |
| --- | --- | --- | --- | --- |
| 4.1 | ReAct and plan-and-execute on real models; tune tool descriptions | `agents/patterns.py` | AI eng | Agent eval suite task success ≥ 80% |
| 4.2 | Supervisor pattern with sub-agents via `agent_as_tool` | `agents/patterns.py` | AI eng | Multi-domain eval cases pass |
| 4.3 | Warehouse tool on Redshift Data API / Synapse with a read-only role; LLM text-to-SQL with schema + examples | `mcp/tools.py`, `platform.py` | Data eng | SQL guard + read-only role both enforced |
| 4.4 | MCP gateway at the edge: OAuth 2.1 (Cognito / Entra ID) → scopes and groups mapping | `services/mcp_server/`, API GW / APIM | DevOps | Token scopes decide visible tools |
| 4.5 | Third MCP server: ticketing or another enterprise API (write tool behind approval) | `mcp/tools.py` | AI eng | Approval flow tested end to end |
| 4.6 | Durable approvals: persist LangGraph checkpoints (DynamoDB / Cosmos DB) instead of memory | `agents/__init__.py` | AI eng | Approvals survive restarts |

**Gate:** task success ≥ 80% · zero unapproved writes in red-team · tools discovered from the registry.

### Phase 5 — Production hardening, scale and reuse (weeks 22–24, Sprints 11–12)

| # | Story | Code | Owner | Done when |
| --- | --- | --- | --- | --- |
| 5.1 | Load test to the NFRs (Locust/k6) and tune autoscaling | `tests/load/` (new), `infra/` | DevOps | 500 concurrent users; p95 targets met |
| 5.2 | Prod environments (`envs/aws-prod`, `envs/azure-prod`) with approvals in the pipeline | `infra/`, `pipelines/` | DevOps | Prod deploys only after dev cloud eval passes |
| 5.3 | Online eval on sampled traffic; drift and cost alerts; runbooks; on-call | `evals/`, `docs/runbooks/` | AI eng + DevOps | Alerts fire in game-day test |
| 5.4 | Package as versioned library + Terraform modules; profile template | `pyproject.toml`, `infra/terraform/modules` | Tech lead | v1.0.0 tagged |
| 5.5 | Onboard use case #3 using config only | `profiles/<new>.yaml`, `evals/golden/<new>.jsonl` | AI eng | Pilot live in under 2 weeks |

**Gate:** NFRs met · use case #3 live in < 2 weeks · docs and ADRs published.

---

## 4. Ways of working

- **Branching:** trunk-based; short-lived feature branches; PR needs one review plus green CI and eval gate.
- **Release path:** PR → CI → offline eval gate → image → dev (AWS + Azure) → cloud eval → prod (manual approval).
- **What triggers a full eval:** any change to `profiles/`, `retrieval/prompts.py`, `ontology/`, chunking, models, or guardrail settings.
- **Prompt changes:** bump `PROMPT_VERSION`; the eval report records it.
- **Definition of done (every story):** tests added, eval gate green, docs or ADR updated if behaviour changed, dashboards show the new path.

## 5. Onboarding a new use case (target: under 2 weeks)

1. Copy `profiles/knowledge-assistant.local.yaml` to `profiles/<use-case>.<cloud>.yaml`; set sources, chunking, retrieval mode, guardrail policy and agent tools.
2. Write `evals/golden/<use-case>.jsonl` with SMEs: 50+ cases including refusal and ACL cases.
3. Run `make eval PROFILE=...` locally, then open a PR. The pipeline gates it.
4. Add new tools (if any) in `mcp/tools.py` with scopes and kind; write tools get approval automatically.

## 6. Known gaps and backlog

| Item | Why it matters | Planned |
| --- | --- | --- |
| Cloud adapters not yet exercised against live accounts | They follow SDK docs but are untested | Phase 1–2 first sprint stories |
| Terraform not yet `validate`d against providers (syntax-checked only) | Argument names can drift between provider versions | Sprint 1 (CI job `terraform_validate`) |
| KEDA `azure-queue` scaler auth via managed identity | Support depends on azurerm/Container Apps version | Story 1.9 |
| Offline faithfulness is a lexical proxy | The local stand-in model only quotes sources, so it scores ~1.0 | Story 2.4 (LLM-judged) |
| Chunking bake-off can't separate strategies on the tiny sample corpus | Needs real documents | Story 1.8 |
| pgvector, Cosmos DB Gremlin adapters | Optional stores | Stories 1.7, 3.3 |
| In-memory checkpoints and semantic cache | Lost on restart; not shared across replicas | Stories 2.6, 4.6 |
