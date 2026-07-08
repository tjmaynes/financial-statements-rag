import asyncio
from pathlib import Path

from arq import Retry

from financial_statements_rag.errors import (
    DocumentProcessingError,
    RetryableProcessingError,
)
from financial_statements_rag.jobs import (
    DocumentJobService,
    DocumentJobStatus,
    SQLiteDocumentJobEventLog,
    build_upload_job_metadata,
)
from financial_statements_rag.workers import WorkerSettings
from financial_statements_rag.workers.process_document import (
    process_document_job,
)


class SuccessfulWorkflow:
    async def ainvoke(self, state: dict[str, object]) -> dict[str, object]:
        return state


class TerminalFailureWorkflow:
    async def ainvoke(self, state: dict[str, object]) -> dict[str, object]:
        raise DocumentProcessingError("document_invalid", "Document is invalid")


class UnexpectedFailureWorkflow:
    async def ainvoke(self, state: dict[str, object]) -> dict[str, object]:
        raise ValueError("secret details")


class RetryableFailureWorkflow:
    async def ainvoke(self, state: dict[str, object]) -> dict[str, object]:
        raise RetryableProcessingError(
            "index_unavailable",
            "Indexing service is temporarily unavailable",
        )


class RecordingDocumentProcessorDispatcher:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, dict[str, object]]] = []

    async def enqueue(self, job_id: str, job_metadata: dict[str, object]) -> None:
        self.enqueued.append((job_id, job_metadata))


async def _worker_context(
    tmp_path: Path,
    workflow: object,
    *,
    job_try: int = 1,
    max_tries: int = 3,
) -> tuple[dict[str, object], DocumentJobService, str]:
    service = DocumentJobService(
        SQLiteDocumentJobEventLog(tmp_path / "events.sqlite3"),
        RecordingDocumentProcessorDispatcher(),
    )
    job = await service.create_job(
        build_upload_job_metadata(
            original_filename="statement.pdf",
            stored_path=Path("data/uploads/abc.pdf"),
        )
    )
    return (
        {
            "document_job_service": service,
            "document_ingestion_workflow": workflow,
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
            SuccessfulWorkflow(),
        )

        await process_document_job(
            context,
            job_id,
            build_upload_job_metadata(
                original_filename="statement.pdf",
                stored_path=Path("data/uploads/abc.pdf"),
            ),
        )

        updated = await service.get_job(job_id)
        assert updated is not None
        assert updated.status == DocumentJobStatus.SUCCEEDED

    asyncio.run(run_test())


def test_worker_records_safe_message_for_known_processing_error(
    tmp_path: Path,
) -> None:
    async def run_test() -> None:
        context, service, job_id = await _worker_context(
            tmp_path,
            TerminalFailureWorkflow(),
        )

        await process_document_job(
            context,
            job_id,
            build_upload_job_metadata(
                original_filename="statement.pdf",
                stored_path=Path("data/uploads/abc.pdf"),
            ),
        )

        updated = await service.get_job(job_id)
        assert updated is not None
        assert updated.status == DocumentJobStatus.FAILED
        assert updated.error_message == "Document is invalid"

    asyncio.run(run_test())


def test_worker_masks_unexpected_errors(tmp_path: Path) -> None:
    async def run_test() -> None:
        context, service, job_id = await _worker_context(
            tmp_path,
            UnexpectedFailureWorkflow(),
        )

        await process_document_job(
            context,
            job_id,
            build_upload_job_metadata(
                original_filename="statement.pdf",
                stored_path=Path("data/uploads/abc.pdf"),
            ),
        )

        updated = await service.get_job(job_id)
        assert updated is not None
        assert updated.status == DocumentJobStatus.FAILED
        assert updated.error_message == "Document processing failed unexpectedly"

    asyncio.run(run_test())


def test_worker_retries_retryable_errors_before_final_attempt(tmp_path: Path) -> None:
    async def run_test() -> None:
        context, service, job_id = await _worker_context(
            tmp_path,
            RetryableFailureWorkflow(),
            job_try=1,
            max_tries=3,
        )

        try:
            await process_document_job(
                context,
                job_id,
                build_upload_job_metadata(
                    original_filename="statement.pdf",
                    stored_path=Path("data/uploads/abc.pdf"),
                ),
            )
        except Retry:
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError("expected ARQ retry")

        updated = await service.get_job(job_id)
        assert updated is not None
        assert updated.status == DocumentJobStatus.STARTED

    asyncio.run(run_test())


def test_worker_marks_job_failed_after_final_retryable_attempt(tmp_path: Path) -> None:
    async def run_test() -> None:
        context, service, job_id = await _worker_context(
            tmp_path,
            RetryableFailureWorkflow(),
            job_try=3,
            max_tries=3,
        )

        await process_document_job(
            context,
            job_id,
            build_upload_job_metadata(
                original_filename="statement.pdf",
                stored_path=Path("data/uploads/abc.pdf"),
            ),
        )

        updated = await service.get_job(job_id)
        assert updated is not None
        assert updated.status == DocumentJobStatus.FAILED
        assert updated.error_message == "Indexing service is temporarily unavailable"

    asyncio.run(run_test())


def test_worker_invokes_workflow_with_job_id_and_document_path(tmp_path: Path) -> None:
    class RecordingWorkflow:
        def __init__(self) -> None:
            self.invocations: list[dict[str, object]] = []

        async def ainvoke(self, state: dict[str, object]) -> dict[str, object]:
            self.invocations.append(state)
            return state

    async def run_test() -> None:
        workflow = RecordingWorkflow()
        context, _service, job_id = await _worker_context(
            tmp_path,
            workflow,
        )

        await process_document_job(
            context,
            job_id,
            build_upload_job_metadata(
                original_filename="statement.pdf",
                stored_path=Path("data/uploads/abc.pdf"),
            ),
        )

        assert workflow.invocations == [
            {
                "job_id": job_id,
                "document_path": Path("data/uploads/abc.pdf"),
                "original_filename": "statement.pdf",
            }
        ]

    asyncio.run(run_test())
