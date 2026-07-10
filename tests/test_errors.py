from sec_filings_rag.errors import (
    ChunkSearchNoMatchError,
    ChunkSearchNoRetrievableChunksError,
    DocumentProcessingError,
    QuestionAnsweringUnavailableError,
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


def test_chunk_search_no_match_error_is_distinct() -> None:
    error = ChunkSearchNoMatchError()

    assert str(error) == "No matching chunks found"


def test_chunk_search_no_retrievable_chunks_error_is_distinct() -> None:
    error = ChunkSearchNoRetrievableChunksError()

    assert str(error) == "No retrievable chunks found"


def test_question_answering_unavailable_error_is_retryable() -> None:
    error = QuestionAnsweringUnavailableError()

    assert isinstance(error, RetryableProcessingError)
    assert error.code == "QUESTION_ANSWERING_UNAVAILABLE"
    assert str(error) == "Question answering is temporarily unavailable"
