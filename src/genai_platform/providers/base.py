"""Provider interfaces. Every model call in the platform goes through these two protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

Message = dict[str, str]  # {"role": "system"|"user"|"assistant", "content": str}


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    meta: dict = field(default_factory=dict)


@runtime_checkable
class LLM(Protocol):
    name: str

    def generate(
        self, messages: list[Message], *, max_tokens: int = 800, temperature: float = 0.0, json_mode: bool = False
    ) -> LLMResult: ...


@runtime_checkable
class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]: ...


def approx_tokens(text: str) -> int:
    """Cheap token estimate (about 1.3 tokens per word) for budgeting and chunk sizing."""
    return max(1, int(len(text.split()) * 1.3))
