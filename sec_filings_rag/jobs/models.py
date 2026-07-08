from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

type JobMetadata = dict[str, object]


class DocumentJobStatus(StrEnum):
    CREATED = "DOCUMENT_PROCESSING_JOB_CREATED"
    STARTED = "DOCUMENT_PROCESSING_JOB_STARTED"
    SUCCEEDED = "DOCUMENT_PROCESSING_JOB_SUCCEEDED"
    FAILED = "DOCUMENT_PROCESSING_JOB_FAILED"


@dataclass(frozen=True)
class DocumentJobRecord:
    job_id: str
    job_metadata: JobMetadata
    status: DocumentJobStatus
    error_message: str | None = None

    @property
    def original_filename(self) -> str:
        value = self.job_metadata.get("original_filename")
        return value if isinstance(value, str) else ""

    @property
    def stored_path(self) -> Path | None:
        value = self.job_metadata.get("stored_path")
        if not isinstance(value, str) or not value:
            return None
        return Path(value)

    def with_status(
        self,
        status: DocumentJobStatus,
        error_message: str | None = None,
    ) -> DocumentJobRecord:
        return replace(self, status=status, error_message=error_message)


def build_upload_job_metadata(
    *,
    original_filename: str,
    stored_path: Path,
) -> JobMetadata:
    return {
        "original_filename": original_filename,
        "stored_path": str(stored_path),
    }


def new_job_id() -> str:
    return uuid4().hex
