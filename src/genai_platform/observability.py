"""Structured JSON logging and optional tracing (Langfuse / OpenTelemetry).

Logs go to stdout as JSON so CloudWatch Logs Insights and Azure Monitor (Log Analytics) can
query them; every RAG/agent record carries ``trace_id``, token usage, cost and latency.
"""

from __future__ import annotations

import json
import logging
import os
import sys


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"level": record.levelname, "logger": record.name, "msg": record.getMessage(),
               "ts": round(record.created, 3)}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out)


def setup_logging(level: str | None = None) -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level or os.environ.get("LOG_LEVEL", "INFO"))


def langfuse_trace(name: str, payload: dict) -> None:
    """Send a trace to Langfuse when LANGFUSE_PUBLIC_KEY is set; no-op otherwise."""
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return
    try:
        from langfuse import Langfuse

        Langfuse().trace(name=name, input=payload.get("input"), output=payload.get("output"),
                         metadata={k: v for k, v in payload.items() if k not in ("input", "output")})
    except Exception:  # tracing must never break a request
        logging.getLogger(__name__).warning("langfuse trace failed", exc_info=True)
