from __future__ import annotations

import json
from dataclasses import dataclass
import time
from typing import Any

from full_stack_data_agent.config.provider_resolution import probe_provider_health, resolve_provider_config
from full_stack_data_agent.config.settings import Settings, get_settings
from full_stack_data_agent.llm.models import ProviderUnavailableError
from full_stack_data_agent.utils.json_extract import extract_first_json_object

_PROVIDER_HEALTH_TTL_SECONDS = 15.0
_CLIENT_CACHE: dict[Settings, "BindingLLMClient"] = {}


@dataclass(frozen=True)
class _ResolvedBindingProvider:
    provider: str
    model: str
    base_url: str
    api_key: str | None = None
    use_responses_api: bool = False
    ollama_pull_model: bool = False

class BindingLLMClient:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._enabled = bool(self._settings.binding_llm_enabled)
        self._resolved: _ResolvedBindingProvider | None = None
        self._resolved_checked_at = 0.0

    def _resolve_provider(self) -> _ResolvedBindingProvider | None:
        if not self._enabled:
            return None

        active_health = probe_provider_health(self._settings, self._settings.llm_provider)
        if active_health.connected:
            active_config = resolve_provider_config(self._settings, self._settings.llm_provider)
            return _ResolvedBindingProvider(
                provider=active_config.provider,
                model=active_config.model,
                base_url=active_config.base_url,
                api_key=active_config.api_key,
                use_responses_api=active_config.use_responses_api,
                ollama_pull_model=active_config.ollama_pull_model,
            )

        fallback_provider = self._settings.llm_fallback_provider
        fallback_health = probe_provider_health(self._settings, fallback_provider)
        if fallback_health.connected:
            fallback_config = resolve_provider_config(self._settings, fallback_provider)
            return _ResolvedBindingProvider(
                provider=fallback_config.provider,
                model=fallback_config.model,
                base_url=fallback_config.base_url,
                api_key=fallback_config.api_key,
                use_responses_api=fallback_config.use_responses_api,
                ollama_pull_model=fallback_config.ollama_pull_model,
            )
        return None

    def _get_resolved_provider(self) -> _ResolvedBindingProvider | None:
        now = time.monotonic()
        if self._resolved is not None and now - self._resolved_checked_at < _PROVIDER_HEALTH_TTL_SECONDS:
            return self._resolved
        if self._resolved is None and self._resolved_checked_at and now - self._resolved_checked_at < _PROVIDER_HEALTH_TTL_SECONDS:
            return None
        self._resolved = self._resolve_provider()
        self._resolved_checked_at = now
        return self._resolved

    def _runtime_dependencies_available(self) -> bool:
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from databao.agent.configs.llm import LLMConfig
            from databao.agent.executors.llm import call_model_with_retry
        except ImportError:
            return False
        return bool(HumanMessage and SystemMessage and LLMConfig and call_model_with_retry)

    def is_available(self) -> bool:
        return self._get_resolved_provider() is not None and self._runtime_dependencies_available()

    def _build_llm_config(self) -> Any:
        resolved = self._get_resolved_provider()
        if resolved is None:
            raise ProviderUnavailableError("Binding LLM unavailable")
        try:
            from databao.agent.configs.llm import LLMConfig
        except ImportError as exc:  # pragma: no cover - dependency-specific
            raise ProviderUnavailableError(f"Binding LLM dependencies unavailable: {exc}") from exc
        if resolved.provider == "ollama":
            name = f"ollama:{resolved.model}"
        else:
            name = resolved.model
        kwargs: dict[str, Any] = {}
        if resolved.api_key:
            kwargs["api_key"] = resolved.api_key
        return LLMConfig(
            name=name,
            api_base_url=resolved.base_url if resolved.provider != "ollama" else None,
            temperature=self._settings.llm_temperature,
            max_tokens=min(2048, self._settings.llm_num_ctx),
            timeout=self._settings.llm_timeout,
            use_responses_api=resolved.use_responses_api,
            ollama_pull_model=resolved.ollama_pull_model,
            model_kwargs=kwargs,
        )

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        if self._get_resolved_provider() is None:
            raise ProviderUnavailableError("Binding LLM unavailable")
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from databao.agent.executors.llm import call_model_with_retry
        except ImportError as exc:  # pragma: no cover - dependency-specific
            raise ProviderUnavailableError(f"Binding LLM dependencies unavailable: {exc}") from exc
        config = self._build_llm_config()
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        response = call_model_with_retry(config.new_chat_model(), messages)
        raw_text = getattr(response, "content", response)
        if isinstance(raw_text, list):
            raw_text = "\n".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in raw_text)
        if not isinstance(raw_text, str):
            raw_text = str(raw_text)
        parsed = extract_first_json_object(raw_text)
        if parsed is None:
            raise ProviderUnavailableError("Binding LLM did not return JSON")
        return parsed


def get_binding_llm_client(settings: Settings | None = None) -> BindingLLMClient:
    resolved_settings = settings or get_settings()
    client = _CLIENT_CACHE.get(resolved_settings)
    if client is None:
        client = BindingLLMClient(resolved_settings)
        _CLIENT_CACHE[resolved_settings] = client
    return client
