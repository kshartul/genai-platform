"""Azure OpenAI providers. Auth uses Entra ID (managed identity) by default; an API key is
accepted from AZURE_OPENAI_API_KEY for local development only."""

from __future__ import annotations

import os

from .base import LLMResult, Message


def _client(endpoint: str, api_version: str):
    from openai import AzureOpenAI  # lazy: only needed when provider == azure

    key = os.environ.get("AZURE_OPENAI_API_KEY")
    if key:
        return AzureOpenAI(azure_endpoint=endpoint, api_version=api_version, api_key=key)
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    token = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
    return AzureOpenAI(azure_endpoint=endpoint, api_version=api_version, azure_ad_token_provider=token)


class AzureOpenAILLM:
    def __init__(self, deployment: str, endpoint: str, api_version: str = "2024-10-21"):
        self.name = deployment
        self.deployment = deployment
        self._c = _client(endpoint, api_version)

    def generate(self, messages: list[Message], *, max_tokens: int = 800, temperature: float = 0.0,
                 json_mode: bool = False) -> LLMResult:
        kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
        r = self._c.chat.completions.create(
            model=self.deployment, messages=messages, max_tokens=max_tokens, temperature=temperature, **kwargs
        )
        u = r.usage
        return LLMResult(r.choices[0].message.content or "", self.deployment,
                         u.prompt_tokens if u else 0, u.completion_tokens if u else 0)


class AzureOpenAIEmbedder:
    def __init__(self, deployment: str, endpoint: str, dim: int = 1536, api_version: str = "2024-10-21"):
        self.name = deployment
        self.deployment = deployment
        self.dim = dim
        self._c = _client(endpoint, api_version)

    def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        r = self._c.embeddings.create(model=self.deployment, input=texts, dimensions=self.dim)
        return [d.embedding for d in r.data]
