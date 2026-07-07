# financial-statements-rag

> RAG pipeline for analyzing financial statements

## Requirements

- [Python](https://www.python.org)
- [Docker](https://www.docker.com) for containerized local runs

## Usage

Install project dependencies:
```bash
make install
```

Start FastAPI app + Redis + ARQ workers locally:
```bash
make start
```

Open the upload UI:
```text
http://localhost:8000/
```

The Compose stack starts:
- `financial-statements-rag-web`: FastAPI app on `http://localhost:8000/`
- `financial-statements-rag-worker`: ARQ worker process for background document processing
- `financial-statements-rag-redis`: Redis `8.8.0` for ARQ dispatch

## Configuration

Runtime settings use the `FSR_*` prefix:

| Variable | Default | Purpose |
|----------|---------|---------|
| `FSR_REDIS_URL` | `redis://localhost:6379/0` | Redis URL for ARQ dispatch. |
| `FSR_SQLITE_DATABASE_PATH` | `data/financial_statements_rag.sqlite3` | SQLite append-only `processing_jobs` event log path. |
| `FSR_UPLOAD_DIR` | `data/uploads` | Directory where uploaded PDFs are stored. |
| `FSR_MAX_UPLOAD_COUNT` | `10` | Maximum PDFs accepted in a single upload request. |
| `FSR_LOG_LEVEL` | `INFO` | Application log level. |
| `FSR_WORKER_CONCURRENCY` | `3` | ARQ worker concurrency (`max_jobs`). |

## Upload Flow

The index page accepts up to 10 PDF files per upload. Each accepted PDF is stored with a generated safe filename while preserving the original filename as metadata. The app creates one document processing job per accepted PDF and enqueues a background task through ARQ.

Job status fragments include the job ID, original filename, current status, and any processing error message. Non-terminal jobs poll `GET /jobs/{job_id}` every 2 seconds through HTMX.

Supported statuses:

- `DOCUMENT_PROCESSING_JOB_CREATED`
- `DOCUMENT_PROCESSING_JOB_STARTED`
- `DOCUMENT_PROCESSING_JOB_SUCCEEDED`
- `DOCUMENT_PROCESSING_JOB_FAILED`

Redis powers the ARQ broker. SQLite records append-only job lifecycle events in the `processing_jobs` table and is the source for latest job status reads.

## Development

Run all tests:
```bash
make test
```

Build the package:
```bash
make build
```

Lint the project:
```bash
make lint
```

Format the project:
```bash
make format
```
