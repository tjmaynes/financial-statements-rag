from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import importlib
from pathlib import Path
from typing import cast

from sec_filings_rag.ingestion.extraction._errors import (
    DocumentProcessingError,
)


PageLoader = Callable[[Path], Sequence[str]]


class EncryptedPdfError(RuntimeError):
    pass


class UnreadablePdfError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str
    source_path: Path


def _default_loader(document_path: Path) -> Sequence[str]:
    pypdf = importlib.import_module("pypdf")
    pypdf_errors = importlib.import_module("pypdf.errors")
    pdf_reader = getattr(pypdf, "PdfReader")
    file_not_decrypted_error = cast(
        type[Exception],
        getattr(pypdf_errors, "FileNotDecryptedError"),
    )
    pdf_read_error = cast(
        type[Exception],
        getattr(pypdf_errors, "PdfReadError"),
    )

    try:
        reader = pdf_reader(str(document_path))
    except FileNotFoundError:
        raise
    except file_not_decrypted_error as error:
        raise EncryptedPdfError from error
    except pdf_read_error as error:
        raise UnreadablePdfError from error
    except OSError as error:
        raise UnreadablePdfError from error

    if reader.is_encrypted:
        raise EncryptedPdfError

    try:
        return [page.extract_text() or "" for page in reader.pages]
    except file_not_decrypted_error as error:
        raise EncryptedPdfError from error
    except pdf_read_error as error:
        raise UnreadablePdfError from error


@dataclass(frozen=True)
class PdfPageExtractor:
    loader: PageLoader = _default_loader

    def extract(self, document_path: Path) -> list[ExtractedPage]:
        if not document_path.exists():
            raise DocumentProcessingError(
                "PDF_FILE_NOT_FOUND",
                "Uploaded PDF could not be found",
            )

        try:
            page_texts = list(self.loader(document_path))
        except FileNotFoundError as error:
            raise DocumentProcessingError(
                "PDF_FILE_NOT_FOUND",
                "Uploaded PDF could not be found",
            ) from error
        except EncryptedPdfError as error:
            raise DocumentProcessingError(
                "PDF_ENCRYPTED",
                "Uploaded PDF is encrypted and cannot be processed",
            ) from error
        except UnreadablePdfError as error:
            raise DocumentProcessingError(
                "PDF_UNREADABLE",
                "Uploaded PDF could not be read",
            ) from error

        pages = [
            ExtractedPage(
                page_number=index,
                text=text,
                source_path=document_path,
            )
            for index, text in enumerate(page_texts, start=1)
        ]

        if not any(page.text.strip() for page in pages):
            raise DocumentProcessingError(
                "PDF_TEXT_EXTRACTION_FAILED",
                "Could not extract text from the PDF",
            )

        return pages
