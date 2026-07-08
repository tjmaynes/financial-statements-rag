from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from uuid import uuid4


class DocumentJobStatus(StrEnum):
    CREATED = "DOCUMENT_PROCESSING_JOB_CREATED"
    STARTED = "DOCUMENT_PROCESSING_JOB_STARTED"
    SUCCEEDED = "DOCUMENT_PROCESSING_JOB_SUCCEEDED"
    FAILED = "DOCUMENT_PROCESSING_JOB_FAILED"


@dataclass(frozen=True)
class DocumentJobRecord:
    job_id: str
    original_filename: str
    stored_path: Path
    status: DocumentJobStatus
    error_message: str | None = None

    def with_status(
        self,
        status: DocumentJobStatus,
        error_message: str | None = None,
    ) -> DocumentJobRecord:
        return replace(self, status=status, error_message=error_message)


def new_job_id() -> str:
    return uuid4().hex
