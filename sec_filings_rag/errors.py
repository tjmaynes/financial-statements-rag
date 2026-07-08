from __future__ import annotations


class DocumentProcessingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RetryableProcessingError(DocumentProcessingError):
    pass
