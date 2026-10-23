"""Composition root: builds every module for one use-case profile.

    platform = Platform.from_profile("profiles/knowledge-assistant.local.yaml")
    platform.ingest()                      # parse, chunk, embed, index, extract entities
    platform.rag.answer("How do I ...?")   # grounded, cited answer
    platform.agent().run("How many ...?")  # agent over the governed tool registry
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .agents import BUILDERS, AgentRunner, Budget
from .config import Profile, load_profile
from .guardrails import Guardrails
from .ingest import IngestPipeline, PIIRedactor, build_chunker
from .kg import GraphRetriever, InMemoryGraph, KGBuilder, LLMExtractor, Ontology, PatternExtractor
from .mcp import Principal, ToolRegistry, register_platform_tools
from .providers import build_embedder, build_llm
from .providers.local import tokenize
from .retrieval import RAGService, RetrievalService
from .vectorstore import build_store

TEXT_TO_SQL = """Write ONE read-only SQLite SELECT that answers the question.
Schema:
{schema}
Examples:
{examples}
Question: {question}
Return JSON: {{"sql": "..."}}"""


class Platform:
    def __init__(self, profile: Profile, root: str | Path = "."):
        self.profile = profile
        self.root = Path(root)
        p = profile
        self.embedder = build_embedder(p.models)
        self.llm = build_llm(p.models, self.embedder)
        self.store = build_store(p.vectorstore, p.models)
        self.guardrails = Guardrails(p.guardrails, p.models.region, p.models.endpoint)
        self.graph = None
        self.kg_builder = None
        self.graph_retriever = None
        if p.kg.enabled:
            ont = Ontology.load(self.root / p.kg.ontology_path)
            extractor = PatternExtractor(ont) if p.models.provider == "local" else LLMExtractor(self.llm, ont)
            if p.kg.store in ("neo4j", "neptune"):
                from .kg import Neo4jGraph

                self.graph = Neo4jGraph(p.kg.uri or "")
            else:
                self.graph = InMemoryGraph()
            self.kg_builder = KGBuilder(extractor, self.graph)
            self.graph_retriever = GraphRetriever(self.graph, self.store)
        self.retrieval = RetrievalService(p, self.store, self.embedder, self.graph_retriever)
        self.rag = RAGService(p, self.retrieval, self.llm, self.guardrails)
        self.pipeline = IngestPipeline(build_chunker(p.chunking, self.embedder), self.embedder, self.store,
                                       redactor=PIIRedactor() if p.chunking.redact_pii else None,
                                       kg_builder=self.kg_builder)
        self.registry = ToolRegistry()
        wh = p.prompts.get("warehouse_path")
        register_platform_tools(self.registry, self, str(self.root / wh) if wh else None,
                                ticket_path=str(self.root / ".data/tickets.jsonl"))

    @classmethod
    def from_profile(cls, path: str | Path, root: str | Path = ".") -> Platform:
        return cls(load_profile(Path(root) / path), root)

    def ingest(self, paths: list[str] | None = None):
        return self.pipeline.ingest_paths([str(self.root / x) for x in (paths or self.profile.corpus_paths)])

    # ---------- agents
    def _text_to_sql(self, question: str) -> dict:
        examples = json.loads(self.profile.prompts.get("sql_examples", "[]"))
        if self.profile.models.provider == "local":
            # offline: pick the closest curated example (query-template library)
            q = set(tokenize(question))
            best = max(examples, key=lambda e: len(q & set(tokenize(e["question"]))), default=None)
            return {"sql": best["sql"]} if best else {"sql": "SELECT 1"}
        schema = self.registry.call("warehouse_schema", {}, Principal("system", ["dw.read"]))
        prompt = TEXT_TO_SQL.format(schema=json.dumps(schema), question=question,
                                    examples="\n".join(f"Q: {e['question']}\nSQL: {e['sql']}" for e in examples))
        raw = self.llm.generate([{"role": "user", "content": prompt}], json_mode=True, max_tokens=300).text
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(m.group(0)) if m else {"sql": raw}

    def agent(self, principal: Principal | None = None, pattern: str | None = None) -> AgentRunner:
        p = self.profile.agent
        principal = principal or Principal("anonymous", ["kb.read"], [])
        budget = Budget(self.llm, p.max_steps, p.max_cost_usd)
        builder = BUILDERS[pattern or p.pattern]
        if builder is BUILDERS["router"]:
            graph = builder(self.registry, principal, self.llm, budget=budget,
                            arg_builder=lambda route, q: self._text_to_sql(q) if route == "query_warehouse" else None)
        else:
            graph = builder(self.registry, principal, self.llm, budget=budget)
        return AgentRunner(graph)
