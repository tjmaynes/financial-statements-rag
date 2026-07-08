from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Protocol, cast

from sec_filings_rag.jobs.models import (
    DocumentJobRecord,
    DocumentJobStatus,
    JobMetadata,
)
from sec_filings_rag.logging import get_logger

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
                    job_metadata,
                    status,
                    error_message
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    json.dumps(job.job_metadata, sort_keys=True),
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
                    job_metadata,
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
                    latest.job_metadata,
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
            if not _table_exists(connection, "processing_jobs"):
                _create_processing_jobs_table(connection)
            elif _processing_jobs_requires_migration(connection):
                _migrate_processing_jobs_table(connection)
            _create_processing_jobs_indexes(connection)


def _job_from_sqlite_row(row: sqlite3.Row) -> DocumentJobRecord:
    return DocumentJobRecord(
        job_id=cast(str, row["job_id"]),
        job_metadata=_parse_job_metadata(row["job_metadata"]),
        status=_parse_job_status(row["status"]),
        error_message=cast(str | None, row["error_message"]),
    )


def _parse_job_metadata(raw_metadata: object) -> JobMetadata:
    if not isinstance(raw_metadata, str):
        raise ValueError("Invalid job metadata")
    parsed = json.loads(raw_metadata)
    if not isinstance(parsed, dict):
        raise ValueError("Invalid job metadata")
    return {str(key): value for key, value in parsed.items()}


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _processing_jobs_requires_migration(connection: sqlite3.Connection) -> bool:
    columns = {
        cast(str, row[1])
        for row in connection.execute("PRAGMA table_info(processing_jobs)").fetchall()
    }
    return "job_metadata" not in columns


def _create_processing_jobs_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS processing_jobs (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            job_metadata TEXT NOT NULL CHECK (json_valid(job_metadata)),
            status TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )


def _create_processing_jobs_indexes(connection: sqlite3.Connection) -> None:
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


def _migrate_processing_jobs_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE processing_jobs_migrated (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            job_metadata TEXT NOT NULL CHECK (json_valid(job_metadata)),
            status TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    connection.execute(
        """
        INSERT INTO processing_jobs_migrated (
            event_id,
            job_id,
            job_metadata,
            status,
            error_message,
            created_at
        )
        SELECT
            event_id,
            job_id,
            json_object(
                'original_filename', original_filename,
                'stored_path', stored_path
            ),
            status,
            error_message,
            created_at
        FROM processing_jobs
        ORDER BY event_id
        """,
    )
    connection.execute("DROP TABLE processing_jobs")
    connection.execute(
        "ALTER TABLE processing_jobs_migrated RENAME TO processing_jobs",
    )
