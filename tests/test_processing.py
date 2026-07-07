import asyncio
from io import BytesIO
import sqlite3
from pathlib import Path

import pytest
from starlette.datastructures import Headers, UploadFile

from financial_statements_rag.processing import (
    DocumentProcessingService,
    DocumentProcessorUnavailableError,
    DocumentProcessingJobRecord,
    DocumentProcessingJobStatus,
    DocumentUploadService,
    SQLiteDocumentProcessorEventLog,
    UploadedDocument,
)


class RecordingDocumentProcessorDispatcher:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, Path]] = []

    async def enqueue(self, job_id: str, document_path: Path) -> None:
        self.enqueued.append((job_id, document_path))


class FailingDocumentProcessorDispatcher:
    async def enqueue(self, job_id: str, document_path: Path) -> None:
        raise DocumentProcessorUnavailableError(
            "Document processing is temporarily unavailable",
        )


def test_job_status_exposes_only_canonical_document_processor_names() -> None:
    with pytest.raises(KeyError):
        DocumentProcessingJobStatus["PROCESSING_SUCCEEDED"]

    with pytest.raises(KeyError):
        DocumentProcessingJobStatus["UPLOADED"]


def test_sqlite_event_log_appends_status_history(tmp_path: Path) -> None:
    async def run_test() -> None:
        database_path = tmp_path / "events.sqlite3"
        event_log = SQLiteDocumentProcessorEventLog(database_path)
        job = DocumentProcessingJobRecord(
            job_id="job-123",
            original_filename="statement.pdf",
            stored_path=Path("data/uploads/abc.pdf"),
            status=DocumentProcessingJobStatus.CREATED,
        )

        await event_log.append(job)
        await event_log.append(
            job.with_status(DocumentProcessingJobStatus.STARTED),
        )
        await event_log.append(
            job.with_status(DocumentProcessingJobStatus.SUCCEEDED),
        )

        with sqlite3.connect(database_path) as connection:
            rows = connection.execute(
                "SELECT status FROM processing_jobs WHERE job_id = ? ORDER BY event_id",
                (job.job_id,),
            ).fetchall()

        assert [row[0] for row in rows] == [
            "DOCUMENT_PROCESSING_JOB_CREATED",
            "DOCUMENT_PROCESSING_JOB_STARTED",
            "DOCUMENT_PROCESSING_JOB_SUCCEEDED",
        ]

    asyncio.run(run_test())


def test_sqlite_event_log_reads_get_job(tmp_path: Path) -> None:
    async def run_test() -> None:
        event_log = SQLiteDocumentProcessorEventLog(tmp_path / "events.sqlite3")
        job = DocumentProcessingJobRecord(
            job_id="job-123",
            original_filename="statement.pdf",
            stored_path=Path("data/uploads/abc.pdf"),
            status=DocumentProcessingJobStatus.CREATED,
        )

        await event_log.append(job)
        await event_log.append(job.with_status(DocumentProcessingJobStatus.STARTED))

        latest = await event_log.latest(job.job_id)

        assert latest == job.with_status(DocumentProcessingJobStatus.STARTED)

    asyncio.run(run_test())


def test_sqlite_event_log_history_returns_latest_state_per_job(
    tmp_path: Path,
) -> None:
    async def run_test() -> None:
        event_log = SQLiteDocumentProcessorEventLog(tmp_path / "events.sqlite3")
        first = DocumentProcessingJobRecord(
            job_id="job-1",
            original_filename="first.pdf",
            stored_path=Path("data/uploads/first.pdf"),
            status=DocumentProcessingJobStatus.CREATED,
        )
        second = DocumentProcessingJobRecord(
            job_id="job-2",
            original_filename="second.pdf",
            stored_path=Path("data/uploads/second.pdf"),
            status=DocumentProcessingJobStatus.CREATED,
        )

        await event_log.append(first)
        await event_log.append(second)
        await event_log.append(
            first.with_status(DocumentProcessingJobStatus.SUCCEEDED),
        )

        history = await event_log.history()

        assert history == [
            first.with_status(DocumentProcessingJobStatus.SUCCEEDED),
            second,
        ]

    asyncio.run(run_test())


