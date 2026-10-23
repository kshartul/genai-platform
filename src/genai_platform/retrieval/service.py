"""``retrieval-service``: hybrid retrieval, GraphRAG routing, reranking, corrective RAG and
grounded, cited answers behind input and output guardrails."""

from __future__ import annotations

import logging
import re
import time
import uuid

from ..config import Profile
from ..guardrails import Guardrails
from ..providers.base import Embedder
from ..types import Answer, Citation, ScoredChunk
from ..vectorstore.base import VectorStore
from . import prompts
from .fusion import build_reranker, reciprocal_rank_fusion

log = logging.getLogger(__name__)


class RetrievalService:
    def __init__(self, profile: Profile, store: VectorStore, embedder: Embedder, graph_retriever=None):
        self.p = profile
        self.cfg = profile.retrieval
        self.store, self.embedder, self.graph = store, embedder, graph_retriever
        self.reranker = build_reranker(self.cfg.reranker, profile.models.region)

    def retrieve(self, query: str, acl: list[str] | None = None, filters: dict | None = None,
                 top_n: int | None = None) -> list[ScoredChunk]:
        top_n = top_n or self.cfg.rerank_top_n
        lists: list[list[ScoredChunk]] = []
        if self.cfg.mode in ("vector", "hybrid", "hybrid+graph"):
            qv = self.embedder.embed([query], kind="query")[0]
            lists.append(self.store.vector_search(qv, self.cfg.top_k, acl, filters))
        if self.cfg.mode in ("hybrid", "hybrid+graph"):
            lists.append(self.store.keyword_search(query, self.cfg.top_k, acl, filters))
        if self.graph is not None and self.cfg.mode in ("graph", "hybrid+graph"):
            _, gchunks = self.graph.retrieve(query, self.cfg.top_k, acl)
            if gchunks:
                lists.append(gchunks)
        fused = reciprocal_rank_fusion(lists, self.cfg.rrf_k) if len(lists) > 1 else (lists[0] if lists else [])
        if self.reranker is not None:
            return self.reranker.rerank(query, fused[: self.cfg.top_k], top_n)
        return fused[:top_n]


class RAGService:
    def __init__(self, profile: Profile, retrieval: RetrievalService, llm, guardrails: Guardrails):
        self.p, self.r, self.llm, self.g = profile, retrieval, llm, guardrails
        self.system = profile.prompts.get("rag_system", prompts.RAG_SYSTEM)
        self.user_tpl = profile.prompts.get("rag_user", prompts.RAG_USER)

    def _rewrite(self, q: str) -> str:
        if not self.p.retrieval.query_rewrite:
            return q
        out = self.llm.generate([{"role": "user", "content": prompts.QUERY_REWRITE.format(question=q)}],
                                max_tokens=80).text.strip()
        return out or q

    def answer(self, question: str, acl: list[str] | None = None, filters: dict | None = None) -> Answer:
        trace_id = uuid.uuid4().hex
        t0 = time.perf_counter()
        gin = self.g.check_input(question)
        if not gin.allowed:
            return Answer(question=question, answer="Request blocked by input guardrails.", trace_id=trace_id,
                          guardrail={"input": gin.as_dict()}, blocked=True)
        q = self._rewrite(gin.text)
        ctx = self.r.retrieve(q, acl, filters)
        corrective = False
        if self.p.retrieval.corrective and (not ctx or ctx[0].score < self.p.retrieval.min_relevance):
            # Corrective RAG: weak evidence -> widen the search once with keyword-only and a rewritten query
            corrective = True
            alt = self.r.store.keyword_search(q, self.p.retrieval.top_k, acl, filters)
            if alt and self.r.reranker:
                alt = self.r.reranker.rerank(q, alt, self.p.retrieval.rerank_top_n)
            ctx = alt or ctx
        if not ctx:
            text = "I don't know based on the provided sources."
        else:
            msgs = [{"role": "system", "content": self.system},
                    {"role": "user", "content": self.user_tpl.format(context=prompts.format_context(ctx), question=q)}]
            text = self.llm.generate(msgs, max_tokens=self.p.models.max_tokens,
                                     temperature=self.p.models.temperature, semantic_key=q,
                                     cache_scope=f"acl={sorted(acl or [])}|filters={sorted((filters or {}).items())}"
                                     ).text.strip()
        gout = self.g.check_output(text, ctx, q)
        if not gout.allowed and "low_groundedness" in gout.reasons:
            text = "I don't know based on the provided sources."
        else:
            text = gout.text
        cited = sorted({int(x) for x in re.findall(r"\[(\d+)\]", text)})
        citations = [Citation(chunk_id=ctx[i - 1].chunk.id, source=ctx[i - 1].chunk.source,
                              section=ctx[i - 1].chunk.section) for i in cited if 0 < i <= len(ctx)]
        usage = self.llm.usage.snapshot() if hasattr(self.llm, "usage") else {}
        usage["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        usage["corrective"] = corrective
        usage["prompt_version"] = prompts.PROMPT_VERSION
        log.info("rag trace=%s ctx=%d cited=%d latency_ms=%s", trace_id, len(ctx), len(citations), usage["latency_ms"])
        return Answer(question=question, answer=text, citations=citations, contexts=ctx, trace_id=trace_id,
                      guardrail={"input": gin.as_dict(), "output": gout.as_dict()}, usage=usage)
