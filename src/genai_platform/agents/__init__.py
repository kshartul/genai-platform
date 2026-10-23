"""Agent runtime: build a pattern from a profile and run it with approval handling."""

from __future__ import annotations

import uuid

from .patterns import (
    AgentState,
    Budget,
    agent_as_tool,
    build_plan_execute_agent,
    build_react_agent,
    build_router_agent,
)

__all__ = [
    "AgentRunner",
    "AgentState",
    "Budget",
    "agent_as_tool",
    "build_plan_execute_agent",
    "build_react_agent",
    "build_router_agent",
]

BUILDERS = {"router": build_router_agent, "react": build_react_agent, "plan_execute": build_plan_execute_agent,
            "supervisor": build_react_agent}


class AgentRunner:
    """Runs a compiled graph per thread. If the graph pauses at the approval gate, ``run`` returns
    ``status=awaiting_approval`` with the pending call; ``resume(thread_id, approved)`` continues."""

    def __init__(self, graph):
        self.graph = graph

    def _cfg(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def _result(self, thread_id: str) -> dict:
        snap = self.graph.get_state(self._cfg(thread_id))
        st = snap.values
        if snap.next and "approval_gate" in snap.next:
            return {"thread_id": thread_id, "status": "awaiting_approval", "pending": st.get("pending")}
        return {"thread_id": thread_id, "status": "done", "answer": st.get("answer", ""),
                "stop_reason": st.get("stop_reason"), "steps": st.get("steps", 0),
                "observations": st.get("observations", [])}

    def run(self, question: str, thread_id: str | None = None) -> dict:
        thread_id = thread_id or uuid.uuid4().hex
        self.graph.invoke({"question": question, "observations": [], "steps": 0, "approved": False},
                          self._cfg(thread_id))
        return self._result(thread_id)

    def resume(self, thread_id: str, approved: bool) -> dict:
        self.graph.update_state(self._cfg(thread_id), {"approved": approved})
        self.graph.invoke(None, self._cfg(thread_id))
        return self._result(thread_id)
