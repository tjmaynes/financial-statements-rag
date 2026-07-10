# Project Agent Guide

> Scope: Root project. These instructions apply to the whole repository unless a nested AGENTS.md overrides them.

## Quick Facts

| Area | Details |
|------|---------|
| Project | FastAPI backend for uploading SEC filing PDFs, indexing them for retrieval, answering questions over indexed chunks, and tracking document processing jobs. |
| Language | Python 3.14.6 |
| Package/build | `pyproject.toml` with Hatchling |
| Web stack | FastAPI, Jinja2, HTMX, Materialize CSS |
| State | Redis-backed ARQ dispatch, SQLite `processing_jobs` plus `conversation_history`, and Postgres plus `pgvector` retrieval data |
| Test/lint | pytest, mypy strict, Ruff, and GitHub Actions CI via `make build` |
| Local services | Web app, ARQ worker, Redis, and Postgres plus `pgvector` through Docker Compose |

## Repository Tour

Trimmed project map:

```text
.
├── sec_filings_rag/
│   ├── answers/            # Ask-answer orchestration and SQLite conversation history
│   ├── errors.py            # Safe terminal and retryable processing error types
│   ├── ingestion/           # PDF extraction, indexing, and LangGraph ingestion workflow
│   ├── jobs/                # Job models, job service, SQLite event log, and ARQ dispatch
│   ├── logging.py           # Package logging configuration
│   ├── main.py              # ASGI app and CLI entrypoint
│   ├── retrieval/           # Read-side vector search over indexed chunks
│   ├── settings.py          # SFR_* environment settings
│   ├── storage.py           # Upload validation and local PDF storage
│   ├── workers/             # ARQ worker modules and job handlers
│   └── web/
│       ├── app.py           # FastAPI app factory and dependency wiring
│       ├── routes.py        # Index, upload, job status, and ask routes
│       └── templates/       # Jinja/HTMX/Materialize templates
├── tests/                   # Unit and route tests
├── docs/                    # Project docs; generated/private docs may be gitignored
├── examples/                # Example input PDFs for manual testing
├── scripts/install.sh       # Local virtualenv and editable install bootstrap
├── Dockerfile
├── docker-compose.yml
├── Makefile
└── README.md
```

## Architecture

- Uploads enter through `POST /upload`, are validated and saved by `sec_filings_rag.storage.DocumentUploadService`, then create one document job per accepted PDF.
- Invalid PDFs are rendered as per-file upload errors while valid PDFs in the same request still enqueue.
- The FastAPI app creates a `DocumentJobRecord`, appends every lifecycle change to SQLite through `SQLiteDocumentJobEventLog`, and uses SQLite history for latest-state reads.
- `POST /ask` validates a JSON request, rejects prompt-injection attempts, embeds the question, retrieves matching chunks from Postgres, builds a strict prompt, and returns an answer with inline citations plus a `citations` array.
- `sec_filings_rag.retrieval.search.ChunkSearchService` owns read-side pgvector lookup against `document_chunks`.
- `sec_filings_rag.answers.service.AnswerService` owns question embeddings, prompt construction, citation repair, and answer generation.
- Completed asks are written to SQLite `conversation_history` through `SQLiteConversationHistory`; blocked prompt-injection requests are rejected before persistence.
- `RedisDocumentJobDispatcher` enqueues `process_document_job` onto ARQ. `sec_filings_rag.workers.process_document` loads the same `Settings`, configures logging, and updates job state as a LangGraph workflow runs.
- SQLite is append-only history. Every job creation/status transition inserts a new `processing_jobs` row.
- Status display reads the latest job state from SQLite history. The index page renders recent jobs newest first. The frontend polls `GET /jobs/{job_id}` every 2 seconds while a job is non-terminal and stops polling after success or failure.
- `process_document_job` calls `build_document_ingestion_workflow(...).ainvoke(...)` directly with `job_id` and `document_path`.
- The LangGraph workflow loads PDF pages, infers report metadata, detects statement sections, extracts line-item candidates, builds chunks, creates embeddings, and persists indexed rows into Postgres plus `pgvector`.
- Safe error contracts live in `sec_filings_rag/errors.py`. `RetryableProcessingError` triggers ARQ retry behavior before final failure is recorded.
- FastAPI app construction is centralized in `sec_filings_rag/web/app.py`; route handlers should stay thin and delegate to services.

### Network Diagram

```mermaid
flowchart LR
    Browser["Browser / HTMX UI"]
    WebApp["FastAPI app\nsec_filings_rag.web.app"]
    AnswerService["AnswerService"]
    Retrieval["ChunkSearchService"]
    StorageModule["storage.py\nupload validation and storage"]
    JobsModule["jobs/*\njob service, dispatcher,\nand event log"]
    Dispatcher["RedisDocumentJobDispatcher"]
    Redis["Redis / ARQ broker"]
    Worker["ARQ worker\nsec_filings_rag.workers.process_document"]
    Workflow["LangGraph workflow\nsec_filings_rag.ingestion.workflow"]
    EventLog["SQLiteDocumentJobEventLog"]
    UploadDir["Upload dir\nSFR_UPLOAD_DIR"]
    SQLite[(SQLite\nprocessing_jobs +\nconversation_history)]
    Postgres[(Postgres\npgvector)]

    Browser -->|POST /upload and GET job status| WebApp
    Browser -->|POST /ask| WebApp
    WebApp --> StorageModule
    WebApp --> JobsModule
    WebApp --> AnswerService
    AnswerService --> Retrieval
    AnswerService --> SQLite
    Retrieval --> Postgres
    StorageModule --> UploadDir
    JobsModule --> EventLog
    JobsModule --> Dispatcher
    Dispatcher --> Redis
    Redis --> Worker
    Worker --> Workflow
    Workflow --> UploadDir
    Workflow --> Postgres
    Worker --> EventLog
    EventLog --> SQLite
```

