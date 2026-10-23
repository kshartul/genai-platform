"""Platform tools, registered once and reused by agents and by the MCP server.

* ``search_knowledge`` / ``answer_question``: wrap retrieval-service (ACL-aware via the caller's groups)
* ``graph_neighbours``: knowledge-graph facts around an entity
* ``query_warehouse``: read-only SQL against the warehouse (SQLite demo; Redshift/Synapse in cloud)
* ``create_ticket``: a WRITE tool to demonstrate the approval gate
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .registry import Principal, ToolError, ToolRegistry, ToolSpec

_SQL_WRITE = re.compile(r"\b(insert|update|delete|drop|alter|create|replace|attach|pragma|grant|truncate)\b", re.IGNORECASE)


def safe_select(sql: str, max_rows: int = 200) -> str:
    s = sql.strip().rstrip(";")
    if ";" in s or not re.match(r"^\s*(select|with)\b", s, re.IGNORECASE) or _SQL_WRITE.search(s):
        raise ToolError("only a single read-only SELECT statement is allowed")
    if not re.search(r"\blimit\s+\d+\s*$", s, re.IGNORECASE):
        s += f" LIMIT {max_rows}"
    return s


def register_platform_tools(registry: ToolRegistry, platform, warehouse_path: str | None = None,
                            ticket_path: str = ".data/tickets.jsonl") -> ToolRegistry:
    def search_knowledge(query: str, top_k: int = 5, _principal: Principal | None = None):
        hits = platform.retrieval.retrieve(query, acl=_principal.groups if _principal else None, top_n=top_k)
        return [{"chunk_id": h.chunk.id, "source": h.chunk.source, "section": h.chunk.section,
                 "score": round(h.score, 4), "text": h.chunk.text} for h in hits]

    def answer_question(question: str, _principal: Principal | None = None):
        a = platform.rag.answer(question, acl=_principal.groups if _principal else None)
        return {"answer": a.answer, "citations": [c.model_dump() for c in a.citations], "trace_id": a.trace_id}

    def graph_neighbours(entity: str):
        if platform.graph_retriever is None:
            raise ToolError("knowledge graph is not enabled for this profile")
        facts, _ = platform.graph_retriever.retrieve(entity)
        return facts

    def query_warehouse(sql: str):
        if not warehouse_path:
            raise ToolError("no warehouse configured")
        q = safe_select(sql)
        con = sqlite3.connect(f"file:{warehouse_path}?mode=ro", uri=True)
        try:
            cur = con.execute(q)
            cols = [d[0] for d in cur.description]
            return {"columns": cols, "rows": cur.fetchall()}
        finally:
            con.close()

    def warehouse_schema():
        if not warehouse_path:
            raise ToolError("no warehouse configured")
        con = sqlite3.connect(f"file:{warehouse_path}?mode=ro", uri=True)
        try:
            return {r[0]: r[1] for r in con.execute("select name, sql from sqlite_master where type='table'")}
        finally:
            con.close()

    def create_ticket(title: str, body: str, _principal: Principal | None = None):
        Path(ticket_path).parent.mkdir(parents=True, exist_ok=True)
        rec = {"title": title, "body": body, "requested_by": _principal.subject if _principal else "unknown"}
        with open(ticket_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return {"status": "created", **rec}

    S = {"type": "string"}
    registry.register(ToolSpec("search_knowledge", "Hybrid search over the knowledge base; returns cited chunks.",
                               search_knowledge, {"query": S, "top_k": {"type": "integer", "required": False}}))
    registry.register(ToolSpec("answer_question", "Grounded, cited answer from the knowledge base.",
                               answer_question, {"question": S}))
    registry.register(ToolSpec("graph_neighbours", "Facts linked to an entity in the knowledge graph.",
                               graph_neighbours, {"entity": S}, scopes=["kb.read", "graph.read"]))
    registry.register(ToolSpec("warehouse_schema", "Table definitions of the analytics warehouse.",
                               warehouse_schema, {}, scopes=["dw.read"]))
    registry.register(ToolSpec("query_warehouse", "Run ONE read-only SQL SELECT against the analytics warehouse.",
                               query_warehouse, {"sql": S}, scopes=["dw.read"], rate_limit_per_min=30))
    registry.register(ToolSpec("create_ticket", "Open a support ticket (write action; needs approval).",
                               create_ticket, {"title": S, "body": S}, scopes=["tickets.write"], kind="write",
                               rate_limit_per_min=5))
    return registry
