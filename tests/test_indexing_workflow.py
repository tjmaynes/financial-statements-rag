from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

from financial_statements_rag.ingestion.extraction.metadata import ReportMetadata
from financial_statements_rag.ingestion.extraction.pdf import ExtractedPage
from financial_statements_rag.ingestion.extraction.statements import (
    StatementDetectionResult,
    StatementLineItem,
    StatementSection,
    StatementType,
)
from financial_statements_rag.ingestion.indexing.models import (
    DocumentChunk,
    IndexedDocument,
    StatementLineItem as IndexedStatementLineItem,
    StatementSection as IndexedStatementSection,
)
from financial_statements_rag.ingestion.indexing.chunking import create_document_chunks
from financial_statements_rag.settings import Settings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from financial_statements_rag.ingestion.workflow import ChunkBuilder


class RecordingPdfPageExtractor:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def extract(self, document_path: Path) -> list[ExtractedPage]:
        self._calls.append("load_pdf_pages")
        return [
            ExtractedPage(
                page_number=1,
                text="Cover page",
                source_path=document_path,
            ),
            ExtractedPage(
                page_number=2,
                text=(
                    "Condensed Consolidated Balance Sheets\n"
                    "Cash and cash equivalents 1,250 March 31, 2026\n"
                ),
                source_path=document_path,
            ),
        ]


class RecordingMetadataInferer:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def infer(self, pages: Sequence[ExtractedPage]) -> ReportMetadata:
        self._calls.append("infer_report_metadata")
        assert len(pages) == 2
        return ReportMetadata(
            company_name="Example Corp",
            ticker="EXM",
            fiscal_year=2026,
            fiscal_quarter="Q1",
            report_type="quarterly",
            currency="USD",
            scale="millions",
        )


class RecordingStatementExtractor:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def detect_sections(
        self,
        pages: Sequence[ExtractedPage],
    ) -> StatementDetectionResult:
        self._calls.append("extract_statement_sections")
        return StatementDetectionResult(
            sections=(
                StatementSection(
                    statement_type=StatementType.BALANCE_SHEET,
                    title="Condensed Consolidated Balance Sheets",
                    page_start=2,
                    page_end=2,
                    raw_text="Cash and cash equivalents 1,250 March 31, 2026",
                    confidence=0.95,
                ),
            ),
        )

    def extract_line_items(
        self,
        sections: Sequence[StatementSection],
        pages: Sequence[ExtractedPage],
        metadata: ReportMetadata | None = None,
    ) -> tuple[StatementLineItem, ...]:
        self._calls.append("extract_statement_line_items")
        return (
            StatementLineItem(
                statement_type=StatementType.BALANCE_SHEET,
                line_item_label="Cash and cash equivalents",
                raw_value_text="1,250",
                period_label="March 31, 2026",
                currency="USD",
                scale="millions",
                page_number=2,
                source_text="Cash and cash equivalents 1,250 March 31, 2026",
                confidence=0.9,
            ),
        )


class RecordingEmbeddingProvider:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self._calls.append("embed_chunks")
        return [[0.1, 0.2, 0.3] for _ in texts]


class RecordingIndexStore:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls
        self.document: IndexedDocument | None = None
        self.sections_count = 0
        self.line_items_count = 0
        self.chunks_count = 0
        self.chunks: list[DocumentChunk] = []

    def upsert_document(self, document: IndexedDocument) -> None:
        self.document = document

    def upsert_statement_sections(
        self,
        document_id: str,
        sections: Sequence[IndexedStatementSection],
    ) -> None:
        assert self.document is not None
        assert document_id == self.document.document_id
        self.sections_count = len(sections)

    def upsert_statement_line_items(
        self,
        document_id: str,
        line_items: Sequence[IndexedStatementLineItem],
    ) -> None:
        assert self.document is not None
        assert document_id == self.document.document_id
        self.line_items_count = len(line_items)

    def upsert_chunks(
        self,
        document_id: str,
        chunks: Sequence[DocumentChunk],
    ) -> None:
        assert self.document is not None
        assert document_id == self.document.document_id
        self._calls.append("persist_index")
        self.chunks_count = len(chunks)
        self.chunks = list(chunks)


