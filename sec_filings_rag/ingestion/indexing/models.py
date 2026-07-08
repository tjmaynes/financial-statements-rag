from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class IndexedDocument:
    document_id: str
    job_id: str
    stored_path: str
    original_filename: str | None = None
    company_name: str | None = None
    ticker: str | None = None
    fiscal_year: int | None = None
    fiscal_quarter: str | None = None
    report_date: date | None = None
    report_type: str | None = None
    currency: str | None = None
    scale: str | None = None
    extraction_warnings: tuple[str, ...] = ()
    page_count: int | None = None

    @classmethod
    def from_path(cls, *, job_id: str, stored_path: Path) -> "IndexedDocument":
        return cls(
            document_id=job_id,
            job_id=job_id,
            stored_path=stored_path.as_posix(),
        )


@dataclass(frozen=True)
class StatementSection:
    section_id: str
    document_id: str
    statement_type: str
    title: str
    page_start: int
    page_end: int
    raw_text: str
    confidence: float


@dataclass(frozen=True)
class StatementLineItem:
    line_item_id: str
    section_id: str | None
    document_id: str
    statement_type: str
    line_item_label: str
    raw_value_text: str | None
    period_label: str | None
    currency: str | None
    scale: str | None
    page_number: int
    source_text: str
    confidence: float


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    document_id: str
    section_id: str | None
    job_id: str
    chunk_index: int
    text: str
    page_start: int
    page_end: int
    company_name: str | None = None
    ticker: str | None = None
    fiscal_year: int | None = None
    fiscal_quarter: str | None = None
    report_date: date | None = None
    report_type: str | None = None
    statement_type: str | None = None
    section_title: str | None = None
    embedding: tuple[float, ...] | None = field(default=None)
