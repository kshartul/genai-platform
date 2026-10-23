"""Rank fusion and reranking."""

from __future__ import annotations

from ..providers.local import tokenize
from ..types import ScoredChunk


def reciprocal_rank_fusion(result_lists: list[list[ScoredChunk]], k: int = 60) -> list[ScoredChunk]:
    """RRF: score = sum(1 / (k + rank)). Robust to the different score scales of BM25 and cosine."""
    scores: dict[str, float] = {}
    best: dict[str, ScoredChunk] = {}
    sources: dict[str, set[str]] = {}
    for results in result_lists:
        for rank, sc in enumerate(results, start=1):
            cid = sc.chunk.id
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            best.setdefault(cid, sc)
            sources.setdefault(cid, set()).add(sc.retriever)
    fused = [ScoredChunk(chunk=best[c].chunk, score=s, retriever="+".join(sorted(sources[c])))
             for c, s in scores.items()]
    return sorted(fused, key=lambda x: -x.score)


class LexicalReranker:
    """Local cross-scoring stand-in: query-term coverage weighted by chunk focus.
    Replace with a cross-encoder (Cohere Rerank on Bedrock, BGE reranker, Azure semantic ranker)."""

    name = "lexical"

    def rerank(self, query: str, items: list[ScoredChunk], top_n: int) -> list[ScoredChunk]:
        q = set(tokenize(query))
        if not q:
            return items[:top_n]
        out = []
        for it in items:
            toks = tokenize(it.chunk.text + " " + it.chunk.section)
            ts = set(toks)
            coverage = len(q & ts) / len(q)
            density = sum(1 for t in toks if t in q) / (len(toks) ** 0.5 or 1)
            out.append(ScoredChunk(chunk=it.chunk, score=0.7 * coverage + 0.3 * min(density, 1.0),
                                   retriever=it.retriever))
        return sorted(out, key=lambda x: -x.score)[:top_n]


class BedrockReranker:
    """Amazon Bedrock Rerank API (e.g. cohere.rerank-v3-5:0 or amazon.rerank-v1:0)."""

    name = "bedrock"

    def __init__(self, model_arn: str, region: str | None = None):
        import boto3

        self.client = boto3.client("bedrock-agent-runtime", region_name=region)
        self.model_arn = model_arn

    def rerank(self, query, items, top_n):
        if not items:
            return []
        r = self.client.rerank(
            queries=[{"type": "TEXT", "textQuery": {"text": query}}],
            sources=[{"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": i.chunk.text}}}
                     for i in items],
            rerankingConfiguration={"type": "BEDROCK_RERANKING_MODEL", "bedrockRerankingConfiguration": {
                "numberOfResults": min(top_n, len(items)), "modelConfiguration": {"modelArn": self.model_arn}}},
        )
        return [ScoredChunk(chunk=items[x["index"]].chunk, score=x["relevanceScore"], retriever=items[x["index"]].retriever)
                for x in r["results"]]


def build_reranker(kind: str, region: str | None = None, model_arn: str | None = None):
    if kind == "bedrock":
        return BedrockReranker(model_arn or "", region)
    if kind in ("lexical", "cohere", "azure_semantic"):
        # cohere/azure_semantic run server-side in the store query; lexical is the local default
        return LexicalReranker()
    return None
