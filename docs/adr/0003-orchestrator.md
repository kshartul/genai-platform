# ADR 0003: LangGraph for agent orchestration

- Status: Proposed
- Date: 2026-10-23

## Context
Agent workflows need explicit state, human-in-the-loop interrupts, checkpointing and testable control flow, in both clouds.

## Decision
Use LangGraph for all agent patterns (router, ReAct, plan-and-execute, supervisor). Tools come only from the platform `ToolRegistry`, so scopes, validation, audit and approval apply regardless of pattern. Bedrock AgentCore or Azure AI Foundry Agent Service can host these graphs, but they do not replace the registry.

## Consequences
- Same agent code runs on ECS and Container Apps.
- Checkpoints must move to a durable store (DynamoDB / Cosmos DB) before production (story 4.6).