def test_document_processing_service_updates_job_state_in_event_log(
    tmp_path: Path,
) -> None:
    async def run_test() -> None:
        event_log = SQLiteDocumentProcessorEventLog(tmp_path / "events.sqlite3")
        dispatcher = RecordingDocumentProcessorDispatcher()
        service = DocumentProcessingService(event_log, dispatcher)

        job = await service.create_job(
            "statement.pdf",
            Path("data/uploads/abc.pdf"),
        )
        updated = await service.update_job_status(
            job.job_id,
            DocumentProcessingJobStatus.STARTED,
        )

        assert await service.get_job(job.job_id) == updated
        with sqlite3.connect(tmp_path / "events.sqlite3") as connection:
            rows = connection.execute(
                "SELECT status FROM processing_jobs WHERE job_id = ? ORDER BY event_id",
                (job.job_id,),
            ).fetchall()

        assert [row[0] for row in rows] == [
            "DOCUMENT_PROCESSING_JOB_CREATED",
            "DOCUMENT_PROCESSING_JOB_STARTED",
        ]

    asyncio.run(run_test())


def test_document_processing_service_process_enqueues_document_processing(
    tmp_path: Path,
) -> None:
    async def run_test() -> None:
        event_log = SQLiteDocumentProcessorEventLog(tmp_path / "events.sqlite3")
        dispatcher = RecordingDocumentProcessorDispatcher()
        service = DocumentProcessingService(event_log, dispatcher)

        job = await service.create_job(
            "statement.pdf",
            Path("data/uploads/abc.pdf"),
        )

        assert dispatcher.enqueued == [(job.job_id, Path("data/uploads/abc.pdf"))]

    asyncio.run(run_test())


def test_document_processing_service_process_raises_when_enqueueing_fails(
    tmp_path: Path,
) -> None:
    async def run_test() -> None:
        event_log = SQLiteDocumentProcessorEventLog(tmp_path / "events.sqlite3")
        service = DocumentProcessingService(
            event_log,
            FailingDocumentProcessorDispatcher(),
        )

        with pytest.raises(DocumentProcessorUnavailableError):
            await service.create_job(
                "statement.pdf",
                Path("data/uploads/abc.pdf"),
            )

    asyncio.run(run_test())


def test_document_upload_service_rejects_non_pdf_extension(tmp_path: Path) -> None:
    async def run_test() -> None:
        service = DocumentUploadService(tmp_path)
        upload = UploadFile(
            filename="statement.txt",
            file=BytesIO(b"not a pdf"),
            headers=Headers({"content-type": "application/pdf"}),
        )

        with pytest.raises(ValueError, match="PDF"):
            await service.upload(upload=upload)

    asyncio.run(run_test())


def test_uploaded_document_uses_generated_pdf_path(tmp_path: Path) -> None:
    document = UploadedDocument.create(
        original_filename="../statement.pdf",
        upload_dir=tmp_path,
    )

    assert document.original_filename == "../statement.pdf"
    assert document.path.parent == tmp_path
    assert document.path.suffix == ".pdf"
    assert ".." not in document.path.name


def test_document_upload_service_persists_contents(tmp_path: Path) -> None:
    async def run_test() -> None:
        service = DocumentUploadService(tmp_path)
        upload = UploadFile(
            filename="statement.pdf",
            file=BytesIO(b"%PDF-1.7\ncontents"),
            headers=Headers({"content-type": "application/pdf"}),
        )

        document = await service.upload(upload=upload)

        assert document.original_filename == "statement.pdf"
        assert document.path.read_bytes() == b"%PDF-1.7\ncontents"

    asyncio.run(run_test())
