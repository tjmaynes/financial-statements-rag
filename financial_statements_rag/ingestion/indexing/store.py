from __future__ import annotations

from collections.abc import Callable, Sequence
import importlib
import json
from typing import Protocol, cast

from financial_statements_rag.ingestion.indexing.models import (
    DocumentChunk,
    IndexedDocument,
    StatementLineItem,
    StatementSection,
)
from financial_statements_rag.errors import RetryableProcessingError

VECTOR_STORE_UNAVAILABLE_CODE = "VECTOR_STORE_UNAVAILABLE"
VECTOR_STORE_UNAVAILABLE_MESSAGE = "Index store is temporarily unavailable"
VECTOR_STORE_WRITE_FAILED_CODE = "VECTOR_STORE_WRITE_FAILED"
VECTOR_STORE_WRITE_FAILED_MESSAGE = "Document chunks could not be saved"


class DatabaseConnection(Protocol):
    def execute(self, statement: str, parameters: object | None = None) -> object: ...

    def commit(self) -> None: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[str], DatabaseConnection]


SCHEMA_STATEMENTS = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE IF NOT EXISTS indexed_documents (
      document_id TEXT PRIMARY KEY,
      job_id TEXT NOT NULL UNIQUE,
      stored_path TEXT NOT NULL,
      original_filename TEXT,
      company_name TEXT,
      ticker TEXT,
      fiscal_year INTEGER,
      fiscal_quarter TEXT,
      report_date DATE,
      report_type TEXT,
      currency TEXT,
      scale TEXT,
      extraction_warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS statement_sections (
      section_id TEXT PRIMARY KEY,
      document_id TEXT NOT NULL
        REFERENCES indexed_documents(document_id)
        ON DELETE CASCADE,
      statement_type TEXT NOT NULL,
      title TEXT NOT NULL,
      page_start INTEGER NOT NULL,
      page_end INTEGER NOT NULL,
      raw_text TEXT NOT NULL,
      confidence REAL NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS statement_line_items (
      line_item_id TEXT PRIMARY KEY,
      section_id TEXT
        REFERENCES statement_sections(section_id)
        ON DELETE CASCADE,
      document_id TEXT NOT NULL
        REFERENCES indexed_documents(document_id)
        ON DELETE CASCADE,
      statement_type TEXT NOT NULL,
      line_item_label TEXT NOT NULL,
      raw_value_text TEXT,
      period_label TEXT,
      currency TEXT,
      scale TEXT,
      page_number INTEGER NOT NULL,
      source_text TEXT NOT NULL,
      confidence REAL NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS document_chunks (
      chunk_id TEXT PRIMARY KEY,
      document_id TEXT NOT NULL
        REFERENCES indexed_documents(document_id)
        ON DELETE CASCADE,
      section_id TEXT
        REFERENCES statement_sections(section_id)
        ON DELETE SET NULL,
      job_id TEXT NOT NULL,
      chunk_index INTEGER NOT NULL,
      text TEXT NOT NULL,
      company_name TEXT,
      ticker TEXT,
      fiscal_year INTEGER,
      fiscal_quarter TEXT,
      report_date DATE,
      report_type TEXT,
      statement_type TEXT,
      section_title TEXT,
      page_start INTEGER NOT NULL,
      page_end INTEGER NOT NULL,
      embedding vector(1536),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE (document_id, chunk_index)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_indexed_documents_job_id
    ON indexed_documents(job_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_indexed_documents_company_name
    ON indexed_documents(company_name)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_indexed_documents_ticker
    ON indexed_documents(ticker)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_indexed_documents_fiscal_period
    ON indexed_documents(fiscal_year, fiscal_quarter)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_statement_sections_document_type
    ON statement_sections(document_id, statement_type)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_statement_line_items_document_type
    ON statement_line_items(document_id, statement_type)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_chunks_document_index
    ON document_chunks(document_id, chunk_index)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_chunks_company_name
    ON document_chunks(company_name)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_chunks_ticker
    ON document_chunks(ticker)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_chunks_fiscal_period
    ON document_chunks(fiscal_year, fiscal_quarter)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_chunks_statement_type
    ON document_chunks(statement_type)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding
    ON document_chunks USING hnsw (embedding vector_cosine_ops)
    """,
)


def schema_sql() -> str:
    return ";\n".join(statement.strip() for statement in SCHEMA_STATEMENTS) + ";"


def _default_connection_factory(dsn: str) -> DatabaseConnection:
    psycopg = importlib.import_module("psycopg")
    return cast(DatabaseConnection, psycopg.connect(dsn))


def _is_unavailable_store_error(error: Exception) -> bool:
    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return True
    return error.__class__.__name__ in {
        "OperationalError",
        "InterfaceError",
        "AdminShutdown",
        "TemporaryDatabaseError",
    }


def _is_retryable_write_error(error: Exception) -> bool:
    return error.__class__.__name__ in {
        "DeadlockDetected",
        "SerializationFailure",
    }


def _vector_literal(embedding: tuple[float, ...] | None) -> str | None:
    if embedding is None:
        return None
    return "[" + ",".join(str(value) for value in embedding) + "]"


class PostgresIndexStore:
    def __init__(
        self,
        postgres_url: str,
        connection_factory: ConnectionFactory = _default_connection_factory,
    ) -> None:
        self._postgres_url = postgres_url
        self._connection_factory = connection_factory

    def initialize(self) -> None:
        connection = self._connection_factory(self._postgres_url)
        try:
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement.strip())
            connection.commit()
        finally:
            connection.close()

    def upsert_document(self, document: IndexedDocument) -> None:
        self._execute_write(
            """
            INSERT INTO indexed_documents (
              document_id,
              job_id,
              stored_path,
              original_filename,
              company_name,
              ticker,
              fiscal_year,
              fiscal_quarter,
              report_date,
              report_type,
              currency,
              scale,
              extraction_warnings
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSONB)
            )
            ON CONFLICT (document_id) DO UPDATE SET
              job_id = EXCLUDED.job_id,
              stored_path = EXCLUDED.stored_path,
              original_filename = EXCLUDED.original_filename,
              company_name = EXCLUDED.company_name,
              ticker = EXCLUDED.ticker,
              fiscal_year = EXCLUDED.fiscal_year,
              fiscal_quarter = EXCLUDED.fiscal_quarter,
              report_date = EXCLUDED.report_date,
              report_type = EXCLUDED.report_type,
              currency = EXCLUDED.currency,
              scale = EXCLUDED.scale,
              extraction_warnings = EXCLUDED.extraction_warnings,
              updated_at = now()
            """,
            (
                document.document_id,
                document.job_id,
                document.stored_path,
                document.original_filename,
                document.company_name,
                document.ticker,
                document.fiscal_year,
                document.fiscal_quarter,
                document.report_date,
                document.report_type,
                document.currency,
                document.scale,
                json.dumps(list(document.extraction_warnings)),
            ),
        )

    def upsert_statement_sections(
        self,
        document_id: str,
        sections: Sequence[StatementSection],
    ) -> None:
        if not sections:
            return
        self._ensure_document_ids(
            document_id,
            [section.document_id for section in sections],
        )

        self._execute_many(
            """
            INSERT INTO statement_sections (
              section_id,
              document_id,
              statement_type,
              title,
              page_start,
              page_end,
              raw_text,
              confidence
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (section_id) DO UPDATE SET
              document_id = EXCLUDED.document_id,
              statement_type = EXCLUDED.statement_type,
              title = EXCLUDED.title,
              page_start = EXCLUDED.page_start,
              page_end = EXCLUDED.page_end,
              raw_text = EXCLUDED.raw_text,
              confidence = EXCLUDED.confidence
            """,
            [
                (
                    section.section_id,
                    section.document_id,
                    section.statement_type,
                    section.title,
                    section.page_start,
                    section.page_end,
                    section.raw_text,
                    section.confidence,
                )
                for section in sections
            ],
        )

    def upsert_statement_line_items(
        self,
        document_id: str,
        line_items: Sequence[StatementLineItem],
    ) -> None:
        if not line_items:
            return
        self._ensure_document_ids(
            document_id,
            [line_item.document_id for line_item in line_items],
        )

        self._execute_many(
            """
            INSERT INTO statement_line_items (
              line_item_id,
              section_id,
              document_id,
              statement_type,
              line_item_label,
              raw_value_text,
              period_label,
              currency,
              scale,
              page_number,
              source_text,
              confidence
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (line_item_id) DO UPDATE SET
              section_id = EXCLUDED.section_id,
              document_id = EXCLUDED.document_id,
              statement_type = EXCLUDED.statement_type,
              line_item_label = EXCLUDED.line_item_label,
              raw_value_text = EXCLUDED.raw_value_text,
              period_label = EXCLUDED.period_label,
              currency = EXCLUDED.currency,
              scale = EXCLUDED.scale,
              page_number = EXCLUDED.page_number,
              source_text = EXCLUDED.source_text,
              confidence = EXCLUDED.confidence
            """,
            [
                (
                    line_item.line_item_id,
                    line_item.section_id,
                    line_item.document_id,
                    line_item.statement_type,
                    line_item.line_item_label,
                    line_item.raw_value_text,
                    line_item.period_label,
                    line_item.currency,
                    line_item.scale,
                    line_item.page_number,
                    line_item.source_text,
                    line_item.confidence,
                )
                for line_item in line_items
            ],
        )

    def upsert_chunks(
        self,
        document_id: str,
        chunks: Sequence[DocumentChunk],
    ) -> None:
        if not chunks:
            return
        self._ensure_document_ids(
            document_id,
            [chunk.document_id for chunk in chunks],
        )

        self._execute_many(
            """
            INSERT INTO document_chunks (
              chunk_id,
              document_id,
              section_id,
              job_id,
              chunk_index,
              text,
              company_name,
              ticker,
              fiscal_year,
              fiscal_quarter,
              report_date,
              report_type,
              statement_type,
              section_title,
              page_start,
              page_end,
              embedding
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              CASE WHEN %s IS NULL THEN NULL ELSE CAST(%s AS vector) END
            )
            ON CONFLICT (chunk_id) DO UPDATE SET
              document_id = EXCLUDED.document_id,
              section_id = EXCLUDED.section_id,
              job_id = EXCLUDED.job_id,
              chunk_index = EXCLUDED.chunk_index,
              text = EXCLUDED.text,
              company_name = EXCLUDED.company_name,
              ticker = EXCLUDED.ticker,
              fiscal_year = EXCLUDED.fiscal_year,
              fiscal_quarter = EXCLUDED.fiscal_quarter,
              report_date = EXCLUDED.report_date,
              report_type = EXCLUDED.report_type,
              statement_type = EXCLUDED.statement_type,
              section_title = EXCLUDED.section_title,
              page_start = EXCLUDED.page_start,
              page_end = EXCLUDED.page_end,
              embedding = EXCLUDED.embedding
            """,
            [
                (
                    chunk.chunk_id,
                    chunk.document_id,
                    chunk.section_id,
                    chunk.job_id,
                    chunk.chunk_index,
                    chunk.text,
                    chunk.company_name,
                    chunk.ticker,
                    chunk.fiscal_year,
                    chunk.fiscal_quarter,
                    chunk.report_date,
                    chunk.report_type,
                    chunk.statement_type,
                    chunk.section_title,
                    chunk.page_start,
                    chunk.page_end,
                    _vector_literal(chunk.embedding),
                    _vector_literal(chunk.embedding),
                )
                for chunk in chunks
            ],
        )

    def _execute_many(
        self,
        statement: str,
        parameter_sets: list[tuple[object, ...]],
    ) -> None:
        connection = self._connection_factory(self._postgres_url)
        try:
            for parameters in parameter_sets:
                connection.execute(statement.strip(), parameters)
            connection.commit()
        except Exception as error:
            self._raise_retryable_error(error)
            raise
        finally:
            connection.close()

    def _execute_write(self, statement: str, parameters: tuple[object, ...]) -> None:
        connection = self._connection_factory(self._postgres_url)
        try:
            connection.execute(statement.strip(), parameters)
            connection.commit()
        except Exception as error:
            self._raise_retryable_error(error)
            raise
        finally:
            connection.close()

    def _raise_retryable_error(self, error: Exception) -> None:
        if _is_unavailable_store_error(error):
            raise RetryableProcessingError(
                VECTOR_STORE_UNAVAILABLE_CODE,
                VECTOR_STORE_UNAVAILABLE_MESSAGE,
            ) from error
        if _is_retryable_write_error(error):
            raise RetryableProcessingError(
                VECTOR_STORE_WRITE_FAILED_CODE,
                VECTOR_STORE_WRITE_FAILED_MESSAGE,
            ) from error

    def _ensure_document_ids(
        self,
        expected_document_id: str,
        actual_document_ids: Sequence[str],
    ) -> None:
        if any(
            document_id != expected_document_id for document_id in actual_document_ids
        ):
            raise ValueError("all records must belong to the requested document_id")
