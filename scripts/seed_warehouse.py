"""Create the demo analytics warehouse (SQLite) used by the query_warehouse tool and agent evals.
In cloud the same tool points at Redshift (Data API) or Synapse/Fabric SQL with a read-only role."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REGIONS = {"North": 1.00, "South": 0.85, "West": 1.20, "East": 0.70}
MONTHS = [f"2025-{m:02d}" for m in range(1, 13)]


def seed(path: str = "data/warehouse/analytics.db") -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sales (region TEXT, month TEXT, revenue_inr_lakh REAL, orders INTEGER)")
    con.execute("CREATE TABLE headcount (department TEXT, employees INTEGER, as_of TEXT)")
    for region, f in REGIONS.items():
        for i, month in enumerate(MONTHS):
            con.execute("INSERT INTO sales VALUES (?,?,?,?)",
                        (region, month, round(100 * f + 5 * i, 1), int(400 * f + 20 * i)))
    for dept, n in [("Engineering", 420), ("Sales", 180), ("Finance", 45), ("People Operations", 30)]:
        con.execute("INSERT INTO headcount VALUES (?,?,?)", (dept, n, "2026-01-01"))
    con.commit()
    con.close()
    return path


if __name__ == "__main__":
    print(seed(*sys.argv[1:]))
