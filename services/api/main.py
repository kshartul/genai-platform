"""Answer and agent API (FastAPI). Runs on ECS Fargate / Lambda (AWS) or Container Apps (Azure).

Auth: in cloud, API Gateway (Cognito/JWT) or Azure API Management (Entra ID) validates the token
and forwards ``X-User``, ``X-Groups`` and ``X-Scopes``; this service trusts only those headers
and must not be exposed directly.
"""

from __future__ import annotations

import os
import re

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from genai_platform.mcp import Principal
from genai_platform.observability import langfuse_trace, setup_logging
from genai_platform.platform import Platform

setup_logging()
PROFILE = os.environ.get("GENAI_PROFILE", "profiles/knowledge-assistant.local.yaml")
ROOT = os.environ.get("GENAI_ROOT", ".")

app = FastAPI(title="GenAI Platform API", version="0.1.0")
platform = Platform.from_profile(PROFILE, ROOT)
if os.environ.get("GENAI_INGEST_ON_START", "true").lower() == "true":
    platform.ingest()
_runners: dict[str, object] = {}


def _items(header: str | None) -> list[str]:
    # "a,b" from direct callers; API Gateway forwards the cognito:groups claim as "[a b]"
    return [s for s in re.split(r"[,\s]+", (header or "").strip().strip("[]")) if s]


def principal(x_user: str | None, x_groups: str | None, x_scopes: str | None) -> Principal:
    """Cognito groups named ``scope:<scope>`` grant tool scopes; all other groups are document ACLs."""
    groups = _items(x_groups)
    scopes = (_items(x_scopes) or ["kb.read"]) + [g.removeprefix("scope:") for g in groups if g.startswith("scope:")]
    return Principal(x_user or "anonymous", list(dict.fromkeys(scopes)),
                     [g for g in groups if not g.startswith("scope:")])


class AskIn(BaseModel):
    question: str
    filters: dict | None = None


class RetrieveIn(BaseModel):
    query: str
    top_n: int = 5


class AgentIn(BaseModel):
    question: str
    pattern: str | None = None


class ApproveIn(BaseModel):
    approved: bool


class IngestIn(BaseModel):
    paths: list[str] | None = None


@app.get("/healthz")
def healthz():
    return {"status": "ok", "profile": platform.profile.name, "chunks": platform.store.count()}


@app.post("/retrieve")
def retrieve(body: RetrieveIn, x_user: str | None = Header(None), x_groups: str | None = Header(None),
             x_scopes: str | None = Header(None)):
    p = principal(x_user, x_groups, x_scopes)
    hits = platform.retrieval.retrieve(body.query, acl=p.groups, top_n=body.top_n)
    return [{"chunk_id": h.chunk.id, "source": h.chunk.source, "section": h.chunk.section, "score": h.score,
             "retriever": h.retriever, "text": h.chunk.text} for h in hits]


@app.post("/answer")
def answer(body: AskIn, x_user: str | None = Header(None), x_groups: str | None = Header(None),
           x_scopes: str | None = Header(None)):
    p = principal(x_user, x_groups, x_scopes)
    a = platform.rag.answer(body.question, acl=p.groups, filters=body.filters)
    langfuse_trace("rag.answer", {"input": body.question, "output": a.answer, "trace_id": a.trace_id,
                                  "usage": a.usage, "guardrail": a.guardrail, "user": p.subject})
    out = a.model_dump()
    for c in out["contexts"]:
        c["chunk"].pop("embedding", None)
    return out


@app.post("/agent/run")
def agent_run(body: AgentIn, x_user: str | None = Header(None), x_groups: str | None = Header(None),
              x_scopes: str | None = Header(None)):
    runner = platform.agent(principal(x_user, x_groups, x_scopes), body.pattern)
    out = runner.run(body.question)
    if out["status"] == "awaiting_approval":
        _runners[out["thread_id"]] = runner
    return out


@app.post("/agent/{thread_id}/approve")
def agent_approve(thread_id: str, body: ApproveIn):
    runner = _runners.pop(thread_id, None)
    if runner is None:
        raise HTTPException(404, "no run awaiting approval with that id")
    return runner.resume(thread_id, body.approved)


@app.post("/ingest")
def ingest(body: IngestIn, x_scopes: str | None = Header(None)):
    if "kb.admin" not in (x_scopes or ""):
        raise HTTPException(403, "kb.admin scope required")
    return platform.ingest(body.paths).__dict__


@app.get("/tools")
def tools(x_user: str | None = Header(None), x_scopes: str | None = Header(None)):
    return [t.schema() for t in platform.registry.visible_to(principal(x_user, None, x_scopes))]
