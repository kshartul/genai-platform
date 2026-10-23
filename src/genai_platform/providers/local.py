"""Offline providers so the whole platform runs and is testable with no cloud account.

* ``HashEmbedder``: deterministic hashed bag-of-words + bigram vectors (L2-normalised).
* ``ExtractiveLLM``: answers by selecting the context sentences that best match the question
  and citing them as [n]. It is a stand-in for a real model, not a model; swap the profile's
  ``models.provider`` to ``aws`` or ``azure`` for real generation.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re

import numpy as np

from .base import LLMResult, Message, approx_tokens

_WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = {"a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "is", "are", "was", "were", "be", "by", "with", "as", "at", "from", "that", "this", "it", "its", "what", "which", "who", "how", "when", "where", "why", "do", "does", "did", "can", "could", "should", "would", "will", "may", "i", "we", "you", "they", "our", "your", "their", "me", "my", "not", "no"}


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in STOPWORDS]


class HashEmbedder:
    def __init__(self, dim: int = 384, name: str = "local-hash-384"):
        self.dim = dim
        self.name = name

    def _vec(self, text: str) -> list[float]:
        v = np.zeros(self.dim, dtype=np.float32)
        toks = tokenize(text)
        feats = toks + [f"{a}_{b}" for a, b in itertools.pairwise(toks)]
        for f in feats:
            h = int(hashlib.md5(f.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 8) & 1 else -1.0
            # light stemming signal: share a bucket on 5-char prefixes
            p = int(hashlib.md5(f[:5].encode()).hexdigest(), 16)
            v[p % self.dim] += 0.5
        n = np.linalg.norm(v)
        return (v / n).tolist() if n else v.tolist()

    def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        return [self._vec(t) for t in texts]


_SENT = re.compile(r"(?<=[.!?])\s+")
_CTX = re.compile(r"^\[(\d+)\]\s*(?:\(([^)]*)\))?\s*(.*)$")


class ExtractiveLLM:
    """Deterministic stand-in LLM. Understands the platform's RAG prompt layout:
    context blocks as lines starting ``[n] (source) text`` and a final ``Question:`` line."""

    def __init__(self, name: str = "local-extractive", max_sentences: int = 3):
        self.name = name
        self.max_sentences = max_sentences

    def generate(self, messages: list[Message], *, max_tokens: int = 800, temperature: float = 0.0,
                 json_mode: bool = False) -> LLMResult:
        prompt = "\n".join(m["content"] for m in messages)
        if json_mode:
            out = json.dumps({"note": "local provider has no JSON reasoning; use a cloud provider"})
            return LLMResult(out, self.name, approx_tokens(prompt), approx_tokens(out))
        question = ""
        blocks: list[tuple[str, str]] = []
        current = None
        for line in prompt.splitlines():
            if line.lower().startswith("question:"):
                question = line.split(":", 1)[1].strip()
                continue
            m = _CTX.match(line.strip())
            if m:
                current = [m.group(1), m.group(3)]
                blocks.append(current)  # type: ignore[arg-type]
            elif current is not None and line.strip() and not line.lower().startswith(("answer", "rules")):
                current[1] += " " + line.strip()
        if not question:
            question = messages[-1]["content"]
        qtok = set(tokenize(question))
        scored = []
        for ref, text in blocks:
            for s in _SENT.split(text):
                stoks = set(tokenize(s))
                if not stoks:
                    continue
                overlap = len(qtok & stoks)
                if overlap:
                    scored.append((overlap / math.sqrt(len(stoks)), ref, s.strip()))
        scored.sort(key=lambda x: -x[0])
        if not scored:
            out = "I don't know based on the provided sources."
        else:
            picked, seen = [], set()
            top = scored[0][0]
            for score, ref, s in scored:
                if score < 0.6 * top:  # keep only sentences close to the best match
                    break
                if s in seen:
                    continue
                seen.add(s)
                picked.append(f"{s} [{ref}]")
                if len(picked) >= self.max_sentences:
                    break
            out = " ".join(picked)
        return LLMResult(out, self.name, approx_tokens(prompt), approx_tokens(out))
