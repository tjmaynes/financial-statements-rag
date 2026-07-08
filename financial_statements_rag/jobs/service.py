from __future__ import annotations

from financial_statements_rag.jobs.dispatcher import (
    DocumentJobDispatcher,
    DocumentJobUnavailableError,
)
from financial_statements_rag.jobs.event_log import DocumentJobEventLog
from financial_statements_rag.jobs.models import (
    DocumentJobRecord,
    DocumentJobStatus,
    JobMetadata,
    new_job_id,
)
from financial_statements_rag.logging import get_logger

logger = get_logger("jobs.service")


class DocumentJobService:
    def __init__(
        self,
        event_log: DocumentJobEventLog,
        dispatcher: DocumentJobDispatcher,
    ) -> None:
        self._event_log = event_log
        self._dispatcher = dispatcher

    async def create_job(
        self,
        job_metadata: JobMetadata,
    ) -> DocumentJobRecord:
        job_metadata_copy = dict(job_metadata)
        job = DocumentJobRecord(
            job_id=new_job_id(),
            job_metadata=job_metadata_copy,
            status=DocumentJobStatus.CREATED,
        )
        await self._event_log.append(job)
        logger.info("job created", extra={"job_id": job.job_id})
        try:
            await self._dispatcher.enqueue(job.job_id, job_metadata_copy)
        except Exception as error:
            logger.exception("job enqueue failed", extra={"job_id": job.job_id})
            if isinstance(error, DocumentJobUnavailableError):
                raise
            raise DocumentJobUnavailableError(
                "Document processing is temporarily unavailable"
            ) from error
        return job

    async def get_job(self, job_id: str) -> DocumentJobRecord | None:
        return await self._event_log.latest(job_id)

    async def job_history(self, limit: int = 50) -> list[DocumentJobRecord]:
        return await self._event_log.history(limit)

    async def update_job_status(
        self,
        job_id: str,
        status: DocumentJobStatus,
        error_message: str | None = None,
    ) -> DocumentJobRecord:
        job = await self._event_log.latest(job_id)
        if job is None:
            raise KeyError(job_id)
        updated = job.with_status(status, error_message)
        await self._event_log.append(updated)
        logger.info("job status updated", extra={"job_id": job_id})
        return updated
