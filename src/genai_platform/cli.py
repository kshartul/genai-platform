"""CLI: ``genai ingest|ask|agent|eval`` against a profile.

  genai ingest --profile profiles/knowledge-assistant.local.yaml
  genai ask    --profile ... "How many days of annual leave do I get?"
  genai agent  --profile ... --scopes kb.read,dw.read "What was total revenue by region in 2025?"
  genai eval   --profile ... --report reports/eval.json        # exits 1 if the gate fails
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .evals import gate, load_golden, run_evals
from .mcp import Principal
from .platform import Platform


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="genai")
    ap.add_argument("command", choices=["ingest", "ask", "agent", "eval"])
    ap.add_argument("text", nargs="?")
    ap.add_argument("--profile", default="profiles/knowledge-assistant.local.yaml")
    ap.add_argument("--root", default=".")
    ap.add_argument("--groups", default="", help="comma-separated ACL groups of the caller")
    ap.add_argument("--scopes", default="kb.read", help="comma-separated tool scopes of the caller")
    ap.add_argument("--report", help="write eval summary JSON here")
    a = ap.parse_args(argv)

    plat = Platform.from_profile(a.profile, a.root)
    groups = [g for g in a.groups.split(",") if g]
    if a.command != "ingest":
        plat.ingest()
    if a.command == "ingest":
        print(json.dumps(plat.ingest().__dict__, indent=2))
        if plat.graph is not None and hasattr(plat.graph, "stats"):
            print(json.dumps({"graph": plat.graph.stats()}))
        return 0
    if a.command == "ask":
        ans = plat.rag.answer(a.text or "", acl=groups)
        print(ans.answer)
        for i, c in enumerate(ans.citations, 1):
            print(f"  [{i}] {c.source} > {c.section}")
        return 0
    if a.command == "agent":
        out = plat.agent(Principal("cli", a.scopes.split(","), groups)).run(a.text or "")
        print(json.dumps(out, indent=2, default=str))
        return 0
    golden = load_golden(Path(a.root) / plat.profile.evals.golden_set)
    summary, results = run_evals(plat, golden)
    failures = gate(summary, plat.profile.evals.thresholds)
    report = {"profile": plat.profile.name, "summary": summary, "thresholds": plat.profile.evals.thresholds,
              "passed": not failures, "failures": failures,
              "cases": [r.__dict__ | {"contexts": None} for r in results]}
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({"summary": summary, "passed": not failures, "failures": failures}, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
