"""Tool registry and policy enforcement (the core of ``mcp-gateway``).

Every tool an agent can call, whether local or exposed over MCP, is registered once with:
scopes (who may call it), kind (read / write), an argument schema, and a rate limit. Calls are
validated, rate-limited and written to an audit log. Write tools require explicit approval.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

audit_log = logging.getLogger("genai.audit")

_PY = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}


class ToolError(Exception):
    pass


class ApprovalRequired(ToolError):
    def __init__(self, tool: str, args: dict):
        super().__init__(f"tool '{tool}' performs a write and needs human approval")
        self.tool, self.args = tool, args


@dataclass
class ToolSpec:
    name: str
    description: str
    fn: Callable[..., Any]
    params: dict[str, dict] = field(default_factory=dict)  # {"query": {"type": "string", "required": True}}
    scopes: list[str] = field(default_factory=lambda: ["kb.read"])
    kind: Literal["read", "write"] = "read"
    rate_limit_per_min: int = 60

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description, "kind": self.kind,
                "parameters": {"type": "object",
                               "properties": {k: {"type": v["type"], "description": v.get("description", "")}
                                              for k, v in self.params.items()},
                               "required": [k for k, v in self.params.items() if v.get("required", True)]}}


@dataclass
class Principal:
    subject: str
    scopes: list[str]
    groups: list[str] = field(default_factory=list)


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, ToolSpec] = {}
        self._calls: dict[tuple[str, str], deque] = defaultdict(deque)
        self.audit: list[dict] = []

    def register(self, spec: ToolSpec) -> ToolSpec:
        self.tools[spec.name] = spec
        return spec

    def visible_to(self, p: Principal) -> list[ToolSpec]:
        return [t for t in self.tools.values() if set(t.scopes) & set(p.scopes)]

    def _validate(self, spec: ToolSpec, args: dict) -> dict:
        unknown = set(args) - set(spec.params)
        if unknown:
            raise ToolError(f"unknown arguments for {spec.name}: {sorted(unknown)}")
        for name, rule in spec.params.items():
            if name not in args:
                if rule.get("required", True):
                    raise ToolError(f"missing argument '{name}' for {spec.name}")
                continue
            if not isinstance(args[name], _PY[rule["type"]]):
                raise ToolError(f"argument '{name}' must be {rule['type']}")
            if rule["type"] == "string" and len(args[name]) > rule.get("max_length", 2000):
                raise ToolError(f"argument '{name}' too long")
        return args

    def _rate_limit(self, spec: ToolSpec, p: Principal):
        q = self._calls[(spec.name, p.subject)]
        now = time.monotonic()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= spec.rate_limit_per_min:
            raise ToolError(f"rate limit exceeded for {spec.name}")
        q.append(now)

    def call(self, name: str, args: dict, principal: Principal, *, approved: bool = False) -> Any:
        spec = self.tools.get(name)
        entry = {"ts": time.time(), "tool": name, "subject": principal.subject, "args": args, "status": "error"}
        try:
            if spec is None:
                raise ToolError(f"unknown tool '{name}'")
            if not set(spec.scopes) & set(principal.scopes):
                raise ToolError(f"principal lacks scope for '{name}' (needs one of {spec.scopes})")
            self._validate(spec, args)
            if spec.kind == "write" and not approved:
                entry["status"] = "approval_required"
                raise ApprovalRequired(name, args)
            self._rate_limit(spec, principal)
            result = spec.fn(**args, _principal=principal) if "_principal" in spec.fn.__code__.co_varnames \
                else spec.fn(**args)
            entry["status"] = "ok"
            return result
        except ToolError as e:
            entry["error"] = str(e)
            raise
        finally:
            self.audit.append(entry)
            audit_log.info(json.dumps(entry, default=str))
