from __future__ import annotations

from types import SimpleNamespace

from full_stack_data_agent.app.databao_runtime import DatabaoRuntime
from full_stack_data_agent.config.provider_resolution import provider_resolution_summary, resolve_provider_config
from full_stack_data_agent.config.settings import get_settings
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
