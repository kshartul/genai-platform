"""Phase 1 bake-off: run the golden set against each chunking strategy and size, print a table.
Pick the winner per corpus and record it in the profile (and an ADR if it changes the default)."""

from __future__ import annotations

import argparse

from genai_platform.config import load_profile
from genai_platform.evals import load_golden, run_evals
from genai_platform.platform import Platform

GRID = [("recursive", 120), ("recursive", 300), ("semantic", 200), ("parent_child", 400)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="profiles/knowledge-assistant.local.yaml")
    a = ap.parse_args()
    print(f"{'strategy':<14}{'tokens':>7}{'chunks':>8}{'hit@5':>8}{'mrr':>7}{'recall':>8}")
    for strategy, tokens in GRID:
        prof = load_profile(a.profile)
        prof.chunking.strategy, prof.chunking.target_tokens = strategy, tokens
        plat = Platform(prof)
        stats = plat.ingest()
        golden = [c for c in load_golden(prof.evals.golden_set) if c.get("type", "rag") == "rag"]
        s, _ = run_evals(plat, golden)
        print(f"{strategy:<14}{tokens:>7}{stats.chunks:>8}{s['hit_rate@5']:>8.2f}{s['mrr']:>7.2f}{s['context_recall']:>8.2f}")


if __name__ == "__main__":
    main()
