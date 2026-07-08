from __future__ import annotations

from collections.abc import Callable, Sequence
from hashlib import sha256
import importlib
from typing import Protocol, cast

from financial_statements_rag.errors import RetryableProcessingError
from financial_statements_rag.settings import Settings

EMBEDDING_SERVICE_UNAVAILABLE_CODE = "EMBEDDING_SERVICE_UNAVAILABLE"
EMBEDDING_SERVICE_UNAVAILABLE_MESSAGE = "Embedding service is temporarily unavailable"


class EmbeddingClient(Protocol):
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingProvider(Protocol):
    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...


ClientFactory = Callable[..., EmbeddingClient]


def _default_client_factory(
    *, model: str, api_key: str, dimensions: int
) -> EmbeddingClient:
    langchain_openai = importlib.import_module("langchain_openai")
    client = langchain_openai.OpenAIEmbeddings(
        model=model,
        api_key=api_key,
        dimensions=dimensions,
    )
    return cast(EmbeddingClient, client)


def _is_transient_embedding_error(error: Exception) -> bool:
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return True
    return error.__class__.__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
    }


class DeterministicEmbeddingProvider:
    def __init__(self, *, dimension: int) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._dimension = dimension

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_text(text) for text in texts]

    def _embed_text(self, text: str) -> list[float]:
        digest = sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        for index in range(self._dimension):
            byte = digest[index % len(digest)]
            values.append(round(byte / 255.0, 6))
        return values


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory = _default_client_factory,
    ) -> None:
        self._settings = settings
        self._client = client_factory(
            model=settings.embedding_model,
            api_key=settings.openai_api_key,
            dimensions=settings.embedding_dimension,
        )

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            return await self._client.aembed_documents(list(texts))
        except Exception as error:
            if _is_transient_embedding_error(error):
                raise RetryableProcessingError(
                    EMBEDDING_SERVICE_UNAVAILABLE_CODE,
                    EMBEDDING_SERVICE_UNAVAILABLE_MESSAGE,
                ) from error
            raise


__all__ = [
    "DeterministicEmbeddingProvider",
    "EmbeddingProvider",
    "OpenAIEmbeddingProvider",
]
