from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
import sqlite3
from typing import Protocol, cast
from uuid import uuid4

from arq.connections import ArqRedis, RedisSettings, create_pool
from starlette.datastructures import UploadFile

from financial_statements_rag.logging import get_logger

logger = get_logger("processing")


class DocumentProcessingJobStatus(StrEnum):
    CREATED = "DOCUMENT_PROCESSING_JOB_CREATED"
    STARTED = "DOCUMENT_PROCESSING_JOB_STARTED"
    SUCCEEDED = "DOCUMENT_PROCESSING_JOB_SUCCEEDED"
    FAILED = "DOCUMENT_PROCESSING_JOB_FAILED"


@dataclass(frozen=True)
class DocumentProcessingJobRecord:
    job_id: str
    original_filename: str
    stored_path: Path
    status: DocumentProcessingJobStatus
    error_message: str | None = None

    def with_status(
        self,
        status: DocumentProcessingJobStatus,
        error_message: str | None = None,
    ) -> DocumentProcessingJobRecord:
        return replace(self, status=status, error_message=error_message)


def new_job_id() -> str:
    return uuid4().hex


def _parse_job_status(raw_status: object) -> DocumentProcessingJobStatus:
    if not isinstance(raw_status, str):
        raise ValueError("Invalid job status")
    try:
        return DocumentProcessingJobStatus(raw_status)
    except ValueError:
        raise ValueError(
            f"{raw_status!r} is not a valid DocumentProcessingStatus"
        ) from None


class DocumentProcessingEventLog(Protocol):
    async def append(self, job: DocumentProcessingJobRecord) -> None: ...

    async def latest(self, job_id: str) -> DocumentProcessingJobRecord | None: ...

    async def history(self, limit: int = 50) -> list[DocumentProcessingJobRecord]: ...


class SQLiteDocumentProcessorEventLog:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._initialize()

    async def append(self, job: DocumentProcessingJobRecord) -> None:
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

    async def latest(self, job_id: str) -> DocumentProcessingJobRecord | None:
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

    async def history(self, limit: int = 50) -> list[DocumentProcessingJobRecord]:
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


def _job_from_sqlite_row(row: sqlite3.Row) -> DocumentProcessingJobRecord:
    return DocumentProcessingJobRecord(
        job_id=cast(str, row["job_id"]),
        original_filename=cast(str, row["original_filename"]),
        stored_path=Path(cast(str, row["stored_path"])),
        status=_parse_job_status(row["status"]),
        error_message=cast(str | None, row["error_message"]),
    )


class DocumentProcessingDispatcher(Protocol):
    async def enqueue(self, job_id: str, document_path: Path) -> None: ...


class DocumentProcessorUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class RedisDocumentProcessorDispatcher:
    redis_url: str
    _pool: ArqRedis | None = None

    async def enqueue(self, job_id: str, document_path: Path) -> None:
        try:
            pool = await self._get_pool()
            enqueued_job = await pool.enqueue_job(
                "process_document_job",
                job_id,
                str(document_path),
                _job_id=job_id,
            )
        except Exception as error:
            logger.exception("enqueue failed", extra={"job_id": job_id})
            raise DocumentProcessorUnavailableError(
                "Document processing is temporarily unavailable",
            ) from error

        if enqueued_job is None:
            raise DocumentProcessorUnavailableError(
                "Document processing is temporarily unavailable",
            )

        logger.info("job enqueued", extra={"job_id": job_id})

    async def _get_pool(self) -> ArqRedis:
        if self._pool is None:
            object.__setattr__(
                self,
                "_pool",
                await create_pool(RedisSettings.from_dsn(self.redis_url)),
            )
        pool = self._pool
        if pool is None:  # pragma: no cover - defensive
            raise RuntimeError("dispatcher pool was not initialized")
        return pool


@dataclass(frozen=True)
class UploadedDocument:
    original_filename: str
    path: Path

    @classmethod
    def create(cls, original_filename: str, upload_dir: Path) -> UploadedDocument:
        return cls(
            original_filename=original_filename,
            path=upload_dir / f"{uuid4().hex}.pdf",
        )


class DocumentUploadService:
    def __init__(self, upload_dir: Path) -> None:
        self._upload_dir = upload_dir

    async def upload(self, upload: UploadFile) -> UploadedDocument:
        filename = upload.filename or ""
        self._is_pdf_file(filename=filename, content_type=upload.content_type)
        document = UploadedDocument.create(
            original_filename=filename,
            upload_dir=self._upload_dir,
        )
        try:
            self._upload_dir.mkdir(parents=True, exist_ok=True)
            document.path.write_bytes(await upload.read())
        except Exception:
            logger.exception("upload storage failed")
            raise
        return document

    def _is_pdf_file(self, filename: str, content_type: str | None) -> None:
        if not filename.lower().endswith(".pdf"):
            logger.info("upload validation failed: file must be a PDF")
            raise ValueError("Uploaded file must be a PDF")
        if content_type not in {None, "", "application/pdf"}:
            logger.info("upload validation failed: content type must be PDF")
            raise ValueError("Uploaded file must be a PDF")


class DocumentProcessingService:
    def __init__(
        self,
        event_log: DocumentProcessingEventLog,
        dispatcher: DocumentProcessingDispatcher,
    ) -> None:
        self._event_log = event_log
        self._dispatcher = dispatcher

    async def create_job(
        self,
        original_filename: str,
        stored_path: Path,
    ) -> DocumentProcessingJobRecord:
        job = DocumentProcessingJobRecord(
            job_id=new_job_id(),
            original_filename=original_filename,
            stored_path=stored_path,
            status=DocumentProcessingJobStatus.CREATED,
        )
        await self._event_log.append(job)
        logger.info("job created", extra={"job_id": job.job_id})
        try:
            await self._dispatcher.enqueue(job.job_id, stored_path)
        except Exception as error:
            logger.exception("job enqueue failed", extra={"job_id": job.job_id})
            if isinstance(error, DocumentProcessorUnavailableError):
                raise
            raise DocumentProcessorUnavailableError(
                "Document processing is temporarily unavailable",
            ) from error
        return job

    async def get_job(self, job_id: str) -> DocumentProcessingJobRecord | None:
        return await self._event_log.latest(job_id)

    async def job_history(self, limit: int = 50) -> list[DocumentProcessingJobRecord]:
        return await self._event_log.history(limit)

    async def update_job_status(
        self,
        job_id: str,
        status: DocumentProcessingJobStatus,
        error_message: str | None = None,
    ) -> DocumentProcessingJobRecord:
        job = await self._event_log.latest(job_id)
        if job is None:
            raise KeyError(job_id)
        updated = job.with_status(status, error_message)
        await self._event_log.append(updated)
        logger.info("job status updated", extra={"job_id": job_id})
        return updated
