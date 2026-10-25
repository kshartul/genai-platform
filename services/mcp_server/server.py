"""MCP server exposing the platform's governed tools to any MCP client (Claude, IDEs, other agents).

Transport: streamable HTTP (``python -m services.mcp_server.server --http``) for ECS / Container
Apps behind the MCP gateway (OAuth 2.1 at the edge), or stdio for local clients.
Every call goes through the same ToolRegistry, so scopes, validation, rate limits, approval
for write tools and the audit log apply exactly as they do for in-process agents.
"""

from __future__ import annotations

import argparse
import json
import os

from mcp.server.mcpserver import MCPServer

from genai_platform.mcp import ApprovalRequired, Principal, ToolError
from genai_platform.platform import Platform

PROFILE = os.environ.get("GENAI_PROFILE", "profiles/knowledge-assistant.local.yaml")
ROOT = os.environ.get("GENAI_ROOT", ".")
# The gateway maps the OAuth token to scopes/groups; for stdio these come from the environment.
CALLER = Principal(os.environ.get("MCP_CALLER", "mcp-client"),
                   os.environ.get("MCP_SCOPES", "kb.read,graph.read,dw.read").split(","),
                   [g for g in os.environ.get("MCP_GROUPS", "").split(",") if g])


def build_server(platform: Platform, caller: Principal = CALLER) -> MCPServer:
    server = MCPServer("genai-platform", instructions="Enterprise knowledge, graph and warehouse tools. "
                                                     "All answers carry citations; write tools need approval.")

    def call(name: str, **args) -> str:
        try:
            return json.dumps(platform.registry.call(name, args, caller), default=str)
        except ApprovalRequired as e:
            return json.dumps({"status": "approval_required", "tool": e.tool, "args": e.args})
        except ToolError as e:
            return json.dumps({"error": str(e)})

    visible = {t.name for t in platform.registry.visible_to(caller)}

    if "search_knowledge" in visible:
        @server.tool(description="Hybrid search over the enterprise knowledge base. Returns chunks with sources.")
        def search_knowledge(query: str, top_k: int = 5) -> str:
            return call("search_knowledge", query=query, top_k=top_k)

    if "answer_question" in visible:
        @server.tool(description="Grounded answer with citations from the enterprise knowledge base.")
        def answer_question(question: str) -> str:
            return call("answer_question", question=question)

    if "graph_neighbours" in visible:
        @server.tool(description="Knowledge-graph facts (owners, dependencies, policies) about an entity.")
        def graph_neighbours(entity: str) -> str:
            return call("graph_neighbours", entity=entity)

    if "warehouse_schema" in visible:
        @server.tool(description="Table definitions of the analytics warehouse.")
        def warehouse_schema() -> str:
            return call("warehouse_schema")

    if "query_warehouse" in visible:
        @server.tool(description="Run one read-only SQL SELECT against the analytics warehouse.")
        def query_warehouse(sql: str) -> str:
            return call("query_warehouse", sql=sql)

    @server.resource("kb://stats")
    def kb_stats() -> str:
        return json.dumps({"profile": platform.profile.name, "chunks": platform.store.count()})

    return server


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8001")))
    a = ap.parse_args()
    platform = Platform.from_profile(PROFILE, ROOT)
    if os.environ.get("GENAI_INGEST_ON_START", "true").lower() == "true":
        platform.ingest()
    server = build_server(platform)
    if a.http:
        server.run("streamable-http", host="0.0.0.0", port=a.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
