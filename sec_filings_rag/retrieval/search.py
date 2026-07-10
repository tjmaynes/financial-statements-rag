from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
import importlib
from typing import Protocol, cast

from sec_filings_rag.errors import (
    ChunkSearchNoMatchError,
    ChunkSearchNoRetrievableChunksError,
)

__all__ = [
    "ChunkSearchFilters",
    "ChunkSearchNoMatchError",
    "ChunkSearchNoRetrievableChunksError",
    "ChunkSearchService",
    "ChunkSearchUnavailableError",
    "RetrievedChunk",
]


class Cursor(Protocol):
    def fetchone(self) -> object: ...

    def fetchall(self) -> list[object]: ...


class DatabaseConnection(Protocol):
    def execute(self, statement: str, parameters: object | None = None) -> Cursor: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[str], DatabaseConnection]


class ChunkSearchUnavailableError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Question answering is temporarily unavailable")


@dataclass(frozen=True)
class ChunkSearchFilters:
    company_name: str | None
    ticker: str | None
    fiscal_year: int
    statement_types: tuple[str, ...] | None


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    text: str
    company_name: str | None
    ticker: str | None
    report_date: date | None
    statement_type: str | None
    section_title: str | None
    page_start: int
    page_end: int
    similarity: float


def _default_connection_factory(dsn: str) -> DatabaseConnection:
    psycopg = importlib.import_module("psycopg")
    return cast(DatabaseConnection, psycopg.connect(dsn))


def _vector_literal(embedding: Sequence[float]) -> str:
    return "[" + ",".join(str(value) for value in embedding) + "]"


class ChunkSearchService:
    def __init__(
        self,
        postgres_url: str,
        connection_factory: ConnectionFactory = _default_connection_factory,
    ) -> None:
        self._postgres_url = postgres_url
        self._connection_factory = connection_factory

    def search(
        self,
        *,
        embedding: Sequence[float],
        filters: ChunkSearchFilters,
        limit: int,
    ) -> list[RetrievedChunk]:
        connection = self._connection_factory(self._postgres_url)
        try:
            filtered_where_clause, filtered_where_parameters = _filtered_where_clause(
                filters
            )
            phase_one_cursor = connection.execute(
                f"""
                SELECT
                  EXISTS (
                    SELECT 1
                    FROM document_chunks
                    WHERE {filtered_where_clause}
                  ) AS has_filtered_rows,
                  EXISTS (
                    SELECT 1
                    FROM document_chunks
                    WHERE {filtered_where_clause}
                      AND embedding IS NOT NULL
                  ) AS has_retrievable_rows
                """.strip(),
                filtered_where_parameters + filtered_where_parameters,
            )
            phase_one_row = phase_one_cursor.fetchone()
            has_filtered_rows, has_retrievable_rows = cast(
                tuple[object, object], phase_one_row
            )
            if not bool(has_filtered_rows):
                raise ChunkSearchNoMatchError()
            if not bool(has_retrievable_rows):
                raise ChunkSearchNoRetrievableChunksError()

            cursor = connection.execute(
                f"""
                SELECT
                  chunk_id,
                  document_id,
                  text,
                  company_name,
                  ticker,
                  report_date,
                  statement_type,
                  section_title,
                  page_start,
                  page_end,
                  1 - (embedding <=> CAST(%s AS vector)) AS similarity
                FROM document_chunks
                WHERE embedding IS NOT NULL
                  AND {filtered_where_clause}
                ORDER BY embedding <=> CAST(%s AS vector)
                LIMIT %s
                """.strip(),
                _phase_two_parameters(
                    embedding=embedding,
                    filtered_where_parameters=filtered_where_parameters,
                    limit=limit,
                ),
            )
            return [_chunk_from_row(row) for row in cursor.fetchall()]
        except Exception as error:
            if _is_unavailable_error(error):
                raise ChunkSearchUnavailableError() from error
            raise
        finally:
            connection.close()


def _filtered_where_clause(
    filters: ChunkSearchFilters,
) -> tuple[str, tuple[object, ...]]:
    conditions = ["fiscal_year = %s"]
    parameters: list[object] = [filters.fiscal_year]
    if filters.company_name is not None:
        conditions.append("company_name = %s")
        parameters.append(filters.company_name)
    if filters.ticker is not None:
        conditions.append("ticker = %s")
        parameters.append(filters.ticker)
    if filters.statement_types:
        conditions.append("statement_type = ANY(%s)")
        parameters.append(list(filters.statement_types))
    return " AND ".join(conditions), tuple(parameters)


def _phase_two_parameters(
    *,
    embedding: Sequence[float],
    filtered_where_parameters: tuple[object, ...],
    limit: int,
) -> tuple[object, ...]:
    vector = _vector_literal(embedding)
    return (
        vector,
        *filtered_where_parameters,
        vector,
        limit,
    )


def _chunk_from_row(row: object) -> RetrievedChunk:
    values = cast(tuple[object, ...], row)
    return RetrievedChunk(
        chunk_id=str(values[0]),
        document_id=str(values[1]),
        text=str(values[2]),
        company_name=values[3] if values[3] is None else str(values[3]),
        ticker=values[4] if values[4] is None else str(values[4]),
        report_date=cast(date | None, values[5]),
        statement_type=values[6] if values[6] is None else str(values[6]),
        section_title=values[7] if values[7] is None else str(values[7]),
        page_start=cast(int, values[8]),
        page_end=cast(int, values[9]),
        similarity=cast(float, values[10]),
    )


def _is_unavailable_error(error: Exception) -> bool:
    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return True
    return error.__class__.__name__ in {
        "OperationalError",
        "InterfaceError",
        "AdminShutdown",
        "TemporaryDatabaseError",
        "UndefinedTable",
    }
