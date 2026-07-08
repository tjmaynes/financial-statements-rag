from pathlib import Path

import pytest

from sec_filings_rag.ingestion.extraction import DocumentProcessingError
from sec_filings_rag.ingestion.extraction.pdf import (
    EncryptedPdfError,
    ExtractedPage,
    PdfPageExtractor,
    UnreadablePdfError,
)


def test_missing_pdf_raises_safe_error(tmp_path: Path) -> None:
    extractor = PdfPageExtractor(loader=lambda _path: [])

    with pytest.raises(DocumentProcessingError) as error_info:
        extractor.extract(tmp_path / "missing.pdf")

    error = error_info.value
    assert error.code == "PDF_FILE_NOT_FOUND"
    assert str(error) == "Uploaded PDF could not be found"


def test_extracted_pages_preserve_page_numbers(tmp_path: Path) -> None:
    document_path = tmp_path / "statement.pdf"
    document_path.write_bytes(b"%PDF-1.4")
    extractor = PdfPageExtractor(loader=lambda _path: ["Cover page", "Balance sheet"])

    pages = extractor.extract(document_path)

    assert pages == [
        ExtractedPage(
            page_number=1,
            text="Cover page",
            source_path=document_path,
        ),
        ExtractedPage(
            page_number=2,
            text="Balance sheet",
            source_path=document_path,
        ),
    ]


def test_textless_pdf_raises_safe_error(tmp_path: Path) -> None:
    document_path = tmp_path / "statement.pdf"
    document_path.write_bytes(b"%PDF-1.4")
    extractor = PdfPageExtractor(loader=lambda _path: ["   ", ""])

    with pytest.raises(DocumentProcessingError) as error_info:
        extractor.extract(document_path)

    error = error_info.value
    assert error.code == "PDF_TEXT_EXTRACTION_FAILED"
    assert str(error) == "Could not extract text from the PDF"


def test_encrypted_pdf_raises_safe_error(tmp_path: Path) -> None:
    document_path = tmp_path / "statement.pdf"
    document_path.write_bytes(b"%PDF-1.4")

    def encrypted_loader(_path: Path) -> list[str]:
        raise EncryptedPdfError

    extractor = PdfPageExtractor(loader=encrypted_loader)

    with pytest.raises(DocumentProcessingError) as error_info:
        extractor.extract(document_path)

    error = error_info.value
    assert error.code == "PDF_ENCRYPTED"
    assert str(error) == "Uploaded PDF is encrypted and cannot be processed"


def test_unreadable_pdf_raises_safe_error(tmp_path: Path) -> None:
    document_path = tmp_path / "statement.pdf"
    document_path.write_bytes(b"%PDF-1.4")

    def unreadable_loader(_path: Path) -> list[str]:
        raise UnreadablePdfError

    extractor = PdfPageExtractor(loader=unreadable_loader)

    with pytest.raises(DocumentProcessingError) as error_info:
        extractor.extract(document_path)

    error = error_info.value
    assert error.code == "PDF_UNREADABLE"
    assert str(error) == "Uploaded PDF could not be read"
