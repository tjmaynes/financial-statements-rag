from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from starlette.datastructures import UploadFile

from financial_statements_rag.logging import get_logger

logger = get_logger("storage")


@dataclass(frozen=True)
class UploadedDocument:
    original_filename: str
    path: Path

    @classmethod
    def create(cls, original_filename: str, upload_dir: Path) -> UploadedDocument:
        return cls(
            original_filename=original_filename,
            path=upload_dir / f"{uuid4().hex}.pdf",
        )


class DocumentUploadService:
    def __init__(self, upload_dir: Path) -> None:
        self._upload_dir = upload_dir

    async def upload(self, upload: UploadFile) -> UploadedDocument:
        filename = upload.filename or ""
        self._validate_pdf(filename=filename, content_type=upload.content_type)
        document = UploadedDocument.create(
            original_filename=filename,
            upload_dir=self._upload_dir,
        )
        try:
            self._upload_dir.mkdir(parents=True, exist_ok=True)
            document.path.write_bytes(await upload.read())
        except Exception:
            logger.exception("upload storage failed")
            raise
        return document

    def _validate_pdf(self, filename: str, content_type: str | None) -> None:
        if not filename.lower().endswith(".pdf"):
            logger.info("upload validation failed: file must be a PDF")
            raise ValueError("Uploaded file must be a PDF")
        if content_type not in {None, "", "application/pdf"}:
            logger.info("upload validation failed: content type must be PDF")
            raise ValueError("Uploaded file must be a PDF")