## Tooling And Setup

- Use Python `3.14.6`; `.python-version` is authoritative.
- Install dependencies with `make install`.
- Start the local Compose stack with `make start`.
- `make start` runs `docker compose up --build` for the web app, worker, Redis, and Postgres plus `pgvector`.
- Runtime settings use the `SFR_*` prefix:
  - `SFR_REDIS_URL`
  - `SFR_POSTGRES_URL`
  - `SFR_OPENAI_API_KEY`
  - `SFR_SQLITE_DATABASE_PATH`
  - `SFR_CHAT_MODEL`
  - `SFR_UPLOAD_DIR`
  - `SFR_MAX_UPLOAD_COUNT`
  - `SFR_LOG_LEVEL`
  - `SFR_WORKER_CONCURRENCY`
  - `SFR_EMBEDDING_MODEL`
  - `SFR_CHUNK_SIZE`
  - `SFR_CHUNK_OVERLAP`
  - `SFR_INDEX_REMAINING_TEXT`
- Do not commit runtime data under `data/`, build outputs, caches, or virtual environments.

## Common Tasks

- `make install` — install the package and dev dependencies into `.venv`.
- `make start` — run the Docker Compose stack with the FastAPI app, worker, Redis, and Postgres plus `pgvector`.
- `make format` — format Python code with Ruff.
- `make lint` — run mypy strict and Ruff checks.
- `make test` — run pytest and doctests.
- `make build` — clean, lint, test, and build the package.
- `.github/workflows/ci.yml` — GitHub Actions workflow for pull requests and pushes to `main`, running `make build` on Python `3.14.6`.
- `docker compose config` — validate the Compose file without starting containers, including the Postgres plus `pgvector` service.

## Testing And Quality Gates

- Add or update tests for behavior changes in `tests/`.
- Prefer behavioral tests over implementation-detail tests.
- Route tests should assert HTTP behavior, rendered data, and HTMX behavior that affects functionality.
- Do not write styling tests. Do not assert CSS framework class names, colors, spacing classes, or purely visual markup.
- Run `make format`, `make lint`, and `make test` before handing work back.
- Run `make build` for broader changes or when touching packaging, Docker, or dependencies.
- Coverage tooling is not currently configured; do not claim coverage thresholds were met unless tooling is added and run.

## Dos

- Prefer creating a class when two or more related functions share state, dependencies, or a domain concept. Example: use `DocumentUploadService` rather than scattered upload helper functions.
- Keep route handlers small; push domain behavior into services or focused modules.
- Keep retrieval read behavior in `sec_filings_rag/retrieval/` and answer orchestration in `sec_filings_rag/answers/`.
- Use explicit protocols for swappable boundaries such as queues, processors, and event logs.
- Keep SQLite `processing_jobs` append-only. Add reads, not update/delete behavior, unless the data model is intentionally redesigned.
- Keep `conversation_history` writes limited to completed ask results; do not persist blocked prompt-injection requests.
- Keep ARQ dispatch details centralized in `RedisDocumentJobDispatcher`.
- Keep LangGraph workflow orchestration centralized in `sec_filings_rag/ingestion/workflow.py`.
- Keep upload validation and local PDF storage in `sec_filings_rag/storage.py`.
- Keep job event log, dispatcher, and document job service behavior in `sec_filings_rag/jobs/`.
- Preserve `job_id` in job lifecycle logs and rendered status updates.
- Use `SFR_*` for new runtime environment variables.
- Update `README.md` when setup, commands, runtime behavior, or user-facing workflows change.

## Don'ts

- Do not write tests that assert visual styling, CSS classes, Materialize-specific class names, colors, or layout-only markup.
- Do not log PDF contents.
- Do not store uploads using user-provided filenames as paths.
- Do not mutate SQLite history rows in place.
- Do not add new global settings with non-`SFR_*` prefixes.
- Do not log PDF contents, chunk text, embeddings, OpenAI keys, or Postgres credentials.
- Do not move retrieval SQL into route handlers or ingestion write-side modules.
- Do not run destructive git commands such as `git reset --hard` or `git checkout --` unless explicitly requested.

## Documentation Duties

- Update `README.md` for significant feature, setup, command, or environment changes.
- Update `docs/` only when design notes or project documentation would otherwise become misleading.
- Keep this `AGENTS.md` current when project conventions change.

## Finish The Task Checklist

- [ ] Run the relevant focused tests first when changing behavior.
- [ ] Run `make format`.
- [ ] Run `make lint`.
- [ ] Run `make test`.
- [ ] Run `make build` for packaging, dependency, Docker, or broad changes.
- [ ] Update `README.md` and other docs if behavior or workflow changed.
- [ ] Summarize changes with a conventional commit message, for example `feat(web): add upload history`.
- [ ] Call out any checks that could not be run and why.
