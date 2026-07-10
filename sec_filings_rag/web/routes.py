from __future__ import annotations

from datetime import UTC, datetime
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from sec_filings_rag.answers import (
    AnswerCitation,
    AnsweringService,
    AnswerRequest,
    AnswerResult,
    PROMPT_INJECTION_DETAIL,
    PromptInjectionDetector,
)
from sec_filings_rag.errors import QuestionAnsweringUnavailableError
from sec_filings_rag.jobs import (
    DocumentJobRecord,
    DocumentJobService,
    DocumentJobStatus,
    DocumentJobUnavailableError,
    build_upload_job_metadata,
)
from sec_filings_rag.logging import get_logger
from sec_filings_rag.retrieval import (
    ChunkSearchNoMatchError,
    ChunkSearchNoRetrievableChunksError,
    ChunkSearchUnavailableError,
)
from sec_filings_rag.settings import Settings
from sec_filings_rag.storage import DocumentUploadService

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=TEMPLATE_DIR)
logger = get_logger("web.routes")
TERMINAL_STATUSES = {
    DocumentJobStatus.SUCCEEDED,
    DocumentJobStatus.FAILED,
}
VALID_STATEMENT_TYPES = {
    "income_statement",
    "balance_sheet",
    "cash_flow_statement",
}


@dataclass(frozen=True)
class UploadError:
    filename: str
    message: str


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _document_job_service(request: Request) -> DocumentJobService:
    return cast(DocumentJobService, request.app.state.document_job_service)


def _document_upload_service(request: Request) -> DocumentUploadService:
    return cast(DocumentUploadService, request.app.state.document_upload_service)


def _answer_service(request: Request) -> AnsweringService:
    return cast(AnsweringService, request.app.state.answer_service)


def _current_utc_year() -> int:
    return datetime.now(UTC).year


def _validate_optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="Invalid request payload")
    trimmed = value.strip()
    if trimmed == "":
        return None
    return trimmed


def _validate_statement_types(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="Invalid request payload")
    if not value:
        raise HTTPException(
            status_code=400,
            detail=(
                "statement_types must contain only income_statement, "
                "balance_sheet, or cash_flow_statement"
            ),
        )
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise HTTPException(status_code=400, detail="Invalid request payload")
        if item not in VALID_STATEMENT_TYPES:
            raise HTTPException(
                status_code=400,
                detail=(
                    "statement_types must contain only income_statement, "
                    "balance_sheet, or cash_flow_statement"
                ),
            )
        normalized.append(item)
    return tuple(normalized)


def _validate_int(
    value: object,
    *,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise HTTPException(status_code=400, detail="Invalid request payload")
    if minimum is not None and value < minimum:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 10"
            if minimum == 1 and maximum == 10
            else "Invalid request payload",
        )
    if maximum is not None and value > maximum:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 10"
            if minimum == 1 and maximum == 10
            else "Invalid request payload",
        )
    return value


async def _parse_ask_request(request: Request) -> AnswerRequest:
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(
            status_code=415,
            detail="Content-Type must be application/json",
        )

    try:
        payload = await request.json()
    except JSONDecodeError as error:
        raise HTTPException(
            status_code=400,
            detail="Request body must be valid JSON",
        ) from error

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail="Request body must be a JSON object",
        )

    question_value = payload.get("question")
    if question_value is None:
        raise HTTPException(status_code=400, detail="Question is required")
    if not isinstance(question_value, str):
        raise HTTPException(status_code=400, detail="Invalid request payload")
    question = question_value.strip()
    if question == "":
        raise HTTPException(status_code=400, detail="Question is required")

    company_name = _validate_optional_text(payload.get("company_name"))
    ticker = _validate_optional_text(payload.get("ticker"))
    if company_name is None and ticker is None:
        raise HTTPException(
            status_code=400,
            detail="Either company_name or ticker is required",
        )

    statement_types = _validate_statement_types(payload.get("statement_types"))
    fiscal_year = _validate_int(
        payload.get("fiscal_year"),
        default=_current_utc_year(),
    )
    limit = _validate_int(payload.get("limit"), default=5, minimum=1, maximum=10)

    inspection = PromptInjectionDetector().inspect(question)
    if inspection.blocked:
        raise HTTPException(status_code=400, detail=PROMPT_INJECTION_DETAIL)

    return AnswerRequest(
        question=question,
        company_name=company_name,
        ticker=ticker,
        fiscal_year=fiscal_year,
        statement_types=statement_types,
        limit=limit,
    )


