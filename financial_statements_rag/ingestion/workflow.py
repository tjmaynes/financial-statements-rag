from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from financial_statements_rag.ingestion.extraction.metadata import (
    ReportMetadata,
    ReportMetadataInferer,
)
from financial_statements_rag.ingestion.extraction.pdf import (
    ExtractedPage,
    PdfPageExtractor,
)
from financial_statements_rag.ingestion.extraction.statements import (
    StatementDetectionResult,
    StatementExtractor,
    StatementLineItem as ExtractedStatementLineItem,
    StatementSection as ExtractedStatementSection,
)
from financial_statements_rag.ingestion.indexing.chunking import (
    build_document_id,
    build_line_item_id,
    build_section_id,
    create_document_chunks,
)
from financial_statements_rag.ingestion.indexing.embeddings import (
    EmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from financial_statements_rag.ingestion.indexing.models import (
    DocumentChunk,
    IndexedDocument,
    StatementLineItem,
    StatementSection,
)
from financial_statements_rag.ingestion.indexing.store import PostgresIndexStore
from financial_statements_rag.settings import Settings


class PdfPageExtractionService(Protocol):
    def extract(self, document_path: Path) -> list[ExtractedPage]: ...


class ReportMetadataInferenceService(Protocol):
    def infer(self, pages: Sequence[ExtractedPage]) -> ReportMetadata: ...


class StatementExtractionService(Protocol):
    def detect_sections(
        self,
        pages: Sequence[ExtractedPage],
    ) -> StatementDetectionResult: ...

    def extract_line_items(
        self,
        sections: Sequence[ExtractedStatementSection],
        pages: Sequence[ExtractedPage],
        metadata: ReportMetadata | None = None,
    ) -> tuple[ExtractedStatementLineItem, ...]: ...


class IndexStore(Protocol):
    def upsert_document(self, document: IndexedDocument) -> None: ...

    def upsert_statement_sections(
        self,
        document_id: str,
        sections: Sequence[StatementSection],
    ) -> None: ...

    def upsert_statement_line_items(
        self,
        document_id: str,
        line_items: Sequence[StatementLineItem],
    ) -> None: ...

    def upsert_chunks(
        self,
        document_id: str,
        chunks: Sequence[DocumentChunk],
    ) -> None: ...


class DocumentIngestionState(TypedDict, total=False):
    job_id: str
    document_path: Path
    pages: list[ExtractedPage]
    metadata: ReportMetadata
    section_detection: StatementDetectionResult
    sections: list[StatementSection]
    line_items: list[StatementLineItem]
    document: IndexedDocument
    chunks: list[DocumentChunk]
    warnings: list[str]


class ChunkBuilder(Protocol):
    def __call__(
        self,
        document: IndexedDocument,
        *,
        document_text: str,
        sections: Sequence[StatementSection],
        chunk_size: int,
        chunk_overlap: int,
        index_remaining_text: bool,
    ) -> list[DocumentChunk]: ...


class DocumentIngestionWorkflow(Protocol):
    async def ainvoke(
        self,
        input: dict[str, Any] | DocumentIngestionState,
    ) -> DocumentIngestionState: ...


def build_document_ingestion_workflow(
    *,
    settings: Settings,
    pdf_page_extractor: PdfPageExtractionService,
    report_metadata_inferer: ReportMetadataInferenceService,
    statement_extractor: StatementExtractionService,
    embedding_provider: EmbeddingProvider,
    index_store: IndexStore,
    chunk_builder: ChunkBuilder = create_document_chunks,
) -> DocumentIngestionWorkflow:
    graph = StateGraph(DocumentIngestionState)

    async def embed_chunks_node(
        state: DocumentIngestionState,
    ) -> DocumentIngestionState:
        return await _embed_chunks(state, embedding_provider)

    graph.add_node(
        "load_pdf_pages",
        lambda state: _load_pdf_pages(state, pdf_page_extractor),
    )
    graph.add_node(
        "infer_report_metadata",
        lambda state: _infer_report_metadata(state, report_metadata_inferer),
    )
    graph.add_node(
        "extract_statement_sections",
        lambda state: _extract_statement_sections(state, statement_extractor),
    )
    graph.add_node(
        "extract_statement_line_items",
        lambda state: _extract_statement_line_items(state, statement_extractor),
    )
    graph.add_node(
        "chunk_extracted_content",
        lambda state: _chunk_extracted_content(state, settings, chunk_builder),
    )
    graph.add_node("embed_chunks", embed_chunks_node)
    graph.add_node(
        "persist_index",
        lambda state: _persist_index(state, index_store),
    )

    graph.add_edge(START, "load_pdf_pages")
    graph.add_edge("load_pdf_pages", "infer_report_metadata")
    graph.add_edge("infer_report_metadata", "extract_statement_sections")
    graph.add_edge("extract_statement_sections", "extract_statement_line_items")
    graph.add_edge("extract_statement_line_items", "chunk_extracted_content")
    graph.add_edge("chunk_extracted_content", "embed_chunks")
    graph.add_edge("embed_chunks", "persist_index")
    graph.add_edge("persist_index", END)

    return cast(DocumentIngestionWorkflow, graph.compile())


def build_default_document_ingestion_workflow(
    settings: Settings,
) -> DocumentIngestionWorkflow:
    return build_document_ingestion_workflow(
        settings=settings,
        pdf_page_extractor=PdfPageExtractor(),
        report_metadata_inferer=ReportMetadataInferer(),
        statement_extractor=StatementExtractor(),
        embedding_provider=OpenAIEmbeddingProvider(settings),
        index_store=PostgresIndexStore(settings.postgres_url),
    )


def _load_pdf_pages(
    state: DocumentIngestionState,
    pdf_page_extractor: PdfPageExtractionService,
) -> DocumentIngestionState:
    document_path = state["document_path"]
    return {"pages": pdf_page_extractor.extract(document_path)}


def _infer_report_metadata(
    state: DocumentIngestionState,
    report_metadata_inferer: ReportMetadataInferenceService,
) -> DocumentIngestionState:
    pages = state["pages"]
    return {"metadata": report_metadata_inferer.infer(pages)}


def _extract_statement_sections(
    state: DocumentIngestionState,
    statement_extractor: StatementExtractionService,
) -> DocumentIngestionState:
    detection = statement_extractor.detect_sections(state["pages"])
    return {
        "section_detection": detection,
        "warnings": list(detection.warnings),
        "sections": _to_index_sections(
            job_id=state["job_id"],
            document_path=state["document_path"],
            sections=detection.sections,
        ),
    }


def _extract_statement_line_items(
    state: DocumentIngestionState,
    statement_extractor: StatementExtractionService,
) -> DocumentIngestionState:
    extraction_sections = state["section_detection"].sections
    metadata = state["metadata"]
    pages = state["pages"]
    extracted_items = statement_extractor.extract_line_items(
        extraction_sections,
        pages,
        metadata,
    )
    indexed_sections = state["sections"]
    section_ids_by_key = {
        (
            section.statement_type,
            section.page_start,
            section.page_end,
            section.title,
        ): section.section_id
        for section in indexed_sections
    }

    line_items: list[StatementLineItem] = []
    for section, extracted_section in zip(
        indexed_sections, extraction_sections, strict=False
    ):
        section_key = (
            section.statement_type,
            section.page_start,
            section.page_end,
            section.title,
        )
        section_id = section_ids_by_key[section_key]
        for item in extracted_items:
            if item.statement_type.value != extracted_section.statement_type.value:
                continue
            if not (
                extracted_section.page_start
                <= item.page_number
                <= extracted_section.page_end
            ):
                continue
            line_items.append(
                StatementLineItem(
                    line_item_id=build_line_item_id(
                        section_id,
                        statement_type=item.statement_type.value,
                        page_number=item.page_number,
                        line_item_label=item.line_item_label,
                        period_label=item.period_label,
                        raw_value_text=item.raw_value_text,
                    ),
                    section_id=section_id,
                    document_id=section.document_id,
                    statement_type=item.statement_type.value,
                    line_item_label=item.line_item_label,
                    raw_value_text=item.raw_value_text,
                    period_label=item.period_label,
                    currency=item.currency,
                    scale=item.scale,
                    page_number=item.page_number,
                    source_text=item.source_text,
                    confidence=item.confidence,
                )
            )

    return {"line_items": line_items}


def _chunk_extracted_content(
    state: DocumentIngestionState,
    settings: Settings,
    chunk_builder: ChunkBuilder,
) -> DocumentIngestionState:
    document = _build_indexed_document(
        job_id=state["job_id"],
        document_path=state["document_path"],
        pages=state["pages"],
        metadata=state["metadata"],
        warnings=tuple(state.get("warnings", [])),
    )
    document_text = "\n".join(page.text for page in state["pages"])
    chunks = chunk_builder(
        document,
        document_text=document_text,
        sections=state["sections"],
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        index_remaining_text=settings.index_remaining_text,
    )
    return {"document": document, "chunks": chunks}


async def _embed_chunks(
    state: DocumentIngestionState,
    embedding_provider: EmbeddingProvider,
) -> DocumentIngestionState:
    chunks = state["chunks"]
    embeddings = await embedding_provider.embed_texts([chunk.text for chunk in chunks])
    embedded_chunks = [
        replace(chunk, embedding=tuple(embedding))
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]
    return {"chunks": embedded_chunks}


def _persist_index(
    state: DocumentIngestionState,
    index_store: IndexStore,
) -> DocumentIngestionState:
    document = state["document"]
    index_store.upsert_document(document)
    index_store.upsert_statement_sections(document.document_id, state["sections"])
    index_store.upsert_statement_line_items(document.document_id, state["line_items"])
    index_store.upsert_chunks(document.document_id, state["chunks"])
    return {}


def _build_indexed_document(
    *,
    job_id: str,
    document_path: Path,
    pages: Sequence[ExtractedPage],
    metadata: ReportMetadata,
    warnings: tuple[str, ...],
) -> IndexedDocument:
    return IndexedDocument(
        document_id=build_document_id(job_id, document_path),
        job_id=job_id,
        stored_path=document_path.as_posix(),
        company_name=metadata.company_name,
        ticker=metadata.ticker,
        fiscal_year=metadata.fiscal_year,
        fiscal_quarter=metadata.fiscal_quarter,
        report_date=metadata.report_date,
        report_type=metadata.report_type,
        currency=metadata.currency,
        scale=metadata.scale,
        extraction_warnings=warnings,
        page_count=len(pages),
    )


def _to_index_sections(
    *,
    job_id: str,
    document_path: Path,
    sections: Sequence[ExtractedStatementSection],
) -> list[StatementSection]:
    document_id = build_document_id(job_id, document_path)
    return [
        StatementSection(
            section_id=build_section_id(
                document_id,
                statement_type=section.statement_type.value,
                page_start=section.page_start,
                page_end=section.page_end,
                title=section.title,
            ),
            document_id=document_id,
            statement_type=section.statement_type.value,
            title=section.title,
            page_start=section.page_start,
            page_end=section.page_end,
            raw_text=section.raw_text,
            confidence=section.confidence,
        )
        for section in sections
    ]
