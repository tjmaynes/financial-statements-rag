from __future__ import annotations

from collections.abc import Iterable, Sequence
from hashlib import sha256
from pathlib import Path
import re

from sec_filings_rag.ingestion.indexing.models import (
    DocumentChunk,
    IndexedDocument,
    StatementSection,
)

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip()


def _stable_id(prefix: str, *parts: object) -> str:
    normalized_parts = [normalize_text(str(part)) for part in parts]
    digest = sha256("\x1f".join(normalized_parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def build_document_id(job_id: str, document_path: str | Path) -> str:
    path = Path(document_path).as_posix()
    return _stable_id("doc", job_id, path)


def build_section_id(
    document_id: str,
    *,
    statement_type: str,
    page_start: int,
    page_end: int,
    title: str,
) -> str:
    return _stable_id(
        "sec",
        document_id,
        statement_type,
        page_start,
        page_end,
        title.casefold(),
    )


def build_line_item_id(
    section_id: str | None,
    *,
    statement_type: str,
    page_number: int,
    line_item_label: str,
    period_label: str | None,
    raw_value_text: str | None,
) -> str:
    return _stable_id(
        "line",
        section_id or "",
        statement_type,
        page_number,
        line_item_label.casefold(),
        period_label or "",
        raw_value_text or "",
    )


def build_chunk_id(
    document_id: str,
    *,
    chunk_index: int,
    page_start: int,
    page_end: int,
    text: str,
) -> str:
    text_hash = sha256(normalize_text(text).encode("utf-8")).hexdigest()
    return _stable_id(
        "chunk",
        document_id,
        chunk_index,
        page_start,
        page_end,
        text_hash,
    )


def _split_text(text: str, *, chunk_size: int, chunk_overlap: int) -> list[str]:
    normalized = normalize_text(text)
    if not normalized:
        return []

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be between 0 and chunk_size - 1")

    step = chunk_size - chunk_overlap
    chunks: list[str] = []
    for start in range(0, len(normalized), step):
        chunk = normalized[start : start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(normalized):
            break
    return chunks


def _remove_section_texts(
    document_text: str, sections: Iterable[StatementSection]
) -> str:
    remaining_text = document_text
    for section in sections:
        raw_text = normalize_text(section.raw_text)
        if not raw_text:
            continue
        remaining_text = remaining_text.replace(section.raw_text, " ", 1)
        remaining_text = remaining_text.replace(raw_text, " ", 1)
    return normalize_text(remaining_text)


def _remaining_page_range(
    document: IndexedDocument,
    sections: Sequence[StatementSection],
) -> tuple[int, int]:
    if sections:
        return (
            min(section.page_start for section in sections),
            max(section.page_end for section in sections),
        )
    if document.page_count and document.page_count > 0:
        return (1, document.page_count)
    return (1, 1)


def create_document_chunks(
    document: IndexedDocument,
    *,
    document_text: str,
    sections: Sequence[StatementSection],
    chunk_size: int,
    chunk_overlap: int,
    index_remaining_text: bool,
) -> list[DocumentChunk]:
    chunks: list[DocumentChunk] = []

    sorted_sections = sorted(
        sections,
        key=lambda section: (
            section.page_start,
            section.page_end,
            section.statement_type,
            section.title.casefold(),
        ),
    )

    for section in sorted_sections:
        for text in _split_text(
            section.raw_text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        ):
            chunk_index = len(chunks)
            chunks.append(
                DocumentChunk(
                    chunk_id=build_chunk_id(
                        document.document_id,
                        chunk_index=chunk_index,
                        page_start=section.page_start,
                        page_end=section.page_end,
                        text=text,
                    ),
                    document_id=document.document_id,
                    section_id=section.section_id,
                    job_id=document.job_id,
                    chunk_index=chunk_index,
                    text=text,
                    company_name=document.company_name,
                    ticker=document.ticker,
                    fiscal_year=document.fiscal_year,
                    fiscal_quarter=document.fiscal_quarter,
                    report_date=document.report_date,
                    report_type=document.report_type,
                    statement_type=section.statement_type,
                    section_title=section.title,
                    page_start=section.page_start,
                    page_end=section.page_end,
                ),
            )

    if not index_remaining_text:
        return chunks

    remaining_text = _remove_section_texts(document_text, sorted_sections)
    if not remaining_text:
        return chunks

    page_start, page_end = _remaining_page_range(document, sorted_sections)
    for text in _split_text(
        remaining_text,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    ):
        chunk_index = len(chunks)
        chunks.append(
            DocumentChunk(
                chunk_id=build_chunk_id(
                    document.document_id,
                    chunk_index=chunk_index,
                    page_start=page_start,
                    page_end=page_end,
                    text=text,
                ),
                document_id=document.document_id,
                section_id=None,
                job_id=document.job_id,
                chunk_index=chunk_index,
                text=text,
                company_name=document.company_name,
                ticker=document.ticker,
                fiscal_year=document.fiscal_year,
                fiscal_quarter=document.fiscal_quarter,
                report_date=document.report_date,
                report_type=document.report_type,
                statement_type=None,
                section_title=None,
                page_start=page_start,
                page_end=page_end,
            ),
        )

    return chunks


__all__ = [
    "DocumentChunk",
    "IndexedDocument",
    "StatementSection",
    "build_chunk_id",
    "build_document_id",
    "build_line_item_id",
    "build_section_id",
    "create_document_chunks",
    "normalize_text",
]