def recording_chunk_builder(
    calls: list[str],
) -> "ChunkBuilder":
    def build_chunks(
        document: IndexedDocument,
        *,
        document_text: str,
        sections: Sequence[IndexedStatementSection],
        chunk_size: int,
        chunk_overlap: int,
        index_remaining_text: bool,
    ) -> list[DocumentChunk]:
        calls.append("chunk_extracted_content")
        return create_document_chunks(
            document,
            document_text=document_text,
            sections=sections,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            index_remaining_text=index_remaining_text,
        )

    return build_chunks


def test_workflow_executes_nodes_in_order() -> None:
    from financial_statements_rag.ingestion.workflow import (
        build_document_ingestion_workflow,
    )

    calls: list[str] = []
    workflow = build_document_ingestion_workflow(
        settings=Settings(chunk_size=100, chunk_overlap=10),
        pdf_page_extractor=RecordingPdfPageExtractor(calls),
        report_metadata_inferer=RecordingMetadataInferer(calls),
        statement_extractor=RecordingStatementExtractor(calls),
        embedding_provider=RecordingEmbeddingProvider(calls),
        index_store=RecordingIndexStore(calls),
        chunk_builder=recording_chunk_builder(calls),
    )

    asyncio.run(
        workflow.ainvoke(
            {
                "job_id": "job-123",
                "document_path": Path("data/uploads/report.pdf"),
                "original_filename": "report.pdf",
            }
        )
    )

    assert calls == [
        "load_pdf_pages",
        "infer_report_metadata",
        "extract_statement_sections",
        "extract_statement_line_items",
        "chunk_extracted_content",
        "embed_chunks",
        "persist_index",
    ]


class WarningOnlyStatementExtractor:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def detect_sections(
        self,
        pages: Sequence[ExtractedPage],
    ) -> StatementDetectionResult:
        self._calls.append("extract_statement_sections")
        return StatementDetectionResult(
            sections=(),
            warnings=("NO_STATEMENT_SECTIONS_FOUND",),
        )

    def extract_line_items(
        self,
        sections: Sequence[StatementSection],
        pages: Sequence[ExtractedPage],
        metadata: ReportMetadata | None = None,
    ) -> tuple[StatementLineItem, ...]:
        self._calls.append("extract_statement_line_items")
        return ()


def test_warning_path_still_persists_chunks() -> None:
    from financial_statements_rag.ingestion.workflow import (
        build_document_ingestion_workflow,
    )

    calls: list[str] = []
    store = RecordingIndexStore(calls)
    workflow = build_document_ingestion_workflow(
        settings=Settings(chunk_size=80, chunk_overlap=10, index_remaining_text=True),
        pdf_page_extractor=RecordingPdfPageExtractor(calls),
        report_metadata_inferer=RecordingMetadataInferer(calls),
        statement_extractor=WarningOnlyStatementExtractor(calls),
        embedding_provider=RecordingEmbeddingProvider(calls),
        index_store=store,
        chunk_builder=recording_chunk_builder(calls),
    )

    state = asyncio.run(
        workflow.ainvoke(
            {
                "job_id": "job-warning",
                "document_path": Path("data/uploads/report.pdf"),
                "original_filename": "report.pdf",
            }
        )
    )

    assert state["warnings"] == ["NO_STATEMENT_SECTIONS_FOUND"]
    assert store.document is not None
    assert store.document.original_filename == "report.pdf"
    assert store.document.extraction_warnings == ("NO_STATEMENT_SECTIONS_FOUND",)
    assert store.line_items_count == 0
    assert store.chunks_count > 0
    assert all(getattr(chunk, "statement_type") is None for chunk in store.chunks)
