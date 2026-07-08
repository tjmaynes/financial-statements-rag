import asyncio
from pathlib import Path
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from financial_statements_rag.jobs import (
    DocumentJobDispatcher,
    DocumentJobService,
    DocumentJobStatus,
    SQLiteDocumentJobEventLog,
)
from financial_statements_rag.settings import Settings
from financial_statements_rag.web.app import create_app

JOB_STATUS_TEMPLATE = (
    Path(__file__).resolve().parent.parent
    / "financial_statements_rag/web/templates/partials/job_status.html"
)


class RecordingDocumentProcessorDispatcher:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, dict[str, object]]] = []

    async def enqueue(self, job_id: str, job_metadata: dict[str, object]) -> None:
        self.enqueued.append((job_id, job_metadata))


class FailingDocumentProcessorDispatcher:
    async def enqueue(self, job_id: str, job_metadata: dict[str, object]) -> None:
        raise RuntimeError("redis unavailable")


def create_test_client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_test_app(tmp_path),
    )


def create_test_app(
    tmp_path: Path,
    dispatcher: DocumentJobDispatcher | None = None,
) -> FastAPI:
    resolved_dispatcher = dispatcher or RecordingDocumentProcessorDispatcher()
    document_job_service = DocumentJobService(
        SQLiteDocumentJobEventLog(tmp_path / "events.sqlite3"),
        resolved_dispatcher,
    )
    return create_app(
        document_job_service=document_job_service,
        document_job_dispatcher=resolved_dispatcher,
        settings=Settings(
            upload_dir=tmp_path / "uploads",
        ),
    )


