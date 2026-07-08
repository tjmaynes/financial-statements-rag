import logging

from _pytest.logging import LogCaptureFixture
from _pytest.monkeypatch import MonkeyPatch

from sec_filings_rag.logging import configure_logging, get_logger
from sec_filings_rag.settings import Settings, load_settings_from_env


def test_configure_logging_respects_log_level(caplog: LogCaptureFixture) -> None:
    configure_logging(Settings(log_level="DEBUG"))
    logger = get_logger("test")

    with caplog.at_level(logging.DEBUG, logger="sec_filings_rag"):
        logger.debug("debug event", extra={"job_id": "job-123"})

    assert "debug event" in caplog.text
    assert "job-123" in caplog.text


def test_settings_loads_redis_and_sqlite_urls_without_backend_selector(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("SFR_REDIS_URL", "redis://example.test:6379/0")
    monkeypatch.setenv("SFR_SQLITE_DATABASE_PATH", "data/events.sqlite3")
    monkeypatch.setenv("SFR_POSTGRES_URL", "postgresql://localhost/fsr")
    monkeypatch.setenv("SFR_OPENAI_API_KEY", "sk-test")

    settings = load_settings_from_env()

    assert settings.redis_url == "redis://example.test:6379/0"
    assert str(settings.sqlite_database_path) == "data/events.sqlite3"
    assert not hasattr(settings, "job_store_backend")
