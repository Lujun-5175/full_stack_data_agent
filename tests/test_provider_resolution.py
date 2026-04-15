from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlparse
from pathlib import Path

from full_stack_data_agent.app.databao_runtime import DatabaoRuntime
from full_stack_data_agent.config.provider_resolution import (
    _probe_deepseek_health,
    ResolvedProviderConfig,
    provider_resolution_summary,
    resolve_provider_config,
)
from full_stack_data_agent.config.settings import _read_env_file, get_settings
from full_stack_data_agent.llm.models import ProviderHealth


def test_settings_parse_deepseek_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "ollama")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:latest")
    get_settings.cache_clear()
    try:
        settings = get_settings()

        assert settings.llm_provider == "deepseek"
        assert settings.provider_name == "deepseek"
        assert settings.llm_fallback_provider == "ollama"
        assert settings.deepseek_api_key == "sk-test"
        assert settings.deepseek_base_url == "https://api.deepseek.com"
        assert settings.deepseek_model == "deepseek-chat"
        assert settings.llm_timeout == 480.0
        assert settings.llm_temperature == 0.2
        assert settings.llm_num_ctx == 8192
    finally:
        get_settings.cache_clear()


def test_settings_accepts_legacy_ollama_llm_numeric_env(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_TIMEOUT", "321")
    monkeypatch.setenv("OLLAMA_TEMPERATURE", "0.55")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "4096")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.llm_timeout == 321.0
        assert settings.llm_temperature == 0.55
        assert settings.llm_num_ctx == 4096
    finally:
        get_settings.cache_clear()


def test_read_env_file_parses_quoted_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        'DEEPSEEK_API_KEY="sk-test value"\n'
        "DEEPSEEK_BASE_URL='https://api.deepseek.com/v1?x=1&y=two'\n"
        'APP_RUNTIME_DIR="D:/data bao/runtime"\n',
        encoding="utf-8",
    )

    values = _read_env_file(env_file)

    assert values["DEEPSEEK_API_KEY"] == "sk-test value"
    assert values["DEEPSEEK_BASE_URL"] == "https://api.deepseek.com/v1?x=1&y=two"
    assert values["APP_RUNTIME_DIR"] == "D:/data bao/runtime"


def test_settings_prefers_llm_numeric_env_over_legacy(monkeypatch) -> None:
    monkeypatch.setenv("LLM_TIMEOUT", "111")
    monkeypatch.setenv("OLLAMA_TIMEOUT", "222")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.11")
    monkeypatch.setenv("OLLAMA_TEMPERATURE", "0.22")
    monkeypatch.setenv("LLM_NUM_CTX", "1234")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "5678")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.llm_timeout == 111.0
        assert settings.llm_temperature == 0.11
        assert settings.llm_num_ctx == 1234
    finally:
        get_settings.cache_clear()


def test_settings_raises_clear_error_for_invalid_llm_timeout(monkeypatch) -> None:
    monkeypatch.setenv("LLM_TIMEOUT", "abc")
    get_settings.cache_clear()
    try:
        try:
            get_settings()
            raise AssertionError("Expected ValueError for invalid LLM_TIMEOUT")
        except ValueError as exc:
            assert "LLM_TIMEOUT='abc'" in str(exc)
            assert "valid float" in str(exc)
    finally:
        get_settings.cache_clear()


def test_settings_raises_clear_error_for_invalid_llm_temperature(monkeypatch) -> None:
    monkeypatch.setenv("LLM_TEMPERATURE", "abc")
    get_settings.cache_clear()
    try:
        try:
            get_settings()
            raise AssertionError("Expected ValueError for invalid LLM_TEMPERATURE")
        except ValueError as exc:
            assert "LLM_TEMPERATURE='abc'" in str(exc)
            assert "valid float" in str(exc)
    finally:
        get_settings.cache_clear()


def test_settings_raises_clear_error_for_invalid_llm_num_ctx(monkeypatch) -> None:
    monkeypatch.setenv("LLM_NUM_CTX", "abc")
    get_settings.cache_clear()
    try:
        try:
            get_settings()
            raise AssertionError("Expected ValueError for invalid LLM_NUM_CTX")
        except ValueError as exc:
            assert "LLM_NUM_CTX='abc'" in str(exc)
            assert "valid integer" in str(exc)
    finally:
        get_settings.cache_clear()


