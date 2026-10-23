"""Evaluation harness (``eval-harness``) and CI release gate.

Golden set (JSONL), one case per line:
  {"id": "q1", "question": "...", "expected_answer": "...", "expected_sources": ["file.md"],
   "acl": ["hr"], "type": "rag" | "agent" | "refusal", "expected_tool": "query_warehouse"}

Metrics computed locally (deterministic, free, run on every PR):
  hit_rate@k, mrr, context_recall, faithfulness (claim support vs context), answer_similarity,
  refusal_accuracy, agent_tool_accuracy, p95 latency.
RAGAS / DeepEval / LLM-as-judge scores are added by ``--judge ragas`` in nightly runs.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..guardrails import groundedness
from ..providers.local import tokenize


@dataclass
class CaseResult:
    id: str
    type: str
    metrics: dict[str, float]
    answer: str = ""
    retrieved: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    contexts: list[str] = field(default_factory=list)


def load_golden(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _source_match(source: str, expected: list[str]) -> bool:
    return any(Path(source).name == Path(e).name for e in expected)


def _f1(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    common = sum(min(ta.count(t), tb.count(t)) for t in set(ta) & set(tb))
    if not common:
        return 0.0
    p, r = common / len(ta), common / len(tb)
    return 2 * p * r / (p + r)


def run_evals(platform, golden: list[dict], k: int = 5) -> tuple[dict[str, float], list[CaseResult]]:
    results: list[CaseResult] = []
    for case in golden:
        ctype = case.get("type", "rag")
        t0 = time.perf_counter()
        if ctype == "agent":
            from ..mcp import Principal

            principal = Principal("eval", case.get("scopes", ["kb.read", "dw.read", "graph.read"]), case.get("acl", []))
            out = platform.agent(principal).run(case["question"])
            obs = out.get("observations", [])
            tool = obs[0]["tool"] if obs else out.get("pending", {}).get("tool") if out.get("pending") else None
            if "expected_status" in case:
                success = out.get("status") == case["expected_status"]
            else:
                blob = json.dumps(obs, default=str).lower()
                success = bool(obs) and obs[0].get("ok", False) and all(
                    s.lower() in blob for s in case.get("expected_contains", []))
            m = {"agent_tool_accuracy": float(tool == case.get("expected_tool")), "agent_task_success": float(success)}
            results.append(CaseResult(case["id"], ctype, m, out.get("answer", ""),
                                      latency_ms=(time.perf_counter() - t0) * 1000))
            continue
        ans = platform.rag.answer(case["question"], acl=case.get("acl"))
        lat = (time.perf_counter() - t0) * 1000
        sources = [c.chunk.source for c in ans.contexts]
        if ctype == "acl":
            blob = (ans.answer + " " + " ".join(c.chunk.text for c in ans.contexts)).lower()
            leak = any(_source_match(s, case.get("forbidden_sources", [])) for s in sources) or any(
                f.lower() in blob for f in case.get("forbidden_contains", []))
            results.append(CaseResult(case["id"], ctype, {"acl_leakage_free": float(not leak)}, ans.answer, sources, lat))
            continue
        if ctype == "refusal":
            refused = ans.blocked or ans.answer.lower().startswith("i don't know")
            results.append(CaseResult(case["id"], ctype, {"refusal_accuracy": float(refused)}, ans.answer, sources, lat))
            continue
        exp = case.get("expected_sources", [])
        ranks = [i for i, s in enumerate(sources[:k], 1) if _source_match(s, exp)]
        m = {
            f"hit_rate@{k}": float(bool(ranks)),
            "mrr": 1.0 / ranks[0] if ranks else 0.0,
            "context_recall": sum(any(_source_match(s, [e]) for s in sources[:k]) for e in exp) / len(exp) if exp else 1.0,
            "faithfulness": groundedness(ans.answer, [c.chunk.text for c in ans.contexts]),
            "answer_similarity": _f1(ans.answer, case.get("expected_answer", "")),
        }
        results.append(CaseResult(case["id"], ctype, m, ans.answer, sources, lat,
                                  [c.chunk.text for c in ans.contexts]))
    summary: dict[str, float] = {}
    for key in sorted({k for r in results for k in r.metrics}):
        vals = [r.metrics[key] for r in results if key in r.metrics]
        summary[key] = round(sum(vals) / len(vals), 4)
    lats = sorted(r.latency_ms for r in results)
    if lats:
        summary["latency_p95_ms"] = round(lats[min(len(lats) - 1, int(0.95 * len(lats)))], 1)
        summary["latency_median_ms"] = round(statistics.median(lats), 1)
    summary["cases"] = len(results)
    return summary, results


def gate(summary: dict[str, float], thresholds: dict[str, float]) -> list[str]:
    """Return the list of failed thresholds (empty = pass). Latency thresholds are upper bounds."""
    failures = []
    for name, thr in thresholds.items():
        val = summary.get(name)
        if val is None:
            failures.append(f"{name}: missing")
        elif name.startswith("latency"):
            if val > thr:
                failures.append(f"{name}: {val} > {thr}")
        elif val < thr:
            failures.append(f"{name}: {val} < {thr}")
    return failures


def ragas_scores(results: list[CaseResult], golden: list[dict]) -> dict:
    """Optional LLM-judged metrics via RAGAS (install extra 'evals'; needs a judge model configured)."""
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

    by_id = {c["id"]: c for c in golden}
    rows = [r for r in results if r.type == "rag"]
    ds = Dataset.from_dict({
        "question": [by_id[r.id]["question"] for r in rows],
        "answer": [r.answer for r in rows],
        "contexts": [r.contexts or [""] for r in rows],
        "ground_truth": [by_id[r.id].get("expected_answer", "") for r in rows],
    })
    return evaluate(ds, metrics=[faithfulness, answer_relevancy, context_precision, context_recall]).to_pandas().mean(
        numeric_only=True).to_dict()
