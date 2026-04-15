from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from full_stack_data_agent.config.settings import Settings


def _settings() -> Settings:
    root = Path("D:/data_bao/full_stack_data_agent")
    return Settings(
        app_root=root,
        runtime_dir=root / ".runtime",
        domain_dir=root / ".runtime" / "context_domain",
        llm_provider="deepseek",
        llm_fallback_provider="ollama",
        deepseek_api_key="sk-test",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-chat",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="gemma4:e4b",
        llm_timeout=30.0,
        llm_temperature=0.2,
        llm_num_ctx=2048,
        binding_llm_enabled=True,
        context_turn_window=6,
        context_result_limit=4,
    )


def test_get_binding_llm_client_reuses_instance_for_same_settings() -> None:
    module = importlib.import_module("full_stack_data_agent.app.binding_llm_client")
    module._CLIENT_CACHE.clear()
    settings = _settings()

    first = module.get_binding_llm_client(settings)
    second = module.get_binding_llm_client(settings)

    assert first is second


def test_binding_llm_client_init_does_not_probe_network(monkeypatch) -> None:
    module = importlib.import_module("full_stack_data_agent.app.binding_llm_client")
    calls: list[str] = []
    monkeypatch.setattr(module, "probe_provider_health", lambda *_args, **_kwargs: calls.append("probe"))

    module.BindingLLMClient(_settings())

    assert calls == []


def test_binding_llm_client_is_unavailable_when_runtime_dependency_missing(monkeypatch) -> None:
    module_name = "full_stack_data_agent.app.binding_llm_client"
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)

    def _fake_probe(*_args, **_kwargs):
        from full_stack_data_agent.llm.models import ProviderHealth

        return ProviderHealth(
            provider="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            connected=True,
            available_models=[],
            api_key_present=True,
            fallback_provider="ollama",
        )

    monkeypatch.setattr(module, "probe_provider_health", _fake_probe)

    original_import = __import__

    def _raising_import(name, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[override]
        if name.startswith("langchain_core"):
            raise ImportError("missing langchain_core")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", _raising_import)
    client = module.BindingLLMClient(_settings())

    assert client.is_available() is False
    with pytest.raises(module.ProviderUnavailableError):
        client.complete_json(system_prompt="sys", user_prompt="user")
