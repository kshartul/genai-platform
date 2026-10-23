"""Guardrail layer: input, output and (via the tool registry) tool-call checks.

Local checks always run (cheap, deterministic, testable). When the profile names a managed
service, its verdict is combined with the local one: Amazon Bedrock Guardrails
(``ApplyGuardrail`` with contextual grounding) or Azure AI Content Safety (text analysis +
Prompt Shields).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import GuardrailConfig
from ..ingest.pii import PIIRedactor
from ..providers.local import tokenize
from ..types import ScoredChunk

INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above) (instructions|prompts?)",
    r"disregard (all |the )?(previous|prior|above|system)",
    r"(reveal|print|show|repeat) (your|the) (system )?(prompt|instructions)",
    r"you are now (?:in )?(dan|developer mode|jailbroken)",
    r"act as (an? )?(unfiltered|unrestricted)",
    r"<\s*/?\s*system\s*>",
    r"BEGIN (SYSTEM|ADMIN) (PROMPT|OVERRIDE)",
]
_INJ = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]
_CITE = re.compile(r"\[(\d+)\]")
_SENT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class GuardResult:
    allowed: bool
    text: str
    reasons: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "reasons": self.reasons, "scores": self.scores}


def groundedness(answer: str, contexts: list[str]) -> float:
    """Share of answer sentences whose content words are mostly present in the retrieved context.
    A cheap, deterministic proxy; the eval harness adds LLM-as-judge faithfulness on top."""
    ctx = set(tokenize(" ".join(contexts)))
    sents = [s for s in _SENT.split(_CITE.sub("", answer)) if tokenize(s)]
    if not sents:
        return 0.0
    supported = sum(1 for s in sents if len(set(tokenize(s)) & ctx) / len(set(tokenize(s))) >= 0.6)
    return supported / len(sents)


class Guardrails:
    def __init__(self, cfg: GuardrailConfig, region: str | None = None, endpoint: str | None = None):
        self.cfg = cfg
        self.pii = PIIRedactor()
        self._bedrock = None
        self._azure = None
        if cfg.provider == "bedrock":
            import boto3

            self._bedrock = boto3.client("bedrock-runtime", region_name=region)
        elif cfg.provider == "azure_content_safety":
            from azure.ai.contentsafety import ContentSafetyClient
            from azure.identity import DefaultAzureCredential

            self._azure = ContentSafetyClient(endpoint, DefaultAzureCredential())

    # ---------- input ----------
    def check_input(self, text: str) -> GuardResult:
        reasons: list[str] = []
        if len(text) > self.cfg.max_input_chars:
            reasons.append("input_too_long")
        if self.cfg.block_prompt_injection and any(p.search(text) for p in _INJ):
            reasons.append("prompt_injection")
        low = text.lower()
        reasons += [f"denied_topic:{t}" for t in self.cfg.denied_topics if t.lower() in low]
        clean = text
        if self.cfg.redact_pii:
            clean, found = self.pii.redact(text)
            if found:
                reasons.append("pii_redacted")  # informational, not blocking
        if self._bedrock is not None:
            reasons += self._bedrock_check(clean, "INPUT")
        if self._azure is not None:
            reasons += self._azure_check(clean)
        blocking = [r for r in reasons if r != "pii_redacted"]
        return GuardResult(not blocking, clean, reasons)

    # ---------- output ----------
    def check_output(self, answer: str, contexts: list[ScoredChunk], question: str = "") -> GuardResult:
        reasons: list[str] = []
        texts = [c.chunk.text for c in contexts]
        g = groundedness(answer, texts)
        abstained = answer.lower().startswith("i don't know")
        if not abstained and g < self.cfg.min_groundedness:
            reasons.append("low_groundedness")
        cites = [int(x) for x in _CITE.findall(answer)]
        if self.cfg.require_citations and not abstained and not cites:
            reasons.append("missing_citations")
        if any(c < 1 or c > len(contexts) for c in cites):
            reasons.append("invalid_citation")
        clean = answer
        if self.cfg.redact_pii:
            clean, found = self.pii.redact(answer)
            if found:
                reasons.append("pii_redacted")
        if self._bedrock is not None:
            reasons += self._bedrock_check(clean, "OUTPUT", grounding=" ".join(texts)[:20000], query=question)
        if self._azure is not None:
            reasons += self._azure_check(clean)
        blocking = [r for r in reasons if r != "pii_redacted"]
        return GuardResult(not blocking, clean, reasons, {"groundedness": round(g, 3)})

    # ---------- managed services ----------
    def _bedrock_check(self, text: str, source: str, grounding: str = "", query: str = "") -> list[str]:
        content = [{"text": {"text": text, "qualifiers": ["guard_content"]}}]
        if grounding:
            content += [{"text": {"text": grounding, "qualifiers": ["grounding_source"]}},
                        {"text": {"text": query, "qualifiers": ["query"]}}]
        r = self._bedrock.apply_guardrail(guardrailIdentifier=self.cfg.guardrail_id,
                                          guardrailVersion=self.cfg.guardrail_version or "DRAFT",
                                          source=source, content=content)
        return ["bedrock_guardrail_intervened"] if r.get("action") == "GUARDRAIL_INTERVENED" else []

    def _azure_check(self, text: str) -> list[str]:
        from azure.ai.contentsafety.models import AnalyzeTextOptions

        r = self._azure.analyze_text(AnalyzeTextOptions(text=text[:10000]))
        return [f"content_safety:{c.category}" for c in r.categories_analysis if (c.severity or 0) >= 4]
