from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from sec_filings_rag.answers.history import (
    ConversationHistoryEntry,
    SQLiteConversationHistory,
)


def test_history_initializes_table(tmp_path: Path) -> None:
    database_path = tmp_path / "history.sqlite3"

    SQLiteConversationHistory(database_path)

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'conversation_history'
            """,
        ).fetchone()

    assert row == ("conversation_history",)


def test_append_and_read_completed_answers(tmp_path: Path) -> None:
    history = SQLiteConversationHistory(tmp_path / "history.sqlite3")
    earlier = ConversationHistoryEntry(
        question="What was revenue?",
        answer="Revenue was 10.0 billion. [1]",
        citations=(
            {
                "index": 1,
                "chunk_id": "chunk-1",
                "text": "Revenue 10.0 billion",
            },
        ),
        company_name="Example Corp",
        ticker="EXM",
        fiscal_year=2026,
        statement_types=("income_statement",),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    later = ConversationHistoryEntry(
        question="What was cash?",
        answer="Cash was 2.0 billion.",
        citations=(),
        company_name="Example Corp",
        ticker="EXM",
        fiscal_year=2026,
        statement_types=("balance_sheet",),
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    history.append(earlier)
    history.append(later)

    entries = history.history()

    assert [entry.question for entry in entries] == [
        "What was cash?",
        "What was revenue?",
    ]
    assert entries[0].citations == ()
    assert entries[1].citations == (
        {
            "index": 1,
            "chunk_id": "chunk-1",
            "text": "Revenue 10.0 billion",
        },
    )
    assert entries[1].company_name == "Example Corp"
    assert entries[1].ticker == "EXM"
    assert entries[1].fiscal_year == 2026
    assert entries[1].statement_types == ("income_statement",)


def test_history_stores_completed_answer_payload(tmp_path: Path) -> None:
    history = SQLiteConversationHistory(tmp_path / "history.sqlite3")
    entry = ConversationHistoryEntry(
        question="What was operating income?",
        answer="Operating income was 4.0 billion. [1]",
        citations=(
            {
                "index": 1,
                "chunk_id": "chunk-2",
                "document_id": "doc-1",
                "company_name": "Example Corp",
                "ticker": "EXM",
                "report_date": "2026-06-30",
                "statement_type": "income_statement",
                "section_title": "Condensed Consolidated Statements of Income",
                "page_start": 4,
                "page_end": 5,
                "text": "Operating income was 4.0 billion",
            },
        ),
        company_name="Example Corp",
        ticker="EXM",
        fiscal_year=2026,
        statement_types=("income_statement", "cash_flow_statement"),
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
    )

    history.append(entry)

    entries = history.history()

    assert entries == [entry]