def _citation_payload(citation: AnswerCitation) -> dict[str, object]:
    return {
        "index": citation.index,
        "chunk_id": citation.chunk_id,
        "document_id": citation.document_id,
        "company_name": citation.company_name,
        "ticker": citation.ticker,
        "report_date": (
            citation.report_date.isoformat()
            if citation.report_date is not None
            else None
        ),
        "statement_type": citation.statement_type,
        "section_title": citation.section_title,
        "page_start": citation.page_start,
        "page_end": citation.page_end,
        "text": citation.text,
    }


def _answer_payload(result: AnswerResult) -> dict[str, object]:
    return {
        "answer": result.answer,
        "citations": [_citation_payload(citation) for citation in result.citations],
    }


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/")
    async def index(request: Request) -> Response:
        jobs = await _document_job_service(request).job_history()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "jobs": jobs,
                "errors": [],
                "terminal_statuses": TERMINAL_STATUSES,
            },
        )

    @router.post("/upload")
    async def upload(
        request: Request,
        files: Annotated[list[UploadFile] | None, File()] = None,
    ) -> Response:
        if not files:
            raise HTTPException(status_code=400, detail="At least one PDF is required")
        settings = _settings(request)
        logger.info("upload request received file_count=%s", len(files))
        if len(files) > settings.max_upload_count:
            raise HTTPException(
                status_code=400,
                detail=f"Upload at most {settings.max_upload_count} PDFs",
            )

        document_upload_service = _document_upload_service(request)
        document_job_service = _document_job_service(request)

        jobs: list[DocumentJobRecord] = []
        errors: list[UploadError] = []

        for upload_file in files:
            filename = upload_file.filename or ""
            try:
                document = await document_upload_service.upload(upload_file)
            except ValueError as error:
                logger.info("upload validation failed filename=%s", filename)
                errors.append(UploadError(filename=filename, message=str(error)))
                continue

            try:
                job = await document_job_service.create_job(
                    build_upload_job_metadata(
                        original_filename=document.original_filename,
                        stored_path=document.path,
                    )
                )
            except DocumentJobUnavailableError:
                raise HTTPException(
                    status_code=503,
                    detail="Document processing is temporarily unavailable",
                ) from None
            logger.info("upload job accepted", extra={"job_id": job.job_id})
            jobs.append(job)

        return templates.TemplateResponse(
            request,
            "partials/job_cards.html",
            {
                "jobs": list(reversed(jobs)),
                "errors": errors,
                "terminal_statuses": TERMINAL_STATUSES,
            },
        )

    @router.get("/jobs/{job_id}")
    async def job_status(request: Request, job_id: str) -> Response:
        job = await _document_job_service(request).get_job(job_id)
        if job is None:
            logger.info("unknown job lookup", extra={"job_id": job_id})
            raise HTTPException(status_code=404, detail="Job not found")

        return templates.TemplateResponse(
            request,
            "partials/job_status.html",
            {"job": job, "terminal_statuses": TERMINAL_STATUSES},
        )

    @router.post("/ask")
    async def ask(request: Request) -> dict[str, object]:
        answer_request = await _parse_ask_request(request)
        try:
            result = await _answer_service(request).answer(answer_request)
        except ChunkSearchNoMatchError as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        except ChunkSearchNoRetrievableChunksError as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        except (ChunkSearchUnavailableError, QuestionAnsweringUnavailableError):
            raise HTTPException(
                status_code=503,
                detail="Question answering is temporarily unavailable",
            ) from None

        return _answer_payload(result)

    return router
