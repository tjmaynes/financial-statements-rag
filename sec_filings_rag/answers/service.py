from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
import importlib
import re
from typing import Protocol, cast

from sec_filings_rag.answers.history import (
    ConversationHistoryEntry,
    ConversationHistoryWriter,
)
from sec_filings_rag.errors import QuestionAnsweringUnavailableError
from sec_filings_rag.retrieval import ChunkSearchFilters, RetrievedChunk
from sec_filings_rag.settings import Settings

UNKNOWN_ANSWER = "I do not know based on the provided context."
PROMPT_INJECTION_DETAIL = "prompt-injection detected"
_CITATION_PATTERN = re.compile(r"\[(\d+)\]")
_WHITESPACE_BEFORE_PUNCTUATION_PATTERN = re.compile(r"\s+([,.;:!?])")
_MULTISPACE_PATTERN = re.compile(r"\s{2,}")


class ChatClient(Protocol):
    async def ainvoke(self, input: str) -> object: ...


class AnswerEmbeddingProvider(Protocol):
    async def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class ChatCompletionProvider(Protocol):
    async def complete(self, prompt: str) -> str: ...


class ChunkSearchProvider(Protocol):
    def search(
        self,
        *,
        embedding: Sequence[float],
        filters: ChunkSearchFilters,
        limit: int,
    ) -> list[RetrievedChunk]: ...


class AnsweringService(Protocol):
    async def answer(self, request: AnswerRequest) -> AnswerResult: ...


ChatClientFactory = Callable[..., ChatClient]
NowFactory = Callable[[], datetime]


@dataclass(frozen=True)
class PromptInjectionInspection:
    blocked: bool
    reason: str | None = None


@dataclass(frozen=True)
class AnswerRequest:
    question: str
    company_name: str | None
    ticker: str | None
    fiscal_year: int
    statement_types: tuple[str, ...] | None
    limit: int


@dataclass(frozen=True)
class AnswerCitation:
    index: int
    chunk_id: str
    document_id: str
    company_name: str | None
    ticker: str | None
    report_date: date | None
    statement_type: str | None
    section_title: str | None
    page_start: int
    page_end: int
    text: str


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    citations: tuple[AnswerCitation, ...]


@dataclass(frozen=True)
class _ContextChunk:
    chunk: RetrievedChunk
    text: str


def _default_chat_client_factory(*, model: str, api_key: str) -> ChatClient:
    langchain_openai = importlib.import_module("langchain_openai")
    client = langchain_openai.ChatOpenAI(
        model=model,
        api_key=api_key,
        temperature=0,
    )
    return cast(ChatClient, client)


def _is_transient_chat_error(error: Exception) -> bool:
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return True
    return error.__class__.__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "ServiceUnavailableError",
    }


class OpenAIChatCompletionProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ChatClientFactory = _default_chat_client_factory,
    ) -> None:
        self._client = client_factory(
            model=settings.chat_model,
            api_key=settings.openai_api_key,
        )

    async def complete(self, prompt: str) -> str:
        try:
            response = await self._client.ainvoke(prompt)
        except Exception as error:
            if _is_transient_chat_error(error):
                raise QuestionAnsweringUnavailableError() from error
            raise
        return _response_text(response)


class PromptInjectionDetector:
    _PHRASES = (
        "ignore previous instructions",
        "ignore the previous instructions",
        "ignore the above instructions",
        "disregard previous instructions",
        "reveal the system prompt",
        "show the system prompt",
        "show hidden prompt",
        "developer instructions",
        "begin system prompt",
        "<system>",
        "</system>",
    )

    def inspect(self, question: str) -> PromptInjectionInspection:
        normalized = " ".join(question.lower().split())
        if any(phrase in normalized for phrase in self._PHRASES):
            return PromptInjectionInspection(
                blocked=True,
                reason=PROMPT_INJECTION_DETAIL,
            )
        return PromptInjectionInspection(blocked=False)


