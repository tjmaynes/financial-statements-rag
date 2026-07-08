from __future__ import annotations

import logging

from sec_filings_rag.settings import Settings

BASE_LOGGER_NAME = "sec_filings_rag"


class JobIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "job_id"):
            record.job_id = "-"
        return True


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logger = logging.getLogger(BASE_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = True

    formatter = logging.Formatter(
        "%(levelname)s %(name)s [job_id=%(job_id)s] %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(JobIdFilter())
        handler.setFormatter(formatter)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{BASE_LOGGER_NAME}.{name}")
