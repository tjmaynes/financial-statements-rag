from sec_filings_rag.errors import (
    DocumentProcessingError,
    RetryableProcessingError,
)


def test_document_processing_error_exposes_safe_message() -> None:
    error = DocumentProcessingError("document_invalid", "Document is invalid")

    assert error.code == "document_invalid"
    assert str(error) == "Document is invalid"


def test_retryable_processing_error_exposes_safe_message() -> None:
    error = RetryableProcessingError(
        "index_unavailable",
        "Indexing service is temporarily unavailable",
    )

    assert error.code == "index_unavailable"
    assert str(error) == "Indexing service is temporarily unavailable"
