"""Entity extraction, ontology and knowledge graph (``kg-builder``).

Flow: ontology (YAML, versioned) -> extractor (LLM structured output, or local pattern
extractor) -> entity resolution -> graph store (memory / Neo4j / Neptune openCypher) with every
node and edge linked back to the chunk ids it came from. ``GraphRetriever`` turns a question
into entity-anchored facts plus the chunks behind them (GraphRAG local search).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from ..providers.base import Embedder
from ..types import Chunk, ScoredChunk

# ---------------------------------------------------------------- ontology


@dataclass
class RelationType:
    name: str
    domain: list[str]
    range: list[str]
    triggers: list[str]


@dataclass
class Ontology:
    version: str
    entity_types: dict[str, dict]
    relations: dict[str, RelationType]

    @classmethod
    def load(cls, path: str | Path) -> Ontology:
        d = yaml.safe_load(Path(path).read_text())
        rels = {k: RelationType(k, v["domain"], v["range"], v.get("triggers", [])) for k, v in d["relations"].items()}
        return cls(str(d.get("version", "1")), d["entity_types"], rels)

    def json_schema(self) -> dict:
        """Schema handed to the LLM for structured extraction."""
        return {
            "type": "object",
            "properties": {
                "entities": {"type": "array", "items": {"type": "object", "properties": {
                    "name": {"type": "string"}, "type": {"enum": list(self.entity_types)}}}},
                "relations": {"type": "array", "items": {"type": "object", "properties": {
                    "source": {"type": "string"}, "relation": {"enum": list(self.relations)},
                    "target": {"type": "string"}, "confidence": {"type": "number"}}}},
            },
        }


# ---------------------------------------------------------------- extraction


@dataclass
class Entity:
    name: str
    type: str


@dataclass
class Relation:
    source: str
    relation: str
    target: str
    confidence: float = 0.7


@dataclass
class Extraction:
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)


class PatternExtractor:
    """Deterministic extractor driven by the ontology's ``patterns`` and relation ``triggers``.
    Good for high-volume, well-formed types and for offline tests."""

    def __init__(self, ontology: Ontology):
        self.ont = ontology
        self.patterns = {t: [re.compile(p) for p in spec.get("patterns", [])] for t, spec in ontology.entity_types.items()}

    def extract(self, text: str) -> Extraction:
        out = Extraction()
        for sent in re.split(r"(?<=[.!?])\s+", text):
            mentions: list[tuple[int, int, Entity]] = []
            for etype, pats in self.patterns.items():
                for p in pats:
                    for m in p.finditer(sent):
                        name = m.group(1) if m.groups() else m.group(0)
                        name = re.sub(r"^(The|A|An)\s+", "", name.strip())
                        if not any(s <= m.start() < e for s, e, _ in mentions):
                            mentions.append((m.start(), m.end(), Entity(name.strip(), etype)))
            mentions.sort(key=lambda x: x[0])
            out.entities += [m[2] for m in mentions]
            for i, (_, e1_end, e1) in enumerate(mentions):
                for s2, _, e2 in mentions[i + 1 :]:
                    between = sent[e1_end:s2].lower()
                    for rel in self.ont.relations.values():
                        if e1.type in rel.domain and e2.type in rel.range and any(t in between for t in rel.triggers):
                            out.relations.append(Relation(e1.name, rel.name, e2.name, 0.8))
        return out


class LLMExtractor:
    """Structured-output extraction with the ontology as the JSON schema; invalid types are dropped."""

    PROMPT = (
        "Extract entities and relations from the text using ONLY this ontology.\n"
        "Ontology (JSON schema): {schema}\nEntity type descriptions: {desc}\n"
        "Return JSON with keys 'entities' and 'relations'.\n\nText:\n{text}"
    )

    def __init__(self, llm, ontology: Ontology):
        self.llm, self.ont = llm, ontology

    def extract(self, text: str) -> Extraction:
        desc = {k: v.get("description", "") for k, v in self.ont.entity_types.items()}
        prompt = self.PROMPT.format(schema=json.dumps(self.ont.json_schema()), desc=json.dumps(desc), text=text)
        raw = self.llm.generate([{"role": "user", "content": prompt}], json_mode=True, max_tokens=1200).text
        try:
            data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
        except ValueError:
            return Extraction()
        ents = [Entity(e["name"], e["type"]) for e in data.get("entities", [])
                if e.get("type") in self.ont.entity_types and e.get("name")]
        types = {e.name: e.type for e in ents}
        rels = []
        for r in data.get("relations", []):
            rt = self.ont.relations.get(r.get("relation", ""))
            if rt and types.get(r.get("source")) in rt.domain and types.get(r.get("target")) in rt.range:
                rels.append(Relation(r["source"], rt.name, r["target"], float(r.get("confidence", 0.7))))
        return Extraction(ents, rels)


# ---------------------------------------------------------------- graph store


def normalize(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    n = re.sub(r"\b(the|a|an)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


class InMemoryGraph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []

    def upsert_node(self, key: str, name: str, etype: str, chunk_id: str):
        n = self.nodes.setdefault(key, {"name": name, "type": etype, "chunks": set()})
        n["chunks"].add(chunk_id)

    def upsert_edge(self, src: str, rel: str, dst: str, chunk_id: str, confidence: float):
        for e in self.edges:
            if (e["src"], e["rel"], e["dst"]) == (src, rel, dst):
                e["chunks"].add(chunk_id)
                e["confidence"] = max(e["confidence"], confidence)
                return
        self.edges.append({"src": src, "rel": rel, "dst": dst, "chunks": {chunk_id}, "confidence": confidence})

    def neighbourhood(self, key: str, depth: int = 1) -> list[dict]:
        frontier, seen, facts = {key}, {key}, []
        for _ in range(depth):
            nxt = set()
            for e in self.edges:
                if e["src"] in frontier or e["dst"] in frontier:
                    if e not in facts:
                        facts.append(e)
                    for k in (e["src"], e["dst"]):
                        if k not in seen:
                            nxt.add(k)
                            seen.add(k)
            frontier = nxt
        return facts

    def stats(self) -> dict:
        return {"nodes": len(self.nodes), "edges": len(self.edges)}


class Neo4jGraph:
    """Neo4j or Amazon Neptune (openCypher over Bolt). Same MERGE semantics as InMemoryGraph."""

    def __init__(self, uri: str, auth: tuple[str, str] | None = None):
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(uri, auth=auth)

    def upsert_node(self, key, name, etype, chunk_id):
        self.driver.execute_query(
            "MERGE (n:Entity {key:$key}) SET n.name=$name, n.type=$type, "
            "n.chunks = CASE WHEN $cid IN coalesce(n.chunks, []) THEN n.chunks ELSE coalesce(n.chunks, []) + $cid END",
            key=key, name=name, type=etype, cid=chunk_id)

    def upsert_edge(self, src, rel, dst, chunk_id, confidence):
        self.driver.execute_query(
            "MATCH (a:Entity {key:$s}), (b:Entity {key:$d}) MERGE (a)-[r:REL {type:$rel}]->(b) "
            "SET r.confidence = CASE WHEN coalesce(r.confidence,0) > $c THEN r.confidence ELSE $c END, "
            "r.chunks = coalesce(r.chunks, []) + $cid", s=src, d=dst, rel=rel, c=confidence, cid=chunk_id)

    def neighbourhood(self, key, depth=1):
        recs, _, _ = self.driver.execute_query(
            f"MATCH (a:Entity {{key:$k}})-[r:REL*1..{int(depth)}]-(b) UNWIND r AS e "
            "RETURN DISTINCT startNode(e).key AS src, e.type AS rel, endNode(e).key AS dst, "
            "e.chunks AS chunks, e.confidence AS confidence LIMIT 100", k=key)
        return [dict(r) | {"chunks": set(r["chunks"] or [])} for r in recs]


_WRITE = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|CALL\s+dbms|CALL\s+apoc)\b", re.IGNORECASE)


def validate_cypher(query: str, max_limit: int = 100) -> str:
    """Guard for text-to-Cypher: read-only, single statement, bounded result size."""
    q = query.strip().rstrip(";")
    if ";" in q:
        raise ValueError("multiple statements are not allowed")
    if _WRITE.search(q):
        raise ValueError("write or admin clauses are not allowed")
    if not re.match(r"^\s*(MATCH|OPTIONAL MATCH|WITH|RETURN)\b", q, re.IGNORECASE):
        raise ValueError("query must start with MATCH/WITH/RETURN")
    m = re.search(r"\bLIMIT\s+(\d+)\s*$", q, re.IGNORECASE)
    if not m:
        q += f" LIMIT {max_limit}"
    elif int(m.group(1)) > max_limit:
        q = q[: m.start(1)] + str(max_limit)
    return q


# ---------------------------------------------------------------- builder + retriever


class KGBuilder:
    def __init__(self, extractor, graph=None, embedder: Embedder | None = None, merge_threshold: float = 0.93):
        self.extractor = extractor
        self.graph = graph or InMemoryGraph()
        self.embedder, self.thr = embedder, merge_threshold
        self._keys: list[str] = []
        self._vecs: list[np.ndarray] = []

    def resolve(self, name: str) -> str:
        """Entity resolution: normalised name, then embedding similarity against known entities."""
        key = normalize(name)
        if key in getattr(self.graph, "nodes", {}) or self.embedder is None:
            return key
        v = np.array(self.embedder.embed([key])[0])
        for k, kv in zip(self._keys, self._vecs):
            if float(kv @ v) >= self.thr:
                return k
        self._keys.append(key)
        self._vecs.append(v)
        return key

    def add_chunks(self, chunks: list[Chunk]) -> int:
        n = 0
        for c in chunks:
            ex = self.extractor.extract(c.text)
            types = {}
            for e in ex.entities:
                k = self.resolve(e.name)
                types[e.name] = k
                self.graph.upsert_node(k, e.name, e.type, c.id)
                n += 1
            for r in ex.relations:
                s, d = types.get(r.source) or self.resolve(r.source), types.get(r.target) or self.resolve(r.target)
                self.graph.upsert_edge(s, r.relation, d, c.id, r.confidence)
        return n


class GraphRetriever:
    def __init__(self, graph: InMemoryGraph, store, depth: int = 1):
        self.graph, self.store, self.depth = graph, store, depth

    def mentions(self, question: str) -> list[str]:
        q = " " + normalize(question) + " "
        hits = [k for k in self.graph.nodes if f" {k} " in q]
        return sorted(hits, key=len, reverse=True)

    def retrieve(self, question: str, k: int = 8, acl=None) -> tuple[list[str], list[ScoredChunk]]:
        facts, chunk_ids = [], {}
        for key in self.mentions(question):
            for e in self.graph.neighbourhood(key, self.depth):
                src = self.graph.nodes.get(e["src"], {}).get("name", e["src"])
                dst = self.graph.nodes.get(e["dst"], {}).get("name", e["dst"])
                fact = f"{src} {e['rel']} {dst}"
                if fact not in facts:
                    facts.append(fact)
                for cid in e["chunks"]:
                    chunk_ids[cid] = max(chunk_ids.get(cid, 0), e["confidence"])
        out = []
        from ..vectorstore.base import acl_allows

        for cid, conf in sorted(chunk_ids.items(), key=lambda x: -x[1])[:k]:
            ch = self.store.get(cid)
            if ch and acl_allows(ch.acl, acl):
                out.append(ScoredChunk(chunk=ch, score=conf, retriever="graph"))
        return facts, out
