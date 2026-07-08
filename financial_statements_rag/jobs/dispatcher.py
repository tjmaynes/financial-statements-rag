from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from arq.connections import ArqRedis, RedisSettings, create_pool

from financial_statements_rag.logging import get_logger

logger = get_logger("jobs.dispatcher")


class DocumentJobDispatcher(Protocol):
    async def enqueue(self, job_id: str, document_path: Path) -> None: ...


class DocumentJobUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class RedisDocumentJobDispatcher:
    redis_url: str
    _pool: ArqRedis | None = None

    async def enqueue(self, job_id: str, document_path: Path) -> None:
        try:
            pool = await self._get_pool()
            enqueued_job = await pool.enqueue_job(
                "process_document_job",
                job_id,
                str(document_path),
                _job_id=job_id,
            )
        except Exception as error:
            logger.exception("enqueue failed", extra={"job_id": job_id})
            raise DocumentJobUnavailableError(
                "Document processing is temporarily unavailable"
            ) from error

        if enqueued_job is None:
            raise DocumentJobUnavailableError(
                "Document processing is temporarily unavailable"
            )

        logger.info("job enqueued", extra={"job_id": job_id})

    async def _get_pool(self) -> ArqRedis:
        if self._pool is None:
            object.__setattr__(
                self,
                "_pool",
                await create_pool(RedisSettings.from_dsn(self.redis_url)),
            )
        pool = self._pool
        if pool is None:  # pragma: no cover - defensive
            raise RuntimeError("dispatcher pool was not initialized")
        return pool
