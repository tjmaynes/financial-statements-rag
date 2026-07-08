from __future__ import annotations

from dataclasses import dataclass
from os import environ
from pathlib import Path

EMBEDDING_MODEL_DIMENSIONS = {
    "text-embedding-3-small": 1536,
}


@dataclass(frozen=True)
class Settings:
    upload_dir: Path = Path("data/uploads")
    max_upload_count: int = 10
    redis_url: str = "redis://localhost:6379/0"
    sqlite_database_path: Path = Path("data/sec_filings_rag.sqlite3")
    log_level: str = "INFO"
    worker_concurrency: int = 3
    postgres_url: str = ""
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    chunk_size: int = 1000
    chunk_overlap: int = 150
    index_remaining_text: bool = True


def _required_env(name: str) -> str:
    value = environ.get(name)
    if value is None or value == "":
        raise RuntimeError(f"Required environment variable missing: {name}")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw_value = environ.get(name)
    if raw_value is None:
        return default
    return raw_value.lower() in {"1", "true", "yes", "on"}


def _embedding_dimension(model: str) -> int:
    try:
        return EMBEDDING_MODEL_DIMENSIONS[model]
    except KeyError:
        raise RuntimeError(f"Unsupported embedding model: {model}") from None


def load_settings_from_env() -> Settings:
    redis_url = environ.get("SFR_REDIS_URL", "redis://localhost:6379/0")
    sqlite_database_path = Path(
        environ.get(
            "SFR_SQLITE_DATABASE_PATH",
            "data/sec_filings_rag.sqlite3",
        ),
    )
    embedding_model = environ.get("SFR_EMBEDDING_MODEL", "text-embedding-3-small")

    return Settings(
        upload_dir=Path(environ.get("SFR_UPLOAD_DIR", "data/uploads")),
        max_upload_count=int(environ.get("SFR_MAX_UPLOAD_COUNT", "10")),
        redis_url=redis_url,
        sqlite_database_path=sqlite_database_path,
        log_level=environ.get("SFR_LOG_LEVEL", "INFO"),
        worker_concurrency=int(environ.get("SFR_WORKER_CONCURRENCY", "3")),
        postgres_url=_required_env("SFR_POSTGRES_URL"),
        openai_api_key=_required_env("SFR_OPENAI_API_KEY"),
        embedding_model=embedding_model,
        embedding_dimension=_embedding_dimension(embedding_model),
        chunk_size=int(environ.get("SFR_CHUNK_SIZE", "1000")),
        chunk_overlap=int(environ.get("SFR_CHUNK_OVERLAP", "150")),
        index_remaining_text=_bool_env("SFR_INDEX_REMAINING_TEXT", True),
    )
