from __future__ import annotations


class DocumentProcessingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RetryableProcessingError(DocumentProcessingError):
    pass


class ChunkSearchNoMatchError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("No matching chunks found")


class ChunkSearchNoRetrievableChunksError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("No retrievable chunks found")


class QuestionAnsweringUnavailableError(RetryableProcessingError):
    def __init__(self) -> None:
        super().__init__(
            "QUESTION_ANSWERING_UNAVAILABLE",
            "Question answering is temporarily unavailable",
        )
