"""Shared data contracts between modules."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Document(BaseModel):
    id: str
    text: str
    source: str
    title: str = ""
    doc_type: str = "text"
    acl: list[str] = Field(default_factory=lambda: ["public"])
    metadata: dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    id: str
    doc_id: str
    text: str
    source: str
    section: str = ""
    position: int = 0
    parent_id: str | None = None
    parent_text: str | None = None
    context_header: str = ""
    acl: list[str] = Field(default_factory=lambda: ["public"])
    content_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding: list[float] | None = None

    def embed_text(self) -> str:
        return f"{self.context_header}\n{self.text}" if self.context_header else self.text


class ScoredChunk(BaseModel):
    chunk: Chunk
    score: float
    retriever: str = ""


class Citation(BaseModel):
    chunk_id: str
    source: str
    section: str = ""


class Answer(BaseModel):
    question: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    contexts: list[ScoredChunk] = Field(default_factory=list)
    trace_id: str = ""
    guardrail: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    blocked: bool = False
