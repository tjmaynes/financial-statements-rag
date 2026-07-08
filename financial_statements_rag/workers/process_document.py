from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from arq import Retry
from arq.connections import RedisSettings

from financial_statements_rag.errors import (
    DocumentProcessingError,
    RetryableProcessingError,
)
from financial_statements_rag.ingestion.extraction.metadata import (
    ReportMetadataInferer,
)
from financial_statements_rag.ingestion.extraction.pdf import PdfPageExtractor
from financial_statements_rag.ingestion.extraction.statements import StatementExtractor
from financial_statements_rag.ingestion.indexing.embeddings import (
    OpenAIEmbeddingProvider,
)
from financial_statements_rag.ingestion.indexing.store import PostgresIndexStore
from financial_statements_rag.ingestion.workflow import (
    DocumentIngestionWorkflow,
    build_document_ingestion_workflow,
)
from financial_statements_rag.jobs import (
    DocumentJobService,
    DocumentJobStatus,
    RedisDocumentJobDispatcher,
    SQLiteDocumentJobEventLog,
)
from financial_statements_rag.logging import configure_logging, get_logger
from financial_statements_rag.settings import load_settings_from_env

logger = get_logger("worker")
DEFAULT_MAX_TRIES = 3
UNEXPECTED_FAILURE_MESSAGE = "Document processing failed unexpectedly"


async def startup(ctx: dict[str, Any]) -> None:
    settings = load_settings_from_env()
    configure_logging(settings)
    index_store = PostgresIndexStore(settings.postgres_url)
    index_store.initialize()

    ctx["settings"] = settings
    ctx["document_job_service"] = DocumentJobService(
        SQLiteDocumentJobEventLog(settings.sqlite_database_path),
        RedisDocumentJobDispatcher(settings.redis_url),
    )
    ctx["document_ingestion_workflow"] = build_document_ingestion_workflow(
        settings=settings,
        pdf_page_extractor=PdfPageExtractor(),
        report_metadata_inferer=ReportMetadataInferer(),
        statement_extractor=StatementExtractor(),
        embedding_provider=OpenAIEmbeddingProvider(settings),
        index_store=index_store,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    logger.info("worker shutdown")


def _is_final_try(ctx: dict[str, Any]) -> bool:
    job_try = int(ctx.get("job_try", 1))
    max_tries = int(ctx.get("max_tries", DEFAULT_MAX_TRIES))
    return job_try >= max_tries


def _safe_failure_message(error: DocumentProcessingError) -> str:
    return error.message


async def process_document_job(
    ctx: dict[str, Any],
    job_id: str,
    document_path: str,
) -> None:
    document_job_service = cast(
        DocumentJobService,
        ctx["document_job_service"],
    )
    document_ingestion_workflow = cast(
        DocumentIngestionWorkflow,
        ctx["document_ingestion_workflow"],
    )

    try:
        await document_job_service.update_job_status(
            job_id,
            DocumentJobStatus.STARTED,
        )
        await document_ingestion_workflow.ainvoke(
            {
                "job_id": job_id,
                "document_path": Path(document_path),
            }
        )
        await document_job_service.update_job_status(
            job_id,
            DocumentJobStatus.SUCCEEDED,
        )
        logger.info("worker job succeeded", extra={"job_id": job_id})
    except RetryableProcessingError as error:
        if _is_final_try(ctx):
            await document_job_service.update_job_status(
                job_id,
                DocumentJobStatus.FAILED,
                _safe_failure_message(error),
            )
            logger.exception("worker job exhausted retries", extra={"job_id": job_id})
            return

        logger.warning("worker retry requested", extra={"job_id": job_id})
        raise Retry(defer=0) from error
    except DocumentProcessingError as error:
        await document_job_service.update_job_status(
            job_id,
            DocumentJobStatus.FAILED,
            _safe_failure_message(error),
        )
        logger.exception("worker job failed", extra={"job_id": job_id})
    except Exception:
        await document_job_service.update_job_status(
            job_id,
            DocumentJobStatus.FAILED,
            UNEXPECTED_FAILURE_MESSAGE,
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
