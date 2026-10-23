"""Chunking strategies. All share one interface: ``chunk(Document) -> list[Chunk]``.

* ``RecursiveChunker``: split by heading, then paragraph, then sentence; pack to a token target
  with overlap. Default; strong baseline.
* ``SemanticChunker``: break where the embedding distance between neighbouring sentences
  spikes (percentile threshold), capped at the token target.
* ``ParentChildChunker``: retrieve on small child chunks, hand the larger parent to the model.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import numpy as np

from ..providers.base import Embedder, approx_tokens
from ..types import Chunk, Document

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[(])")


@dataclass
class Section:
    path: str
    text: str


def split_sections(doc: Document) -> list[Section]:
    stack: list[str] = []
    sections: list[Section] = []
    buf: list[str] = []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            sections.append(Section(" > ".join(stack) or doc.title, body))
        buf.clear()

    for line in doc.text.splitlines():
        m = _HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            stack[:] = stack[: level - 1] + [m.group(2).strip()]
        else:
            buf.append(line)
    flush()
    return sections


def _units(text: str) -> list[str]:
    """Paragraphs, with tables kept whole and long paragraphs split into sentences."""
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if para.lstrip().startswith("|"):
            units.append(para)  # never split a table
        else:
            units.extend(s.strip() for s in _SENT.split(para.replace("\n", " ")) if s.strip())
    return units


def _join(units: list[str]) -> str:
    """Join units with a space, but keep tables on their own lines so they stay parseable."""
    out = ""
    for u in units:
        sep = "" if not out else ("\n\n" if u.startswith("|") or out.endswith("|") else " ")
        out += sep + u
    return out


def _pack_groups(units: list[str], target: int, overlap: int) -> list[list[str]]:
    groups, cur, cur_tok = [], [], 0
    for u in units:
        t = approx_tokens(u)
        if cur and cur_tok + t > target:
            groups.append(cur)
            tail, tail_tok = [], 0
            for prev in reversed(cur):
                tail_tok += approx_tokens(prev)
                if tail_tok > overlap:
                    break
                tail.insert(0, prev)
            cur, cur_tok = tail, sum(approx_tokens(x) for x in tail)
        cur.append(u)
        cur_tok += t
    if cur:
        groups.append(cur)
    return groups


def _pack(units: list[str], target: int, overlap: int) -> list[str]:
    return [_join(g) for g in _pack_groups(units, target, overlap)]


def _make(doc: Document, text: str, section: str, pos: int, contextual: bool, **kw) -> Chunk:
    h = hashlib.sha256(f"{doc.id}|{section}|{text}".encode()).hexdigest()
    return Chunk(id=f"{doc.id}-{pos:04d}", doc_id=doc.id, text=text, source=doc.source, section=section,
                 position=pos, acl=doc.acl, content_hash=h,
                 context_header=f"Document: {doc.title} | Section: {section}" if contextual else "",
                 metadata={"title": doc.title, "doc_type": doc.doc_type, **doc.metadata}, **kw)


class RecursiveChunker:
    def __init__(self, target_tokens: int = 400, overlap_tokens: int = 50, contextual_headers: bool = True):
        self.target, self.overlap, self.ctx = target_tokens, overlap_tokens, contextual_headers

    def chunk(self, doc: Document) -> list[Chunk]:
        out: list[Chunk] = []
        for sec in split_sections(doc):
            for text in _pack(_units(sec.text), self.target, self.overlap):
                out.append(_make(doc, text, sec.path, len(out), self.ctx))
        return out


class SemanticChunker:
    def __init__(self, embedder: Embedder, target_tokens: int = 400, percentile: float = 90.0,
                 contextual_headers: bool = True):
        self.emb, self.target, self.pct, self.ctx = embedder, target_tokens, percentile, contextual_headers

    def chunk(self, doc: Document) -> list[Chunk]:
        out: list[Chunk] = []
        for sec in split_sections(doc):
            units = _units(sec.text)
            if len(units) <= 2:
                groups = [units]
            else:
                vecs = np.array(self.emb.embed(units))
                dist = 1 - np.sum(vecs[:-1] * vecs[1:], axis=1)
                cut = float(np.percentile(dist, self.pct))
                groups, cur, tok = [], [units[0]], approx_tokens(units[0])
                for i, u in enumerate(units[1:]):
                    t = approx_tokens(u)
                    if dist[i] >= cut or tok + t > self.target:
                        groups.append(cur)
                        cur, tok = [], 0
                    cur.append(u)
                    tok += t
                groups.append(cur)
            for g in groups:
                if g:
                    out.append(_make(doc, _join(g), sec.path, len(out), self.ctx))
        return out


class ParentChildChunker:
    def __init__(self, parent_tokens: int = 600, child_tokens: int = 120, contextual_headers: bool = True):
        self.parent_tokens, self.child_tokens, self.ctx = parent_tokens, child_tokens, contextual_headers

    def chunk(self, doc: Document) -> list[Chunk]:
        out: list[Chunk] = []
        for sec in split_sections(doc):
            for p_idx, parent_units in enumerate(_pack_groups(_units(sec.text), self.parent_tokens, 0)):
                parent_text = _join(parent_units)
                parent_id = f"{doc.id}-p{len(out):04d}-{p_idx}"
                for child in _pack(parent_units, self.child_tokens, 0):
                    out.append(_make(doc, child, sec.path, len(out), self.ctx,
                                     parent_id=parent_id, parent_text=parent_text))
        return out


def build_chunker(cfg, embedder: Embedder):
    if cfg.strategy == "semantic":
        return SemanticChunker(embedder, cfg.target_tokens, cfg.semantic_breakpoint_percentile, cfg.contextual_headers)
    if cfg.strategy == "parent_child":
        return ParentChildChunker(cfg.target_tokens, cfg.child_tokens, cfg.contextual_headers)
    return RecursiveChunker(cfg.target_tokens, cfg.overlap_tokens, cfg.contextual_headers)
