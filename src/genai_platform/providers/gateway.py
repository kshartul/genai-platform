"""LLM gateway: the single entry point for model calls.

Adds what individual SDKs don't: ordered fallbacks, retries with backoff, exact-match and
semantic caching, and per-request usage and cost metering. Prices are configuration
(``prices_per_1k``), so cost reports follow your own rate card.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np

from .base import LLM, Embedder, LLMResult, Message

log = logging.getLogger(__name__)


@dataclass
class UsageMeter:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cache_hits: int = 0
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)

    def add(self, r: LLMResult, prices: dict[str, tuple[float, float]]):
        self.calls += 1
        if r.cached:
            self.cache_hits += 1
            return
        p_in, p_out = prices.get(r.model, (0.0, 0.0))
        cost = r.input_tokens / 1000 * p_in + r.output_tokens / 1000 * p_out
        self.input_tokens += r.input_tokens
        self.output_tokens += r.output_tokens
        self.cost_usd += cost
        m = self.by_model.setdefault(r.model, {"calls": 0, "cost_usd": 0.0})
        m["calls"] += 1
        m["cost_usd"] += cost

    def snapshot(self) -> dict:
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cost_usd": round(self.cost_usd, 6), "cache_hits": self.cache_hits}


class LLMGateway:
    def __init__(self, models: list[LLM], *, retries: int = 2, backoff_s: float = 0.5, cache_size: int = 512,
                 semantic_cache: Embedder | None = None, semantic_threshold: float = 0.97,
                 prices_per_1k: dict[str, tuple[float, float]] | None = None):
        if not models:
            raise ValueError("LLMGateway needs at least one model")
        self.models = models
        self.retries = retries
        self.backoff_s = backoff_s
        self._cache: OrderedDict[str, LLMResult] = OrderedDict()
        self._cache_size = cache_size
        self._sem = semantic_cache
        self._sem_thr = semantic_threshold
        self._sem_entries: list[tuple[np.ndarray, str, str]] = []  # (question vector, cache key, scope)
        self.prices = prices_per_1k or {}
        self.usage = UsageMeter()

    @property
    def name(self) -> str:
        return self.models[0].name

    def _key(self, messages, max_tokens, temperature, json_mode) -> str:
        raw = json.dumps([messages, max_tokens, temperature, json_mode], sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _remember(self, key: str, r: LLMResult):
        self._cache[key] = r
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def generate(self, messages: list[Message], *, max_tokens: int = 800, temperature: float = 0.0,
                 json_mode: bool = False, use_cache: bool = True, semantic_key: str | None = None,
                 cache_scope: str = "") -> LLMResult:
        """``semantic_key`` (normally the user question) enables the semantic cache for this call.
        ``cache_scope`` must encode everything that changes what the caller may see (e.g. ACL groups),
        so a cached answer is never served across permission boundaries."""
        key = self._key(messages, max_tokens, temperature, json_mode)
        if use_cache and temperature == 0 and key in self._cache:
            hit = self._cache[key]
            r = LLMResult(hit.text, hit.model, hit.input_tokens, hit.output_tokens, cached=True)
            self.usage.add(r, self.prices)
            return r
        qvec = None
        if use_cache and self._sem is not None and temperature == 0 and semantic_key:
            qvec = np.array(self._sem.embed([semantic_key], kind="query")[0])
            for vec, k, scope in self._sem_entries:
                if scope == cache_scope and float(vec @ qvec) >= self._sem_thr and k in self._cache:
                    hit = self._cache[k]
                    r = LLMResult(hit.text, hit.model, cached=True, meta={"semantic_cache": True})
                    self.usage.add(r, self.prices)
                    return r
        last_err: Exception | None = None
        for model in self.models:
            for attempt in range(self.retries + 1):
                try:
                    r = model.generate(messages, max_tokens=max_tokens, temperature=temperature, json_mode=json_mode)
                    self.usage.add(r, self.prices)
                    if use_cache and temperature == 0:
                        self._remember(key, r)
                        if qvec is not None:
                            self._sem_entries.append((qvec, key, cache_scope))
                    return r
                except Exception as e:  # noqa: BLE001 - provider SDKs raise many types
                    last_err = e
                    log.warning("model %s attempt %d failed: %s", model.name, attempt + 1, e)
                    time.sleep(self.backoff_s * (2**attempt))
            log.warning("falling back from %s", model.name)
        raise RuntimeError(f"all models failed: {last_err}")
