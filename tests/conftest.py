from __future__ import annotations

from os import environ


def pytest_configure() -> None:
    environ.setdefault("FSR_POSTGRES_URL", "postgresql://localhost/fsr_test")
    environ.setdefault("FSR_OPENAI_API_KEY", "sk-test")
