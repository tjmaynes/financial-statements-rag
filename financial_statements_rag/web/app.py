from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from financial_statements_rag.processing import (
    DocumentProcessingDispatcher,
    DocumentProcessingService,
    SQLiteDocumentProcessorEventLog,
    RedisDocumentProcessorDispatcher,
)
from financial_statements_rag.logging import configure_logging, get_logger
from financial_statements_rag.settings import Settings, load_settings_from_env
from financial_statements_rag.processing import DocumentUploadService
from financial_statements_rag.web.routes import create_router

logger = get_logger("web.app")


def create_app(
    document_processing_service: DocumentProcessingService | None = None,
    document_processing_dispatcher: DocumentProcessingDispatcher | None = None,
    document_upload_service: DocumentUploadService | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    resolved_settings = settings or load_settings_from_env()
    configure_logging(resolved_settings)

    app = FastAPI(title="Financial Statements RAG")
    app.state.settings = resolved_settings
    app.state.document_processing_service = (
        document_processing_service
        or DocumentProcessingService(
            SQLiteDocumentProcessorEventLog(resolved_settings.sqlite_database_path),
            document_processing_dispatcher
            or RedisDocumentProcessorDispatcher(resolved_settings.redis_url),
        )
    )
    app.state.document_upload_service = (
        document_upload_service or DocumentUploadService(resolved_settings.upload_dir)
    )
    app.include_router(create_router())

    logger.info(
        "app created upload_dir=%s max_upload_count=%s redis_url=%s sqlite_path=%s",
        resolved_settings.upload_dir,
        resolved_settings.max_upload_count,
        resolved_settings.redis_url,
        resolved_settings.sqlite_database_path,
    )
    return app


app = create_app()
