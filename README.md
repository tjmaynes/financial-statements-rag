# financial-statements-rag

> FastAPI app for uploading financial statement PDFs and tracking background document processing jobs.

## Requirements

- [Python](https://www.python.org) `3.14.6`
- [Docker](https://www.docker.com) for the local Compose stack

## Usage

Install project dependencies into `.venv`:

```bash
make install
```

Start the local Compose stack:

```bash
make start
```

Open the upload UI:

```text
http://localhost:8000/
```

The Compose stack starts:

- `financial-statements-rag-web`: FastAPI app on `http://localhost:8000/`
- `financial-statements-rag-worker`: ARQ worker process running `financial_statements_rag.workers.process_document.WorkerSettings`
- `financial-statements-rag-redis`: Redis `8.8.0` for ARQ dispatch
- `financial-statements-rag-postgres`: Postgres `16` with `pgvector` for indexed document storage

Uploaded files and SQLite runtime data are stored in the `financial-statements-rag-data` Docker volume. Local non-container defaults write under `data/`, which is ignored by git.

## Configuration

Runtime settings use the `FSR_*` prefix:

| Variable | Default | Purpose |
|----------|---------|---------|
| `FSR_REDIS_URL` | `redis://localhost:6379/0` | Redis URL for ARQ dispatch. |
| `FSR_POSTGRES_URL` | required | Postgres connection URL for LangGraph indexing data and `pgvector` storage. |
| `FSR_OPENAI_API_KEY` | required | OpenAI API key used for embeddings. The app does not fall back to `OPENAI_API_KEY`. |
| `FSR_SQLITE_DATABASE_PATH` | `data/financial_statements_rag.sqlite3` | SQLite append-only `processing_jobs` event log path. |
| `FSR_UPLOAD_DIR` | `data/uploads` | Directory where uploaded PDFs are stored. |
| `FSR_MAX_UPLOAD_COUNT` | `10` | Maximum PDFs accepted in a single upload request. |
| `FSR_LOG_LEVEL` | `INFO` | Application log level. |
| `FSR_WORKER_CONCURRENCY` | `3` | ARQ worker concurrency (`max_jobs`). |
| `FSR_EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI embedding model. |
| `FSR_CHUNK_SIZE` | `1000` | Chunk size used when splitting statement and remaining text. |
| `FSR_CHUNK_OVERLAP` | `150` | Overlap between adjacent chunks. |
| `FSR_INDEX_REMAINING_TEXT` | `true` | Whether non-statement text also becomes indexed chunks. |

`docker-compose.yml` requires `FSR_OPENAI_API_KEY` in your shell before startup. OpenAI keys are passed through environment variables only and should not be logged or committed.

## Upload Flow

The index page accepts up to 10 PDF files per upload. Each accepted PDF is validated and stored by `financial_statements_rag.storage.DocumentUploadService` with a generated safe filename while preserving the original filename as metadata. The `financial_statements_rag.jobs.DocumentJobService` then creates one document job per accepted PDF, appends a `DOCUMENT_PROCESSING_JOB_CREATED` event to SQLite, and enqueues a background ARQ task.

Invalid PDFs are reported in the upload response without preventing valid PDFs in the same request from being accepted. If Redis or ARQ enqueueing is unavailable, the upload returns `503` with `Document processing is temporarily unavailable`.

The index page renders the latest known state for recent jobs, newest first. Job status fragments include the job ID, original filename, current status, and any processing error message. Non-terminal jobs poll `GET /jobs/{job_id}` every 2 seconds through HTMX; terminal jobs stop polling.

Supported statuses:

- `DOCUMENT_PROCESSING_JOB_CREATED`
- `DOCUMENT_PROCESSING_JOB_STARTED`
- `DOCUMENT_PROCESSING_JOB_SUCCEEDED`
- `DOCUMENT_PROCESSING_JOB_FAILED`

Redis powers the ARQ broker. SQLite records append-only job lifecycle events in the `processing_jobs` table and is the source for latest job status reads.

## Document Processing Workflow

`process_document_job` in `financial_statements_rag.workers.process_document` uses `financial_statements_rag.jobs` for status tracking and `financial_statements_rag.ingestion` for document transformation. The compiled LangGraph workflow:

- loads PDF pages with page-level provenance
- infers report metadata such as company, ticker, report period, currency, and scale
- detects statement sections and line-item candidates
- builds statement-first chunks, then optional remaining-text chunks
- embeds chunks with OpenAI
- persists documents, sections, line items, chunks, and vectors into Postgres with `pgvector`

SQLite remains the append-only job history store. Postgres stores retrieval data only.

Workers update jobs to `DOCUMENT_PROCESSING_JOB_STARTED`, run the LangGraph workflow, then append either `DOCUMENT_PROCESSING_JOB_SUCCEEDED` or `DOCUMENT_PROCESSING_JOB_FAILED`. Known processing failures record safe messages from `DocumentProcessingError` or `RetryableProcessingError`. Unexpected exceptions are masked as `Document processing failed unexpectedly`.

## Optional Integration Check

To exercise the local indexing runtime manually:

1. Export `FSR_OPENAI_API_KEY` in your shell.
2. Run `make start`.
3. Upload a representative quarterly report PDF at `http://localhost:8000/`.
4. Confirm the worker marks the job as succeeded and Postgres receives indexed rows.

## Development

GitHub Actions runs `make build` on pull requests and pushes to `main` using Python `3.14.6` with cached pip dependencies.

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

Validate the Compose file:

```bash
docker compose config
```
