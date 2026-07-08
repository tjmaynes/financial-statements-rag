from __future__ import annotations

import asyncio
import os

import pytest

from sec_filings_rag.errors import RetryableProcessingError
from sec_filings_rag.ingestion.indexing.embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingClient,
    OpenAIEmbeddingProvider,
)
from sec_filings_rag.settings import Settings


class RecordingEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]


class TimeoutEmbeddingClient:
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        raise TimeoutError("upstream timeout")


def test_deterministic_embedding_provider_is_stable() -> None:
    provider = DeterministicEmbeddingProvider(dimension=6)

    first = asyncio.run(provider.embed_texts(["Revenue rose 20%"]))
    second = asyncio.run(provider.embed_texts(["Revenue rose 20%"]))

    assert first == second
    assert len(first[0]) == 6
    assert all(isinstance(value, float) for value in first[0])


def test_openai_provider_uses_settings_api_key_only() -> None:
    captured: dict[str, object] = {}
    client = RecordingEmbeddingClient()

    def client_factory(
        *,
        model: str,
        api_key: str,
        dimensions: int,
    ) -> EmbeddingClient:
        captured["model"] = model
        captured["api_key"] = api_key
        captured["dimensions"] = dimensions
        return client

    settings = Settings(
        openai_api_key="sk-configured",
        embedding_model="text-embedding-3-small",
        embedding_dimension=1536,
    )
    os.environ["OPENAI_API_KEY"] = "ignored"

    provider = OpenAIEmbeddingProvider(settings, client_factory=client_factory)
    embeddings = asyncio.run(provider.embed_texts(["alpha", "beta"]))

    assert captured == {
        "model": "text-embedding-3-small",
        "api_key": "sk-configured",
        "dimensions": 1536,
    }
    assert client.calls == [["alpha", "beta"]]
    assert embeddings == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]


def test_transient_embedding_failure_becomes_retryable() -> None:
    def client_factory(
        *,
        model: str,
        api_key: str,
        dimensions: int,
    ) -> EmbeddingClient:
        return TimeoutEmbeddingClient()

    settings = Settings(
        openai_api_key="sk-configured",
        embedding_model="text-embedding-3-small",
        embedding_dimension=1536,
    )
    provider = OpenAIEmbeddingProvider(settings, client_factory=client_factory)

    with pytest.raises(
        RetryableProcessingError,
        match="Embedding service is temporarily unavailable",
    ):
        asyncio.run(provider.embed_texts(["cash flow statement"]))
