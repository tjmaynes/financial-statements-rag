from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Protocol, cast

from financial_statements_rag.jobs.models import DocumentJobRecord, DocumentJobStatus
from financial_statements_rag.logging import get_logger

logger = get_logger("jobs.event_log")


def _parse_job_status(raw_status: object) -> DocumentJobStatus:
    if not isinstance(raw_status, str):
        raise ValueError("Invalid job status")
    try:
        return DocumentJobStatus(raw_status)
    except ValueError:
        raise ValueError(f"{raw_status!r} is not a valid DocumentJobStatus") from None


class DocumentJobEventLog(Protocol):
    async def append(self, job: DocumentJobRecord) -> None: ...

    async def latest(self, job_id: str) -> DocumentJobRecord | None: ...

    async def history(self, limit: int = 50) -> list[DocumentJobRecord]: ...


class SQLiteDocumentJobEventLog:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._initialize()

    async def append(self, job: DocumentJobRecord) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """
                INSERT INTO processing_jobs (
                    job_id,
                    original_filename,
                    stored_path,
                    status,
                    error_message
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.original_filename,
                    str(job.stored_path),
                    job.status.value,
                    job.error_message,
                ),
            )
        logger.info("job event appended", extra={"job_id": job.job_id})

    async def latest(self, job_id: str) -> DocumentJobRecord | None:
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT
                    job_id,
                    original_filename,
                    stored_path,
                    status,
                    error_message
                FROM processing_jobs
                WHERE job_id = ?
                ORDER BY event_id DESC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return _job_from_sqlite_row(row)

    async def history(self, limit: int = 50) -> list[DocumentJobRecord]:
        with sqlite3.connect(self._database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT
                    latest.job_id,
                    latest.original_filename,
                    latest.stored_path,
                    latest.status,
                    latest.error_message
                FROM processing_jobs latest
                INNER JOIN (
                    SELECT job_id, MAX(event_id) AS event_id
                    FROM processing_jobs
                    GROUP BY job_id
                ) grouped
                    ON latest.job_id = grouped.job_id
                    AND latest.event_id = grouped.event_id
                ORDER BY latest.event_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_job_from_sqlite_row(row) for row in rows]

    def _initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS processing_jobs (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_processing_jobs_latest
                ON processing_jobs(job_id, event_id DESC)
                """,
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_processing_jobs_created_at
                ON processing_jobs(created_at)
                """,
            )


def _job_from_sqlite_row(row: sqlite3.Row) -> DocumentJobRecord:
    return DocumentJobRecord(
        job_id=cast(str, row["job_id"]),
        original_filename=cast(str, row["original_filename"]),
        stored_path=Path(cast(str, row["stored_path"])),
        status=_parse_job_status(row["status"]),
        error_message=cast(str | None, row["error_message"]),
    )
