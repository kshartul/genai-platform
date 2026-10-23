import asyncio
import json

import pytest

from genai_platform.agents import AgentRunner, Budget, build_react_agent
from genai_platform.kg import validate_cypher
from genai_platform.mcp import Principal, ToolError, safe_select
from genai_platform.providers.base import LLMResult


# ---------------- knowledge graph
def test_graph_extracts_ontology_relations(platform):
    facts = {(platform.graph.nodes[e["src"]]["name"], e["rel"], platform.graph.nodes[e["dst"]]["name"])
             for e in platform.graph.edges}
    assert ("Risk Team", "OWNS", "Fraud Detection Service") in facts
    assert ("Payments API", "GOVERNED_BY", "PCI Compliance Policy") in facts
    assert ("Incident Commander", "ESCALATES_TO", "Head of Platform") in facts
    assert all(e["chunks"] for e in platform.graph.edges)  # every edge links back to its evidence


def test_graph_retriever_returns_facts_and_evidence(platform):
    facts, chunks = platform.graph_retriever.retrieve("What does the Fraud Detection Service depend on?")
    assert "Fraud Detection Service DEPENDS_ON Identity Service" in facts
    assert chunks and chunks[0].retriever == "graph"


def test_cypher_guard():
    assert validate_cypher("MATCH (n) RETURN n").endswith("LIMIT 100")
    assert validate_cypher("MATCH (n) RETURN n LIMIT 5000").endswith("LIMIT 100")
    for bad in ["MATCH (n) DETACH DELETE n", "CREATE (n:X)", "MATCH (n) RETURN n; MATCH (m) RETURN m"]:
        with pytest.raises(ValueError):
            validate_cypher(bad)


# ---------------- tool registry / MCP gateway policy
def test_sql_guard():
    assert safe_select("select * from sales").endswith("LIMIT 200")
    for bad in ["drop table sales", "select 1; delete from sales", "update sales set orders=0"]:
        with pytest.raises(ToolError):
            safe_select(bad)


def test_scopes_validation_and_audit(platform):
    reg = platform.registry
    nobody = Principal("u1", ["kb.read"])
    with pytest.raises(ToolError, match="lacks scope"):
        reg.call("query_warehouse", {"sql": "select 1"}, nobody)
    with pytest.raises(ToolError, match="unknown arguments"):
        reg.call("search_knowledge", {"query": "x", "rm": "-rf"}, nobody)
    assert reg.audit[-1]["status"] == "error" and reg.audit[-1]["subject"] == "u1"


# ---------------- agents
def test_router_agent_uses_warehouse(platform):
    out = platform.agent(Principal("a", ["kb.read", "dw.read"])).run("What was the total revenue by region in 2025?")
    obs = out["observations"][0]
    assert out["status"] == "done" and obs["tool"] == "query_warehouse" and obs["ok"]
    assert obs["result"]["rows"][0][0] == "West"


def test_router_agent_falls_back_when_scope_missing(platform):
    out = platform.agent(Principal("a", ["kb.read"])).run("How many employees per department?")
    assert out["observations"][0]["tool"] == "answer_question"


def test_write_tool_pauses_for_approval(platform, tmp_path):
    runner = platform.agent(Principal("a", ["kb.read", "tickets.write"]))
    out = runner.run("Please open a ticket to reset my VPN token")
    assert out["status"] == "awaiting_approval" and out["pending"]["tool"] == "create_ticket"
    denied = runner.resume(out["thread_id"], approved=False)
    assert denied["stop_reason"] == "approval_denied"
    out2 = runner.run("Please open a ticket to reset my VPN token")
    done = runner.resume(out2["thread_id"], approved=True)
    assert done["observations"][0]["result"]["status"] == "created"


class ScriptedLLM:
    """Plays back JSON steps so the ReAct loop is tested without a cloud model."""

    name = "scripted"

    def __init__(self, steps):
        self.steps = list(steps)

    def generate(self, messages, **kw):
        return LLMResult(json.dumps(self.steps.pop(0)), self.name)


def test_react_agent_loop_and_budget(platform):
    p = Principal("r", ["kb.read", "graph.read"])
    llm = ScriptedLLM([{"tool": "graph_neighbours", "args": {"entity": "Payments API"}},
                       {"final": "The Payments API is owned by the Payments Team."}])
    out = AgentRunner(build_react_agent(platform.registry, p, llm, budget=Budget(llm, 5, 1.0))).run("who owns payments?")
    assert out["answer"].startswith("The Payments API") and out["observations"][0]["ok"]
    looping = ScriptedLLM([{"tool": "search_knowledge", "args": {"query": "x"}}] * 10)
    out = AgentRunner(build_react_agent(platform.registry, p, looping, budget=Budget(looping, 3, 1.0))).run("loop")
    assert out["stop_reason"] == "max_steps"


# ---------------- MCP server
def test_mcp_server_lists_and_calls_tools(platform):
    from services.mcp_server.server import build_server

    server = build_server(platform, Principal("mcp", ["kb.read", "graph.read"]))
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"search_knowledge", "answer_question", "graph_neighbours"} <= names
    assert "query_warehouse" not in names  # not in caller scopes
    res = asyncio.run(server.call_tool("graph_neighbours", {"entity": "Identity Service"}))
    assert "Access Control Policy" in str(res)
