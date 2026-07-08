from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from sec_filings_rag.jobs import (
    DocumentJobRecord,
    DocumentJobService,
    DocumentJobStatus,
    DocumentJobUnavailableError,
    build_upload_job_metadata,
)
from sec_filings_rag.logging import get_logger
from sec_filings_rag.settings import Settings
from sec_filings_rag.storage import DocumentUploadService

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=TEMPLATE_DIR)
logger = get_logger("web.routes")
TERMINAL_STATUSES = {
    DocumentJobStatus.SUCCEEDED,
    DocumentJobStatus.FAILED,
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

    return router
