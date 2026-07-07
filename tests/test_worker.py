import asyncio
from pathlib import Path

from arq import Retry

from financial_statements_rag.processing import (
    DocumentProcessingService,
    DocumentProcessingJobStatus,
    SQLiteDocumentProcessorEventLog,
)
from financial_statements_rag.pipeline import RetryableProcessingError
from financial_statements_rag.worker import WorkerSettings, process_document_job


class SuccessfulPipeline:
    async def status(self, job_id: str) -> None:  # pragma: no cover - not used in tests
        return None

    async def process(self, document_path: Path, job_id: str) -> None:
        return None


class TerminalFailurePipeline:
    async def status(self, job_id: str) -> None:  # pragma: no cover - not used in tests
        return None

    async def process(self, document_path: Path, job_id: str) -> None:
        raise ValueError("bad document")


class RetryableFailurePipeline:
    async def status(self, job_id: str) -> None:  # pragma: no cover - not used in tests
        return None

    async def process(self, document_path: Path, job_id: str) -> None:
        raise RetryableProcessingError("redis timed out")


class RecordingDocumentProcessorDispatcher:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, Path]] = []

    async def enqueue(self, job_id: str, document_path: Path) -> None:
        self.enqueued.append((job_id, document_path))


async def _worker_context(
    tmp_path: Path,
    pipeline: object,
    *,
    job_try: int = 1,
    max_tries: int = 3,
) -> tuple[dict[str, object], DocumentProcessingService, str]:
    service = DocumentProcessingService(
        SQLiteDocumentProcessorEventLog(tmp_path / "events.sqlite3"),
        RecordingDocumentProcessorDispatcher(),
    )
    job = await service.create_job("statement.pdf", Path("data/uploads/abc.pdf"))
    return (
        {
            "document_processing_service": service,
            "process_document_pipeline": pipeline,
            "job_try": job_try,
            "max_tries": max_tries,
        },
        service,
        job.job_id,
    )


def test_worker_settings_use_concurrency_three() -> None:
    assert WorkerSettings.max_jobs == 3


def test_worker_marks_job_succeeded(tmp_path: Path) -> None:
    async def run_test() -> None:
        context, service, job_id = await _worker_context(
            tmp_path,
            SuccessfulPipeline(),
        )

        await process_document_job(context, job_id, "data/uploads/abc.pdf")

        updated = await service.get_job(job_id)
        assert updated is not None
        assert updated.status == DocumentProcessingJobStatus.SUCCEEDED

    asyncio.run(run_test())


def test_worker_marks_job_failed_for_terminal_errors(tmp_path: Path) -> None:
    async def run_test() -> None:
        context, service, job_id = await _worker_context(
            tmp_path,
            TerminalFailurePipeline(),
        )

        await process_document_job(context, job_id, "data/uploads/abc.pdf")

        updated = await service.get_job(job_id)
        assert updated is not None
        assert updated.status == DocumentProcessingJobStatus.FAILED
        assert updated.error_message == "bad document"

    asyncio.run(run_test())


def test_worker_retries_retryable_errors_before_final_attempt(tmp_path: Path) -> None:
    async def run_test() -> None:
        context, service, job_id = await _worker_context(
            tmp_path,
            RetryableFailurePipeline(),
            job_try=1,
            max_tries=3,
        )

        try:
            await process_document_job(context, job_id, "data/uploads/abc.pdf")
        except Retry:
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError("expected ARQ retry")

        updated = await service.get_job(job_id)
        assert updated is not None
        assert updated.status == DocumentProcessingJobStatus.STARTED

    asyncio.run(run_test())


def test_worker_marks_job_failed_after_final_retryable_attempt(tmp_path: Path) -> None:
    async def run_test() -> None:
        context, service, job_id = await _worker_context(
            tmp_path,
            RetryableFailurePipeline(),
            job_try=3,
            max_tries=3,
        )

        await process_document_job(context, job_id, "data/uploads/abc.pdf")

        updated = await service.get_job(job_id)
        assert updated is not None
        assert updated.status == DocumentProcessingJobStatus.FAILED
        assert updated.error_message == "redis timed out"

    asyncio.run(run_test())
