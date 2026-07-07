from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from arq import Retry
from arq.connections import RedisSettings

from financial_statements_rag.processing import (
    DocumentProcessingService,
    DocumentProcessingJobStatus,
    RedisDocumentProcessorDispatcher,
    SQLiteDocumentProcessorEventLog,
)
from financial_statements_rag.logging import configure_logging, get_logger
from financial_statements_rag.pipeline import (
    DocumentProcessingPipeline,
    DefaultDocumentProcessorPipeline,
    RetryableProcessingError,
)
from financial_statements_rag.settings import Settings, load_settings_from_env

logger = get_logger("worker")
DEFAULT_MAX_TRIES = 3


async def startup(ctx: dict[str, Any]) -> None:
    settings = load_settings_from_env()
    configure_logging(settings)

    ctx["settings"] = settings
    ctx["document_processing_service"] = DocumentProcessingService(
        SQLiteDocumentProcessorEventLog(settings.sqlite_database_path),
        RedisDocumentProcessorDispatcher(settings.redis_url),
    )
    ctx["process_document_pipeline"] = DefaultDocumentProcessorPipeline()


async def shutdown(ctx: dict[str, Any]) -> None:
    logger.info("worker shutdown")


def _is_final_try(ctx: dict[str, Any]) -> bool:
    job_try = int(ctx.get("job_try", 1))
    max_tries = int(ctx.get("max_tries", DEFAULT_MAX_TRIES))
    return job_try >= max_tries


async def process_document_job(
    ctx: dict[str, Any],
    job_id: str,
    document_path: str,
) -> None:
    document_processing_service = cast(
        DocumentProcessingService,
        ctx["document_processing_service"],
    )
    process_document_pipeline = cast(
        DocumentProcessingPipeline, ctx["process_document_pipeline"]
    )

    try:
        await document_processing_service.update_job_status(
            job_id,
            DocumentProcessingJobStatus.STARTED,
        )
        await process_document_pipeline.process(Path(document_path), job_id)
        await document_processing_service.update_job_status(
            job_id,
            DocumentProcessingJobStatus.SUCCEEDED,
        )
        logger.info("worker job succeeded", extra={"job_id": job_id})
    except RetryableProcessingError as error:
        if _is_final_try(ctx):
            await document_processing_service.update_job_status(
                job_id,
                DocumentProcessingJobStatus.FAILED,
                str(error),
            )
            logger.exception("worker job exhausted retries", extra={"job_id": job_id})
            return

        logger.warning("worker retry requested", extra={"job_id": job_id})
        raise Retry(defer=0) from error
    except Exception as error:
        await document_processing_service.update_job_status(
            job_id,
            DocumentProcessingJobStatus.FAILED,
            str(error),
        )
        logger.exception("worker job failed", extra={"job_id": job_id})


_settings = load_settings_from_env()


class WorkerSettings:
    functions = [process_document_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = _settings.worker_concurrency
    max_tries = DEFAULT_MAX_TRIES
    retry_jobs = True
