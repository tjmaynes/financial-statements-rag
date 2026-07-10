from __future__ import annotations

from datetime import date

from sec_filings_rag.retrieval.search import (
    ChunkSearchFilters,
    ChunkSearchNoMatchError,
    ChunkSearchNoRetrievableChunksError,
    ChunkSearchUnavailableError,
    ChunkSearchService,
)


class RecordingCursor:
    def __init__(
        self,
        *,
        fetchone_results: list[object] | None = None,
        fetchall_result: list[object] | None = None,
    ) -> None:
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_result = list(fetchall_result or [])

    def fetchone(self) -> object:
        return self._fetchone_results.pop(0)

    def fetchall(self) -> list[object]:
        return self._fetchall_result


class RecordingConnection:
    def __init__(self, cursors: list[RecordingCursor]) -> None:
        self.executed: list[tuple[str, object | None]] = []
        self._cursors = list(cursors)

    def execute(self, statement: str, params: object | None = None) -> RecordingCursor:
        self.executed.append((statement, params))
        return self._cursors.pop(0)

    def close(self) -> None:
        return None


class TemporaryDatabaseError(RuntimeError):
    pass


class UndefinedTable(RuntimeError):
    pass


class FailingConnection:
    def execute(self, statement: str, params: object | None = None) -> RecordingCursor:
        raise TemporaryDatabaseError("database unavailable")

    def close(self) -> None:
        return None


class MissingSchemaConnection:
    def execute(self, statement: str, params: object | None = None) -> RecordingCursor:
        raise UndefinedTable("relation document_chunks does not exist")

    def close(self) -> None:
        return None


def test_search_uses_two_phase_retrieval_and_maps_results() -> None:
    connection = RecordingConnection(
        [
            RecordingCursor(fetchone_results=[(1, 1)]),
            RecordingCursor(
                fetchall_result=[
                    (
                        "chunk-123",
                        "doc-123",
                        "Revenue was 10.0 billion",
                        "Example Corp",
                        "EXM",
                        date(2026, 6, 30),
                        "income_statement",
                        "Condensed Consolidated Statements of Income",
                        3,
                        4,
                        0.91,
                    )
                ],
            ),
        ]
    )
    service = ChunkSearchService(
        "postgresql://localhost/fsr",
        connection_factory=lambda _dsn: connection,
    )

    results = service.search(
        embedding=(0.1, 0.2, 0.3),
        filters=ChunkSearchFilters(
            company_name="Example Corp",
            ticker="EXM",
            fiscal_year=2026,
            statement_types=("income_statement", "cash_flow_statement"),
        ),
        limit=5,
    )

    assert len(connection.executed) == 2
    phase_one_sql, phase_one_params = connection.executed[0]
    phase_two_sql, phase_two_params = connection.executed[1]

    assert "EXISTS (" in phase_one_sql
    assert "has_filtered_rows" in phase_one_sql
    assert "has_retrievable_rows" in phase_one_sql
    assert "embedding IS NOT NULL" in phase_one_sql
    assert "ORDER BY embedding <=> CAST(%s AS vector)" in phase_two_sql
    assert "CAST(%s AS vector)" in phase_two_sql
    assert "statement_type = ANY(%s)" in phase_two_sql
    assert "IS NULL OR" not in phase_one_sql
    assert "IS NULL OR" not in phase_two_sql
    assert isinstance(phase_two_params, tuple)
    assert phase_two_params[-1] == 5

    assert len(results) == 1
    assert results[0].chunk_id == "chunk-123"
    assert results[0].document_id == "doc-123"
    assert results[0].statement_type == "income_statement"
    assert results[0].section_title == "Condensed Consolidated Statements of Income"
    assert results[0].similarity == 0.91


def test_search_omits_absent_optional_filters_from_sql_and_parameters() -> None:
    connection = RecordingConnection(
        [
            RecordingCursor(fetchone_results=[(1, 1)]),
            RecordingCursor(fetchall_result=[]),
        ]
    )
    service = ChunkSearchService(
        "postgresql://localhost/fsr",
        connection_factory=lambda _dsn: connection,
    )

    service.search(
        embedding=(0.1, 0.2, 0.3),
        filters=ChunkSearchFilters(
            company_name=None,
            ticker=None,
            fiscal_year=2026,
            statement_types=None,
        ),
        limit=5,
    )

    phase_one_sql, phase_one_params = connection.executed[0]
    phase_two_sql, phase_two_params = connection.executed[1]

    assert "company_name = %s" not in phase_one_sql
    assert "ticker = %s" not in phase_one_sql
    assert "statement_type = ANY(%s)" not in phase_one_sql
    assert "company_name = %s" not in phase_two_sql
    assert "ticker = %s" not in phase_two_sql
    assert "statement_type = ANY(%s)" not in phase_two_sql
    assert phase_one_params == (2026, 2026)
    assert phase_two_params == ("[0.1,0.2,0.3]", 2026, "[0.1,0.2,0.3]", 5)


def test_search_raises_no_match_error_when_filtered_rows_missing() -> None:
    connection = RecordingConnection([RecordingCursor(fetchone_results=[(0, 0)])])
    service = ChunkSearchService(
        "postgresql://localhost/fsr",
        connection_factory=lambda _dsn: connection,
    )

    try:
        service.search(
            embedding=(0.1, 0.2, 0.3),
            filters=ChunkSearchFilters(
                company_name="Example Corp",
                ticker=None,
                fiscal_year=2026,
                statement_types=("balance_sheet",),
            ),
            limit=5,
        )
    except ChunkSearchNoMatchError:
        return

    assert False, "expected ChunkSearchNoMatchError"


def test_search_raises_no_retrievable_chunks_error_when_embeddings_missing() -> None:
    connection = RecordingConnection([RecordingCursor(fetchone_results=[(1, 0)])])
    service = ChunkSearchService(
        "postgresql://localhost/fsr",
        connection_factory=lambda _dsn: connection,
    )

    try:
        service.search(
            embedding=(0.1, 0.2, 0.3),
            filters=ChunkSearchFilters(
                company_name=None,
                ticker="EXM",
                fiscal_year=2026,
                statement_types=("cash_flow_statement",),
            ),
            limit=5,
        )
    except ChunkSearchNoRetrievableChunksError:
        return

    assert False, "expected ChunkSearchNoRetrievableChunksError"


def test_transient_store_failure_becomes_unavailable() -> None:
    service = ChunkSearchService(
        "postgresql://localhost/fsr",
        connection_factory=lambda _dsn: FailingConnection(),
    )

    try:
        service.search(
            embedding=(0.1, 0.2, 0.3),
            filters=ChunkSearchFilters(
                company_name="Example Corp",
                ticker=None,
                fiscal_year=2026,
                statement_types=("income_statement",),
            ),
            limit=5,
        )
    except ChunkSearchUnavailableError:
        return

    assert False, "expected ChunkSearchUnavailableError"


def test_missing_schema_becomes_unavailable() -> None:
    service = ChunkSearchService(
        "postgresql://localhost/fsr",
        connection_factory=lambda _dsn: MissingSchemaConnection(),
    )

    try:
        service.search(
            embedding=(0.1, 0.2, 0.3),
            filters=ChunkSearchFilters(
                company_name=None,
                ticker="EXM",
                fiscal_year=2026,
                statement_types=("balance_sheet",),
            ),
            limit=5,
        )
    except ChunkSearchUnavailableError:
        return

    assert False, "expected ChunkSearchUnavailableError"
