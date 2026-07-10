from __future__ import annotations

from fastapi import FastAPI

from sec_filings_rag.answers import (
    AnswerService,
    AnsweringService,
    OpenAIChatCompletionProvider,
    SQLiteConversationHistory,
)
from sec_filings_rag.ingestion.indexing.embeddings import OpenAIEmbeddingProvider
from sec_filings_rag.jobs import (
    DocumentJobDispatcher,
    DocumentJobService,
    RedisDocumentJobDispatcher,
    SQLiteDocumentJobEventLog,
)
from sec_filings_rag.logging import configure_logging, get_logger
from sec_filings_rag.retrieval import ChunkSearchService
from sec_filings_rag.settings import Settings, load_settings_from_env
from sec_filings_rag.storage import DocumentUploadService
from sec_filings_rag.web.routes import create_router

logger = get_logger("web.app")


def create_app(
    document_job_service: DocumentJobService | None = None,
    document_job_dispatcher: DocumentJobDispatcher | None = None,
    document_upload_service: DocumentUploadService | None = None,
    answer_service: AnsweringService | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    resolved_settings = settings or load_settings_from_env()
    configure_logging(resolved_settings)

    app = FastAPI(title="SEC Filings RAG")
    app.state.settings = resolved_settings
    app.state.document_job_service = document_job_service or DocumentJobService(
        SQLiteDocumentJobEventLog(resolved_settings.sqlite_database_path),
        document_job_dispatcher
        or RedisDocumentJobDispatcher(resolved_settings.redis_url),
    )
    app.state.document_upload_service = (
        document_upload_service or DocumentUploadService(resolved_settings.upload_dir)
    )
    app.state.answer_service = answer_service or AnswerService(
        embedding_provider=OpenAIEmbeddingProvider(resolved_settings),
        search_service=ChunkSearchService(resolved_settings.postgres_url),
        chat_provider=OpenAIChatCompletionProvider(resolved_settings),
        conversation_history=SQLiteConversationHistory(
            resolved_settings.sqlite_database_path
        ),
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