class AnswerService:
    def __init__(
        self,
        *,
        embedding_provider: AnswerEmbeddingProvider,
        search_service: ChunkSearchProvider,
        chat_provider: ChatCompletionProvider,
        conversation_history: ConversationHistoryWriter | None = None,
        prompt_injection_detector: PromptInjectionDetector | None = None,
        max_context_characters: int = 12_000,
        now_factory: NowFactory | None = None,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._search_service = search_service
        self._chat_provider = chat_provider
        self._conversation_history = conversation_history
        self._prompt_injection_detector = (
            prompt_injection_detector or PromptInjectionDetector()
        )
        self._max_context_characters = max_context_characters
        self._now_factory = now_factory or _utc_now

    async def answer(self, request: AnswerRequest) -> AnswerResult:
        inspection = self._prompt_injection_detector.inspect(request.question)
        if inspection.blocked:
            raise ValueError(inspection.reason or PROMPT_INJECTION_DETAIL)

        embeddings = await self._embedding_provider.embed_texts([request.question])
        if not embeddings or not embeddings[0]:
            raise QuestionAnsweringUnavailableError()
        question_embedding = tuple(float(value) for value in embeddings[0])
        normalized_statement_types = _normalize_statement_types(request.statement_types)
        filters = ChunkSearchFilters(
            company_name=request.company_name,
            ticker=request.ticker,
            fiscal_year=request.fiscal_year,
            statement_types=normalized_statement_types,
        )
        retrieved_chunks = self._search_service.search(
            embedding=question_embedding,
            filters=filters,
            limit=request.limit,
        )
        context_chunks = self._fit_chunks_to_budget(retrieved_chunks)
        prompt = self._build_prompt(request.question, context_chunks)
        model_answer = await self._chat_provider.complete(prompt)

        if model_answer.strip() == UNKNOWN_ANSWER:
            result = AnswerResult(answer=UNKNOWN_ANSWER, citations=())
            self._persist_history(
                request=request,
                statement_types=normalized_statement_types,
                result=result,
            )
            return result

        result = AnswerResult(
            answer=_repair_citations(model_answer, len(context_chunks)),
            citations=tuple(
                _citation_from_context_chunk(index, context_chunk)
                for index, context_chunk in enumerate(context_chunks, start=1)
            ),
        )
        self._persist_history(
            request=request,
            statement_types=normalized_statement_types,
            result=result,
        )
        return result

    def _build_prompt(
        self,
        question: str,
        context_chunks: Sequence[_ContextChunk],
    ) -> str:
        context = "\n\n".join(
            _context_block(index, context_chunk)
            for index, context_chunk in enumerate(context_chunks, start=1)
        )
        return "\n".join(
            [
                "You answer questions about SEC filings using only the provided context.",
                "If the context is insufficient, say you do not know.",
                "Cite the chunk sources you used with inline references like [1].",
                "Use only citation numbers that correspond to the provided context blocks.",
                f'Answer exactly "{UNKNOWN_ANSWER}" when the context does not support the answer.',
                "",
                "Question:",
                question,
                "",
                "Context:",
                context,
            ]
        )

    def _fit_chunks_to_budget(
        self,
        retrieved_chunks: Sequence[RetrievedChunk],
    ) -> list[_ContextChunk]:
        included: list[_ContextChunk] = []
        remaining = self._max_context_characters

        for chunk in retrieved_chunks:
            index = len(included) + 1
            separator = "\n\n" if included else ""
            header = _context_header(index, chunk)
            full_block = f"{header}\n{chunk.text}"
            required = len(separator) + len(full_block)

            if required <= remaining:
                included.append(_ContextChunk(chunk=chunk, text=chunk.text))
                remaining -= required
                continue

            available_text = remaining - len(separator) - len(header) - 1
            if available_text <= 0:
                break

            included.append(
                _ContextChunk(
                    chunk=chunk,
                    text=chunk.text[:available_text],
                )
            )
            break

        return included

    def _persist_history(
        self,
        *,
        request: AnswerRequest,
        statement_types: tuple[str, ...] | None,
        result: AnswerResult,
    ) -> None:
        if self._conversation_history is None:
            return
        self._conversation_history.append(
            ConversationHistoryEntry(
                question=request.question,
                answer=result.answer,
                citations=tuple(
                    _citation_history_payload(citation) for citation in result.citations
                ),
                company_name=request.company_name,
                ticker=request.ticker,
                fiscal_year=request.fiscal_year,
                statement_types=statement_types or (),
                created_at=self._now_factory(),
            )
        )


def _normalize_statement_types(
    statement_types: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    if statement_types is None:
        return None
    unique_statement_types = tuple(dict.fromkeys(statement_types))
    return unique_statement_types or None


def _context_header(index: int, chunk: RetrievedChunk) -> str:
    company_name = chunk.company_name or "Unknown"
    ticker = chunk.ticker or "Unknown"
    statement_type = chunk.statement_type or "Unknown"
    return (
        f"[{index}] Company: {company_name} | Ticker: {ticker} | "
        f"Statement: {statement_type} | Pages: {chunk.page_start}-{chunk.page_end}"
    )


def _context_block(index: int, context_chunk: _ContextChunk) -> str:
    return f"{_context_header(index, context_chunk.chunk)}\n{context_chunk.text}"


def _citation_from_context_chunk(
    index: int,
    context_chunk: _ContextChunk,
) -> AnswerCitation:
    chunk = context_chunk.chunk
    return AnswerCitation(
        index=index,
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        company_name=chunk.company_name,
        ticker=chunk.ticker,
        report_date=chunk.report_date,
        statement_type=chunk.statement_type,
        section_title=chunk.section_title,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        text=context_chunk.text,
    )


def _citation_history_payload(citation: AnswerCitation) -> dict[str, object]:
    return {
        "index": citation.index,
        "chunk_id": citation.chunk_id,
        "document_id": citation.document_id,
        "company_name": citation.company_name,
        "ticker": citation.ticker,
        "report_date": (
            citation.report_date.isoformat()
            if citation.report_date is not None
            else None
        ),
        "statement_type": citation.statement_type,
        "section_title": citation.section_title,
        "page_start": citation.page_start,
        "page_end": citation.page_end,
        "text": citation.text,
    }


def _repair_citations(answer: str, citation_count: int) -> str:
    normalized_answer = answer.strip()
    if normalized_answer == UNKNOWN_ANSWER:
        return UNKNOWN_ANSWER

    citations = [
        int(match.group(1)) for match in _CITATION_PATTERN.finditer(normalized_answer)
    ]
    if citations and all(1 <= index <= citation_count for index in citations):
        return normalized_answer

    stripped_answer = _CITATION_PATTERN.sub("", normalized_answer)
    stripped_answer = _WHITESPACE_BEFORE_PUNCTUATION_PATTERN.sub(r"\1", stripped_answer)
    stripped_answer = _MULTISPACE_PATTERN.sub(" ", stripped_answer).strip()
    if citation_count == 0:
        return stripped_answer
    trailing_citations = "".join(f"[{index}]" for index in range(1, citation_count + 1))
    if stripped_answer == "":
        return trailing_citations
    return f"{stripped_answer} {trailing_citations}"


def _response_text(response: object) -> str:
    if isinstance(response, str):
        return response

    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            text = getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
                continue
            if isinstance(part, dict):
                value = part.get("text")
                if isinstance(value, str):
                    parts.append(value)
        return "".join(parts)
    raise TypeError("Chat completion response did not contain text content")


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "AnswerCitation",
    "AnswerEmbeddingProvider",
    "AnsweringService",
    "AnswerRequest",
    "AnswerResult",
    "AnswerService",
    "ChatCompletionProvider",
    "ChunkSearchProvider",
    "OpenAIChatCompletionProvider",
    "PROMPT_INJECTION_DETAIL",
    "PromptInjectionDetector",
    "PromptInjectionInspection",
    "UNKNOWN_ANSWER",
]
