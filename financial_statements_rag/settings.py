from __future__ import annotations

from dataclasses import dataclass
from os import environ
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    upload_dir: Path = Path("data/uploads")
    max_upload_count: int = 10
    redis_url: str = "redis://localhost:6379/0"
    sqlite_database_path: Path = Path("data/financial_statements_rag.sqlite3")
    log_level: str = "INFO"
    worker_concurrency: int = 3


def load_settings_from_env() -> Settings:
    redis_url = environ.get("FSR_REDIS_URL", "redis://localhost:6379/0")
    sqlite_database_path = Path(
        environ.get(
            "FSR_SQLITE_DATABASE_PATH",
            "data/financial_statements_rag.sqlite3",
        ),
    )

    return Settings(
        upload_dir=Path(environ.get("FSR_UPLOAD_DIR", "data/uploads")),
        max_upload_count=int(environ.get("FSR_MAX_UPLOAD_COUNT", "10")),
        redis_url=redis_url,
        sqlite_database_path=sqlite_database_path,
        log_level=environ.get("FSR_LOG_LEVEL", "INFO"),
        worker_concurrency=int(environ.get("FSR_WORKER_CONCURRENCY", "3")),
    )
