# ADR 0004: Evaluation gates every release

- Status: Proposed
- Date: 2026-10-23

## Context
Changes to prompts, chunking, models, ontology or guardrails can silently reduce answer quality or leak data.

## Decision
Every use case has a golden set (`evals/golden/*.jsonl`) with RAG, refusal, ACL-leak and agent cases. The pipeline runs `genai eval` offline on every PR and against real cloud deployments after each dev deploy. Any metric below its profile threshold fails the stage. LLM-judged metrics (RAGAS) run nightly and are calibrated against human labels.

## Consequences
- Golden sets are product assets owned with domain SMEs and grow from pilot feedback.
- Offline metrics guard plumbing (retrieval, ACLs, guardrails, routing). Answer-quality thresholds from the spec are enforced on the cloud profiles.
