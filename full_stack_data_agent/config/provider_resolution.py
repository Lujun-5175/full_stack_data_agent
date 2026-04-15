from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

import requests

from full_stack_data_agent.config.settings import Settings
from full_stack_data_agent.llm.models import ProviderHealth

_SUPPORTED_PROVIDERS = {"deepseek", "ollama"}


def normalize_provider_name(value: str | None, default: str = "deepseek") -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    if raw.startswith("ollama"):
        return "ollama"
    if raw.startswith("deepseek"):
        return "deepseek"
    if raw in _SUPPORTED_PROVIDERS:
        return raw
    return default


@dataclass(frozen=True)
class ResolvedProviderConfig:
    provider: str
    model: str
    base_url: str
    api_key_present: bool
    fallback_provider: str
    temperature: float
    timeout: float
    num_ctx: int
    api_key: str | None = None
    use_responses_api: bool = False
    ollama_pull_model: bool = False

    def public_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_present": self.api_key_present,
            "fallback_provider": self.fallback_provider,
        }


def resolve_provider_config(settings: Settings, provider_name: str | None = None) -> ResolvedProviderConfig:
    provider = normalize_provider_name(provider_name or settings.llm_provider)
    fallback_provider = normalize_provider_name(settings.llm_fallback_provider, default="ollama")

    if provider == "deepseek":
        api_key = settings.deepseek_api_key.strip() or None
        return ResolvedProviderConfig(
            provider="deepseek",
            model=settings.deepseek_model,
            base_url=settings.deepseek_base_url.rstrip("/"),
            api_key_present=bool(api_key),
            fallback_provider=fallback_provider,
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout,
            num_ctx=settings.llm_num_ctx,
            api_key=api_key,
        )

    return ResolvedProviderConfig(
        provider="ollama",
        model=settings.ollama_model,
        base_url=settings.ollama_base_url.rstrip("/"),
        api_key_present=False,
        fallback_provider=fallback_provider,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout,
        num_ctx=settings.llm_num_ctx,
    )


def provider_resolution_summary(settings: Settings, provider_name: str | None = None) -> dict[str, Any]:
    return resolve_provider_config(settings, provider_name=provider_name).public_dict()


def probe_provider_health(settings: Settings, provider_name: str | None = None) -> ProviderHealth:
    resolved = resolve_provider_config(settings, provider_name=provider_name)
    if resolved.provider == "deepseek":
        return _probe_deepseek_health(resolved)
    return _probe_ollama_health(resolved)


def _probe_deepseek_health(resolved: ResolvedProviderConfig) -> ProviderHealth:
    if not resolved.api_key_present:
        return ProviderHealth(
            provider=resolved.provider,
            base_url=resolved.base_url,
            model=resolved.model,
            connected=False,
            available_models=[],
            error="DEEPSEEK_API_KEY is missing.",
            api_key_present=False,
            fallback_provider=resolved.fallback_provider,
            failure_category="auth",
        )

    headers = {"Authorization": f"Bearer {resolved.api_key}"}
    models_url = f"{resolved.base_url}/v1/models"
    try:
        started = time.perf_counter()
        response = requests.get(
            models_url,
            headers=headers,
            timeout=min(float(resolved.timeout), 5.0),
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code == 200:
            error = None
            connected = True
        elif response.status_code in (401, 403):
            error = "DeepSeek authentication failed."
            connected = False
        elif response.status_code == 429:
            error = "DeepSeek rate limited the request."
            connected = True
        elif response.status_code >= 500:
            error = f"DeepSeek service returned {response.status_code}."
            connected = False
        else:
            error = f"DeepSeek service returned unexpected status {response.status_code}."
            connected = False
        return ProviderHealth(
            provider=resolved.provider,
            base_url=resolved.base_url,
            model=resolved.model,
            connected=connected,
            available_models=[],
            error=error,
            api_key_present=True,
            fallback_provider=resolved.fallback_provider,
            failure_category=(
                None if response.status_code == 200 else
                "auth" if response.status_code in (401, 403) else
                "rate_limit" if response.status_code == 429 else
                "transport" if response.status_code >= 500 else
                "unavailable"
            ),
            latency_ms=latency_ms,
        )
    except requests.Timeout as exc:
        return ProviderHealth(
            provider=resolved.provider,
            base_url=resolved.base_url,
            model=resolved.model,
            connected=False,
            available_models=[],
            error=f"DeepSeek request timed out: {exc}",
            api_key_present=True,
            fallback_provider=resolved.fallback_provider,
            failure_category="timeout",
        )
    except requests.RequestException as exc:
        return ProviderHealth(
            provider=resolved.provider,
            base_url=resolved.base_url,
            model=resolved.model,
            connected=False,
            available_models=[],
            error=f"DeepSeek is unavailable: {exc}",
            api_key_present=True,
            fallback_provider=resolved.fallback_provider,
            failure_category="transport",
        )


def _probe_ollama_health(resolved: ResolvedProviderConfig) -> ProviderHealth:
    try:
        started = time.perf_counter()
        response = requests.get(f"{resolved.base_url}/api/tags", timeout=min(float(resolved.timeout), 10.0))
        latency_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        models = response.json().get("models", [])
        available_models = [str(item.get("name")) for item in models if isinstance(item, dict) and item.get("name")]
        connected = resolved.model in available_models
        error = None if connected else f"Model {resolved.model} is not available in local Ollama."
        return ProviderHealth(
            provider=resolved.provider,
            base_url=resolved.base_url,
            model=resolved.model,
            connected=connected,
            available_models=available_models,
            error=error,
            api_key_present=False,
            fallback_provider=resolved.fallback_provider,
            failure_category=None if connected else "model_missing",
            latency_ms=latency_ms,
        )
    except requests.Timeout as exc:
        return ProviderHealth(
            provider=resolved.provider,
            base_url=resolved.base_url,
            model=resolved.model,
            connected=False,
            available_models=[],
            error=f"Ollama request timed out: {exc}",
            api_key_present=False,
            fallback_provider=resolved.fallback_provider,
            failure_category="timeout",
        )
    except requests.RequestException as exc:
        return ProviderHealth(
            provider=resolved.provider,
            base_url=resolved.base_url,
            model=resolved.model,
            connected=False,
            available_models=[],
            error=f"Ollama is unavailable: {exc}",
            api_key_present=False,
            fallback_provider=resolved.fallback_provider,
            failure_category="transport",
        )
