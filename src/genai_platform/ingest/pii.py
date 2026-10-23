"""PII detection and redaction applied before indexing and before output.

Regex detectors cover the common structured identifiers. For production, the same interface
is backed by Amazon Comprehend ``detect_pii_entities`` or Azure AI Language PII detection
(see ``CloudPIIRedactor``); the regex layer stays on as a cheap first pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


def _luhn_ok(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(d) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(d)):
        if i % 2:
            n *= 2
            n -= 9 if n > 9 else 0
        total += n
    return total % 10 == 0


PATTERNS: dict[str, re.Pattern] = {
    "EMAIL": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "CARD": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "AADHAAR": re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b"),
    "PAN": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "PHONE": re.compile(r"(?<!\w)(?:\+\d{1,3}[\s-]?)?(?:\(\d{2,4}\)[\s-]?)?\d{3,5}[\s-]\d{3,4}[\s-]?\d{0,4}(?!\w)"),
}


@dataclass
class PIIFinding:
    kind: str
    start: int
    end: int


class PIIRedactor:
    def __init__(self, kinds: list[str] | None = None):
        self.kinds = kinds or list(PATTERNS)

    def find(self, text: str) -> list[PIIFinding]:
        out: list[PIIFinding] = []
        for kind in self.kinds:
            for m in PATTERNS[kind].finditer(text):
                if kind == "CARD" and not _luhn_ok(m.group()):
                    continue
                if kind == "PHONE" and sum(c.isdigit() for c in m.group()) < 8:
                    continue
                out.append(PIIFinding(kind, m.start(), m.end()))
        # drop overlaps, keep the earliest/longest
        out.sort(key=lambda f: (f.start, -(f.end - f.start)))
        merged: list[PIIFinding] = []
        for f in out:
            if merged and f.start < merged[-1].end:
                continue
            merged.append(f)
        return merged

    def redact(self, text: str) -> tuple[str, list[PIIFinding]]:
        findings = self.find(text)
        for f in reversed(findings):
            text = text[: f.start] + f"[{f.kind}]" + text[f.end :]
        return text, findings


class CloudPIIRedactor(PIIRedactor):
    """Regex pass, then Amazon Comprehend (aws) or Azure AI Language (azure) for names/addresses."""

    def __init__(self, cloud: str, region: str | None = None, endpoint: str | None = None):
        super().__init__()
        self.cloud, self.region, self.endpoint = cloud, region, endpoint

    def redact(self, text: str):
        text, findings = super().redact(text)
        if self.cloud == "aws":
            import boto3

            ents = boto3.client("comprehend", region_name=self.region).detect_pii_entities(
                Text=text[:99000], LanguageCode="en")["Entities"]
            for e in sorted(ents, key=lambda e: -e["BeginOffset"]):
                text = text[: e["BeginOffset"]] + f"[{e['Type']}]" + text[e["EndOffset"] :]
        elif self.cloud == "azure":
            from azure.ai.textanalytics import TextAnalyticsClient
            from azure.identity import DefaultAzureCredential

            c = TextAnalyticsClient(self.endpoint, DefaultAzureCredential())
            text = c.recognize_pii_entities([text])[0].redacted_text
        return text, findings
