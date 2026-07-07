import asyncio
from pathlib import Path

from financial_statements_rag.processing import (
    DocumentProcessingService,
    DocumentProcessingJobStatus,
    SQLiteDocumentProcessorEventLog,
)
from financial_statements_rag.pipeline import DefaultDocumentProcessorPipeline


class RecordingDocumentProcessorDispatcher:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, Path]] = []

    async def enqueue(self, job_id: str, document_path: Path) -> None:
        self.enqueued.append((job_id, document_path))