def test_get_index_renders_upload_form(tmp_path: Path) -> None:
    client = create_test_client(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert 'hx-post="/upload"' in response.text
    assert 'hx-target="#jobs-list"' in response.text
    assert 'hx-swap="afterbegin"' in response.text
    assert 'type="file"' in response.text
    assert "multiple" in response.text


def test_job_status_template_uses_document_processor_status_names() -> None:
    template = JOB_STATUS_TEMPLATE.read_text()

    assert "DOCUMENT_PROCESSING_JOB_SUCCEEDED" in template
    assert "DOCUMENT_PROCESSING_JOB_FAILED" in template
    assert "DOCUMENT_PROCESSING_JOB_STARTED" in template
    assert "PROCESSING_SUCCEEDED" not in template
    assert "PROCESSING_FAILED" not in template
    assert "PROCESSING_STARTED" not in template


def test_get_index_renders_existing_job_history_latest_first(tmp_path: Path) -> None:
    client = create_test_client(tmp_path)
    client.post(
        "/upload",
        files=[("files", ("older.pdf", b"%PDF-1.4\n", "application/pdf"))],
    )
    client.post(
        "/upload",
        files=[("files", ("newer.pdf", b"%PDF-1.4\n", "application/pdf"))],
    )

    response = client.get("/")

    assert response.status_code == 200
    assert "newer.pdf" in response.text
    assert "older.pdf" in response.text
    assert response.text.index("newer.pdf") < response.text.index("older.pdf")
    assert "DOCUMENT_PROCESSING_JOB_CREATED" in response.text
    assert 'id="jobs-list"' in response.text


def test_upload_creates_one_enqueued_uploaded_job_per_pdf(tmp_path: Path) -> None:
    dispatcher = RecordingDocumentProcessorDispatcher()
    client = TestClient(create_test_app(tmp_path, dispatcher=dispatcher))

    response = client.post(
        "/upload",
        files=[
            ("files", ("a.pdf", b"%PDF-1.4\n", "application/pdf")),
            ("files", ("b.pdf", b"%PDF-1.4\n", "application/pdf")),
        ],
    )

    assert response.status_code == 200
    assert response.text.count("DOCUMENT_PROCESSING_JOB_CREATED") == 2
    assert "a.pdf" in response.text
    assert "b.pdf" in response.text
    assert 'id="jobs-list"' not in response.text
    assert len(dispatcher.enqueued) == 2
    assert all(
        isinstance(job_metadata.get("stored_path"), str)
        and str(job_metadata["stored_path"]).endswith(".pdf")
        for _, job_metadata in dispatcher.enqueued
    )


def test_upload_response_renders_new_jobs_latest_first(tmp_path: Path) -> None:
    client = create_test_client(tmp_path)

    response = client.post(
        "/upload",
        files=[
            ("files", ("older.pdf", b"%PDF-1.4\n", "application/pdf")),
            ("files", ("newer.pdf", b"%PDF-1.4\n", "application/pdf")),
        ],
    )

    assert response.status_code == 200
    assert response.text.index("newer.pdf") < response.text.index("older.pdf")


def test_upload_rejects_empty_upload(tmp_path: Path) -> None:
    client = create_test_client(tmp_path)

    response = client.post("/upload", files=[])

    assert response.status_code == 400


def test_upload_rejects_more_than_ten_files(tmp_path: Path) -> None:
    client = create_test_client(tmp_path)

    response = client.post(
        "/upload",
        files=[
            ("files", (f"statement-{index}.pdf", b"%PDF-1.4\n", "application/pdf"))
            for index in range(11)
        ],
    )

    assert response.status_code == 400


def test_upload_accepts_valid_files_and_reports_invalid_files(tmp_path: Path) -> None:
    client = create_test_client(tmp_path)

    response = client.post(
        "/upload",
        files=[
            ("files", ("statement.pdf", b"%PDF-1.4\n", "application/pdf")),
            ("files", ("notes.txt", b"not pdf", "text/plain")),
        ],
    )

    assert response.status_code == 200
    assert "statement.pdf" in response.text
    assert "notes.txt" in response.text
    assert "Uploaded file must be a PDF" in response.text


def test_upload_returns_503_when_enqueueing_fails(tmp_path: Path) -> None:
    client = TestClient(
        create_test_app(
            tmp_path,
            dispatcher=FailingDocumentProcessorDispatcher(),
        ),
    )

    response = client.post(
        "/upload",
        files=[("files", ("statement.pdf", b"%PDF-1.4\n", "application/pdf"))],
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Document processing is temporarily unavailable"


def test_unknown_job_returns_404(tmp_path: Path) -> None:
    client = create_test_client(tmp_path)

    response = client.get("/jobs/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"


def test_job_status_reads_latest_state_from_sqlite_history(tmp_path: Path) -> None:
    event_log = SQLiteDocumentJobEventLog(tmp_path / "events.sqlite3")
    dispatcher = RecordingDocumentProcessorDispatcher()
    document_job_service = DocumentJobService(event_log, dispatcher)
    client = TestClient(
        create_app(
            document_job_service=document_job_service,
            document_job_dispatcher=dispatcher,
            settings=Settings(
                upload_dir=tmp_path / "uploads",
            ),
        ),
    )
    upload = client.post(
        "/upload",
        files=[("files", ("sqlite.pdf", b"%PDF-1.4\n", "application/pdf"))],
    )
    match = re.search(r"Job ID: ([a-f0-9]+)", upload.text)
    assert match is not None

    asyncio.run(
        document_job_service.update_job_status(
            match.group(1),
            DocumentJobStatus.FAILED,
        ),
    )

    response = client.get(f"/jobs/{match.group(1)}")

    assert response.status_code == 200
    assert "DOCUMENT_PROCESSING_JOB_FAILED" in response.text


def test_non_terminal_job_fragment_polls_every_two_seconds(tmp_path: Path) -> None:
    client = create_test_client(tmp_path)

    response = client.post(
        "/upload",
        files=[("files", ("a.pdf", b"%PDF-1.4\n", "application/pdf"))],
    )

    assert response.status_code == 200
    assert 'hx-trigger="every 2s"' in response.text
    assert "Job ID" in response.text


def test_terminal_job_fragment_stops_polling(tmp_path: Path) -> None:
    event_log = SQLiteDocumentJobEventLog(tmp_path / "events.sqlite3")
    dispatcher = RecordingDocumentProcessorDispatcher()
    document_job_service = DocumentJobService(event_log, dispatcher)
    client = TestClient(
        create_app(
            document_job_service=document_job_service,
            document_job_dispatcher=dispatcher,
            settings=Settings(
                upload_dir=tmp_path / "uploads",
            ),
        ),
    )
    upload = client.post(
        "/upload",
        files=[("files", ("a.pdf", b"%PDF-1.4\n", "application/pdf"))],
    )
    match = re.search(r"Job ID: ([a-f0-9]+)", upload.text)
    assert match is not None
    asyncio.run(
        document_job_service.update_job_status(
            match.group(1),
            DocumentJobStatus.SUCCEEDED,
        ),
    )

    response = client.get(f"/jobs/{match.group(1)}")

    assert response.status_code == 200
    assert "DOCUMENT_PROCESSING_JOB_SUCCEEDED" in response.text
    assert 'hx-trigger="every 2s"' not in response.text
