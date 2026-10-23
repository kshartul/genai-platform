"""Amazon Bedrock providers (Converse API for chat, Titan / Cohere for embeddings).

Credentials come from the standard AWS chain (IAM role on ECS/Lambda, SSO locally).
Calls go through the Bedrock VPC interface endpoint provisioned in infra/terraform.
"""

from __future__ import annotations

import json

from .base import LLMResult, Message


def _client(service: str, region: str | None):
    import boto3  # lazy: only needed when provider == aws

    return boto3.client(service, region_name=region)


class BedrockLLM:
    def __init__(self, model_id: str, region: str | None = None):
        self.name = model_id
        self.model_id = model_id
        self._rt = _client("bedrock-runtime", region)

    def generate(self, messages: list[Message], *, max_tokens: int = 800, temperature: float = 0.0,
                 json_mode: bool = False) -> LLMResult:
        system = [{"text": m["content"]} for m in messages if m["role"] == "system"]
        if json_mode:
            system.append({"text": "Respond with a single valid JSON object and nothing else."})
        convo = [{"role": m["role"], "content": [{"text": m["content"]}]} for m in messages if m["role"] != "system"]
        resp = self._rt.converse(
            modelId=self.model_id,
            messages=convo,
            system=system,
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
        )
        text = "".join(c.get("text", "") for c in resp["output"]["message"]["content"])
        usage = resp.get("usage", {})
        return LLMResult(text, self.model_id, usage.get("inputTokens", 0), usage.get("outputTokens", 0))


class BedrockEmbedder:
    def __init__(self, model_id: str = "amazon.titan-embed-text-v2:0", dim: int = 1024, region: str | None = None):
        self.name = model_id
        self.model_id = model_id
        self.dim = dim
        self._rt = _client("bedrock-runtime", region)

    def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        if self.model_id.startswith("cohere."):
            body = {"texts": texts, "input_type": "search_query" if kind == "query" else "search_document"}
            out = json.loads(self._rt.invoke_model(modelId=self.model_id, body=json.dumps(body))["body"].read())
            return out["embeddings"]
        vectors = []
        for t in texts:  # Titan v2 embeds one text per call
            body = {"inputText": t, "dimensions": self.dim, "normalize": True}
            out = json.loads(self._rt.invoke_model(modelId=self.model_id, body=json.dumps(body))["body"].read())
            vectors.append(out["embedding"])
        return vectors
