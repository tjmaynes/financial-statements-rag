"""Worker modules."""

from sec_filings_rag.settings import load_settings_from_env
from sec_filings_rag.workers.process_document import (
    DEFAULT_MAX_TRIES,
    process_document_job,
    shutdown,
    startup,
)


from arq.connections import RedisSettings

_settings = load_settings_from_env()


class WorkerSettings:
    functions = [process_document_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = _settings.worker_concurrency
    max_tries = DEFAULT_MAX_TRIES
    retry_jobs = True
