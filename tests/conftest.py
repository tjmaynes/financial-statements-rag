from __future__ import annotations

from os import environ


def pytest_configure() -> None:
    environ.setdefault("SFR_POSTGRES_URL", "postgresql://localhost/SFR_test")
    environ.setdefault("SFR_OPENAI_API_KEY", "sk-test")
