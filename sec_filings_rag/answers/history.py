from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Protocol


@dataclass(frozen=True)
class ConversationHistoryEntry:
    question: str
    answer: str
    citations: tuple[dict[str, object], ...]
    company_name: str | None
    ticker: str | None
    fiscal_year: int
    statement_types: tuple[str, ...]
    created_at: datetime


class ConversationHistoryWriter(Protocol):
    def append(self, entry: ConversationHistoryEntry) -> None: ...


class SQLiteConversationHistory:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._initialize()

    def append(self, entry: ConversationHistoryEntry) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """
                INSERT INTO conversation_history (
                    question,
                    answer,
                    citations,
                    company_name,
                    ticker,
                    fiscal_year,
                    statement_types,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.question,
                    entry.answer,
                    json.dumps(entry.citations),
                    entry.company_name,
                    entry.ticker,
                    entry.fiscal_year,
                    json.dumps(entry.statement_types),
                    entry.created_at.astimezone(UTC).isoformat(),
                ),
            )

    def history(self, limit: int = 50) -> list[ConversationHistoryEntry]:
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT
                    question,
                    answer,
                    citations,
                    company_name,
                    ticker,
                    fiscal_year,
                    statement_types,
                    created_at
                FROM conversation_history
                ORDER BY created_at DESC, history_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_entry_from_row(row) for row in rows]

    def _initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_history (
                    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    citations TEXT NOT NULL CHECK (json_valid(citations)),
                    company_name TEXT,
                    ticker TEXT,
                    fiscal_year INTEGER NOT NULL,
                    statement_types TEXT NOT NULL CHECK (json_valid(statement_types)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_history_created_at
                ON conversation_history(created_at DESC)
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_history_ticker_year
                ON conversation_history(ticker, fiscal_year, created_at DESC)
                """,
            )


def _entry_from_row(row: sqlite3.Row) -> ConversationHistoryEntry:
    return ConversationHistoryEntry(
        question=str(row["question"]),
        answer=str(row["answer"]),
        citations=tuple(json.loads(str(row["citations"]))),
        company_name=row["company_name"]
        if row["company_name"] is None
        else str(row["company_name"]),
        ticker=row["ticker"] if row["ticker"] is None else str(row["ticker"]),
        fiscal_year=int(row["fiscal_year"]),
        statement_types=tuple(json.loads(str(row["statement_types"]))),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )


__all__ = [
    "ConversationHistoryEntry",
    "ConversationHistoryWriter",
    "SQLiteConversationHistory",
]
