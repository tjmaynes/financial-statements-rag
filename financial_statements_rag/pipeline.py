from __future__ import annotations

from pathlib import Path
from typing import Protocol

from financial_statements_rag.processing import DocumentProcessingService
from financial_statements_rag.logging import get_logger

logger = get_logger("pipeline")


class RetryableProcessingError(RuntimeError):
    pass


class DocumentProcessingPipeline(Protocol):
    async def process(self, document_path: Path, job_id: str) -> None: ...


class DefaultDocumentProcessorPipeline:
    async def process(self, document_path: Path, job_id: str) -> None:
        # TODO: move document saving logic to here (see DocumentUploadService.upload)
        logger.info("pipeline default processed", extra={"job_id": job_id})

        # TODO: extract pdf content

        # TODO: https://docs.langchain.com/oss/python/langgraph/agentic-rag#1-preprocess-documents
