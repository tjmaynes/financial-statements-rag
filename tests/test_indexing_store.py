from __future__ import annotations

from datetime import date

import pytest

from financial_statements_rag.errors import RetryableProcessingError
from financial_statements_rag.ingestion.indexing.models import (
    DocumentChunk,
    IndexedDocument,
    StatementLineItem,
    StatementSection,
)
from financial_statements_rag.ingestion.indexing.store import (
    PostgresIndexStore,
    schema_sql,
)


class RecordingConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object | None]] = []

    def execute(self, statement: str, params: object | None = None) -> None:
        self.executed.append((statement, params))

    def commit(self) -> None:
        self.executed.append(("COMMIT", None))

    def close(self) -> None:
        self.executed.append(("CLOSE", None))


class TemporaryDatabaseError(RuntimeError):
    pass


class FailingConnection:
    def execute(self, statement: str, params: object | None = None) -> None:
        raise TemporaryDatabaseError("database unavailable")

    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None


def _document() -> IndexedDocument:
    return IndexedDocument(
        document_id="doc_123",
        job_id="job-123",
        stored_path="data/uploads/report.pdf",
        original_filename="report.pdf",
        company_name="Example Corp",
        ticker="EXM",
        fiscal_year=2026,
        fiscal_quarter="Q2",
        report_date=date(2026, 6, 30),
        report_type="quarterly",
        currency="USD",
        scale="millions",
        extraction_warnings=("NO_COVER_PAGE",),
        page_count=8,
    )


def _section() -> StatementSection:
    return StatementSection(
        section_id="sec_123",
        document_id="doc_123",
        statement_type="balance_sheet",
        title="Condensed Consolidated Balance Sheets",
        page_start=3,
        page_end=4,
        raw_text="Cash and cash equivalents 1250",
        confidence=0.95,
    )


def _line_item() -> StatementLineItem:
    return StatementLineItem(
        line_item_id="line_123",
        section_id="sec_123",
        document_id="doc_123",
        statement_type="balance_sheet",
        line_item_label="Cash and cash equivalents",
        raw_value_text="1,250",
        period_label="March 31, 2026",
        currency="USD",
        scale="millions",
        page_number=3,
        source_text="Cash and cash equivalents 1,250 March 31, 2026",
        confidence=0.9,
    )


def _chunk() -> DocumentChunk:
    return DocumentChunk(
        chunk_id="chunk_123",
        document_id="doc_123",
        section_id="sec_123",
        job_id="job-123",
        chunk_index=0,
        text="Cash and cash equivalents 1250",
        company_name="Example Corp",
        ticker="EXM",
        fiscal_year=2026,
        fiscal_quarter="Q2",
        report_date=date(2026, 6, 30),
        report_type="quarterly",
        statement_type="balance_sheet",
        section_title="Condensed Consolidated Balance Sheets",
        page_start=3,
        page_end=4,
        embedding=(0.1, 0.2, 0.3),
    )


def test_schema_sql_creates_pgvector_tables() -> None:
    sql = schema_sql()

    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "CREATE TABLE IF NOT EXISTS indexed_documents" in sql
    assert "CREATE TABLE IF NOT EXISTS statement_sections" in sql
    assert "CREATE TABLE IF NOT EXISTS statement_line_items" in sql
    assert "CREATE TABLE IF NOT EXISTS document_chunks" in sql
    assert "embedding vector(1536)" in sql


def test_initialize_executes_schema_statements() -> None:
    connection = RecordingConnection()
    store = PostgresIndexStore(
        "postgresql://localhost/fsr",
        connection_factory=lambda _dsn: connection,
    )

    store.initialize()

    assert any(
        "CREATE EXTENSION IF NOT EXISTS vector" in sql
        for sql, _params in connection.executed
    )
    assert any(
        "CREATE TABLE IF NOT EXISTS document_chunks" in sql
        for sql, _params in connection.executed
    )
    assert ("COMMIT", None) in connection.executed
    assert ("CLOSE", None) in connection.executed


def test_upsert_chunks_is_idempotent() -> None:
    connection = RecordingConnection()
    store = PostgresIndexStore(
        "postgresql://localhost/fsr",
        connection_factory=lambda _dsn: connection,
    )

    document = _document()
    section = _section()
    line_item = _line_item()
    chunk = _chunk()

    store.upsert_document(document)
    store.upsert_statement_sections(document.document_id, [section])
    store.upsert_statement_line_items(document.document_id, [line_item])
    store.upsert_chunks(document.document_id, [chunk])
    store.upsert_chunks(document.document_id, [chunk])

    chunk_statements = [
        params
        for sql, params in connection.executed
        if "INSERT INTO document_chunks" in sql
    ]
    assert len(chunk_statements) == 2
    assert all(
        "ON CONFLICT (chunk_id) DO UPDATE" in sql
        for sql, _ in connection.executed
        if "INSERT INTO document_chunks" in sql
    )
    assert chunk_statements[0] == chunk_statements[1]


def test_upsert_chunks_uses_single_typed_embedding_parameter() -> None:
    connection = RecordingConnection()
    store = PostgresIndexStore(
        "postgresql://localhost/fsr",
        connection_factory=lambda _dsn: connection,
    )

    document = _document()
    section = _section()
    line_item = _line_item()
    chunk = _chunk()

    store.upsert_document(document)
    store.upsert_statement_sections(document.document_id, [section])
    store.upsert_statement_line_items(document.document_id, [line_item])
    store.upsert_chunks(document.document_id, [chunk])

    chunk_insert = next(
        (sql, params)
        for sql, params in connection.executed
        if "INSERT INTO document_chunks" in sql
    )
    sql, params = chunk_insert

    assert "CAST(%s AS vector)" in sql
    assert "CASE WHEN %s IS NULL" not in sql
    assert isinstance(params, tuple)
    assert len(params) == 17


def test_transient_store_error_becomes_retryable() -> None:
    store = PostgresIndexStore(
        "postgresql://localhost/fsr",
        connection_factory=lambda _dsn: FailingConnection(),
    )

    with pytest.raises(
        RetryableProcessingError,
        match="Index store is temporarily unavailable",
    ):
        store.upsert_document(_document())
