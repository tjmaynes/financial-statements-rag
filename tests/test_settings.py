from _pytest.monkeypatch import MonkeyPatch
import pytest

from sec_filings_rag.settings import Settings, load_settings_from_env


def test_missing_redis_url_reports_required_env_var(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("SFR_REDIS_URL", raising=False)
    monkeypatch.setenv("SFR_POSTGRES_URL", "postgresql://localhost/fsr")
    monkeypatch.setenv("SFR_OPENAI_API_KEY", "sk-test")

    with pytest.raises(
        RuntimeError,
        match="Required environment variable missing: SFR_REDIS_URL",
    ):
        load_settings_from_env()


def test_missing_postgres_url_reports_required_env_var(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("SFR_POSTGRES_URL", raising=False)
    monkeypatch.delenv("SFR_OPENAI_API_KEY", raising=False)

    with pytest.raises(
        RuntimeError,
        match="Required environment variable missing: SFR_POSTGRES_URL",
    ):
        load_settings_from_env()


def test_missing_openai_key_reports_required_env_var(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("SFR_POSTGRES_URL", "postgresql://localhost/fsr")
    monkeypatch.delenv("SFR_OPENAI_API_KEY", raising=False)

    with pytest.raises(
        RuntimeError,
        match="Required environment variable missing: SFR_OPENAI_API_KEY",
    ):
        load_settings_from_env()


def test_openai_api_key_is_not_used_as_fallback(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("SFR_POSTGRES_URL", "postgresql://localhost/fsr")
    monkeypatch.setenv("OPENAI_API_KEY", "ignored")
    monkeypatch.delenv("SFR_OPENAI_API_KEY", raising=False)

    with pytest.raises(
        RuntimeError,
        match="Required environment variable missing: SFR_OPENAI_API_KEY",
    ):
        load_settings_from_env()


def test_indexing_settings_defaults_are_loaded(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("SFR_POSTGRES_URL", "postgresql://localhost/fsr")
    monkeypatch.setenv("SFR_OPENAI_API_KEY", "sk-test")

    settings = load_settings_from_env()

    assert settings.postgres_url == "postgresql://localhost/fsr"
    assert settings.openai_api_key == "sk-test"
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.embedding_dimension == 1536
    assert settings.chat_model == "gpt-4.1-mini"
    assert settings.chunk_size == 1000
    assert settings.chunk_overlap == 150
    assert settings.index_remaining_text is True


def test_unsupported_embedding_model_fails_startup(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("SFR_POSTGRES_URL", "postgresql://localhost/fsr")
    monkeypatch.setenv("SFR_OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("SFR_EMBEDDING_MODEL", "unknown-model")

    with pytest.raises(
        RuntimeError,
        match="Unsupported embedding model: unknown-model",
    ):
        load_settings_from_env()


def test_settings_can_be_constructed_directly_for_tests() -> None:
    settings = Settings()

    assert settings.redis_url == ""
    assert settings.postgres_url == ""
    assert settings.openai_api_key == ""
    assert settings.chat_model == "gpt-4.1-mini"