def test_resolve_provider_config_defaults_to_deepseek(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_PROVIDER", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        resolved = resolve_provider_config(settings)
        summary = provider_resolution_summary(settings)

        assert resolved.provider == "deepseek"
        assert resolved.model == "deepseek-chat"
        assert resolved.fallback_provider == "ollama"
        assert summary == {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "api_key_present": False,
            "fallback_provider": "ollama",
        }
    finally:
        get_settings.cache_clear()


def test_provider_status_returns_unified_structure(monkeypatch) -> None:
    get_settings.cache_clear()
    try:
        settings = get_settings()
        runtime = DatabaoRuntime(settings)

        monkeypatch.setattr(
            "full_stack_data_agent.app.databao_runtime.check_runtime_dependencies",
            lambda: SimpleNamespace(is_ok=True),
        )
        monkeypatch.setattr(
            "full_stack_data_agent.app.databao_runtime.probe_provider_health",
            lambda _settings, provider_name=None: ProviderHealth(
                provider="deepseek" if provider_name != "ollama" else "ollama",
                base_url="https://api.deepseek.com" if provider_name != "ollama" else settings.ollama_base_url,
                model="deepseek-chat" if provider_name != "ollama" else settings.ollama_model,
                connected=True,
                available_models=[],
                api_key_present=provider_name != "ollama",
                fallback_provider="ollama",
                fallback_connected=True,
            ),
        )

        status = runtime.provider_status()

        assert status.provider == "deepseek"
        assert status.model == "deepseek-chat"
        assert status.base_url == "https://api.deepseek.com"
        assert status.connected is True
        assert status.fallback_provider == "ollama"
        assert status.fallback_connected is True
        assert status.api_key_present is True
    finally:
        get_settings.cache_clear()


def test_deepseek_probe_uses_models_endpoint_and_200_is_connected(monkeypatch) -> None:
    resolved = ResolvedProviderConfig(
        provider="deepseek",
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
        api_key_present=True,
        fallback_provider="ollama",
        temperature=0.2,
        timeout=30.0,
        num_ctx=2048,
        api_key="sk-test",
    )

    def _fake_get(url, **_kwargs):
        assert urlparse(url).path == "/v1/models"
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr("full_stack_data_agent.config.provider_resolution.requests.get", _fake_get)

    health = _probe_deepseek_health(resolved)

    assert health.connected is True
    assert health.error is None


def test_deepseek_probe_401_and_403_are_not_connected(monkeypatch) -> None:
    resolved = ResolvedProviderConfig(
        provider="deepseek",
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
        api_key_present=True,
        fallback_provider="ollama",
        temperature=0.2,
        timeout=30.0,
        num_ctx=2048,
        api_key="sk-test",
    )

    for status_code in (401, 403):
        monkeypatch.setattr(
            "full_stack_data_agent.config.provider_resolution.requests.get",
            lambda *_args, **_kwargs: SimpleNamespace(status_code=status_code),
        )
        health = _probe_deepseek_health(resolved)
        assert health.connected is False
        assert "authentication failed" in str(health.error).lower()


def test_deepseek_probe_404_is_not_connected(monkeypatch) -> None:
    resolved = ResolvedProviderConfig(
        provider="deepseek",
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
        api_key_present=True,
        fallback_provider="ollama",
        temperature=0.2,
        timeout=30.0,
        num_ctx=2048,
        api_key="sk-test",
    )
    monkeypatch.setattr(
        "full_stack_data_agent.config.provider_resolution.requests.get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=404),
    )

    health = _probe_deepseek_health(resolved)

    assert health.connected is False
    assert "404" in str(health.error)


def test_deepseek_probe_429_is_reachable_but_rate_limited(monkeypatch) -> None:
    resolved = ResolvedProviderConfig(
        provider="deepseek",
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
        api_key_present=True,
        fallback_provider="ollama",
        temperature=0.2,
        timeout=30.0,
        num_ctx=2048,
        api_key="sk-test",
    )
    monkeypatch.setattr(
        "full_stack_data_agent.config.provider_resolution.requests.get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=429),
    )

    health = _probe_deepseek_health(resolved)

    assert health.connected is True
    assert "rate limited" in str(health.error).lower()
