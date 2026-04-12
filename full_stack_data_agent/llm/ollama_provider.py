from __future__ import annotations

"""Legacy direct HTTP provider kept for fallback/reference.

The default analysis path is now DatabaoRuntime via Databao LLMConfig.
"""

import time
from typing import Any

import requests

from full_stack_data_agent.config.settings import Settings
from full_stack_data_agent.llm.base import LLMProvider
from full_stack_data_agent.llm.models import (
    EmptyResponseError,
    LLMChatResult,
    MessagePayload,
    ModelInfo,
    ModelNotFoundError,
    ProviderHealth,
    ProviderProtocolError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class OllamaProvider(LLMProvider):
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self._settings = settings
        self._base = settings.ollama_base_url.rstrip("/")
        self._session = session or requests.Session()

    def health_check(self) -> ProviderHealth:
        try:
            models = self.list_models()
            return ProviderHealth(
                provider=self._settings.provider_name,
                base_url=self._base,
                model=self._settings.ollama_model,
                connected=True,
                available_models=[model.name for model in models],
            )
        except Exception as exc:
            return ProviderHealth(
                provider=self._settings.provider_name,
                base_url=self._base,
                model=self._settings.ollama_model,
                connected=False,
                available_models=[],
                error=str(exc),
            )

    def list_models(self) -> list[ModelInfo]:
        payload = self._request_json("GET", "/api/tags")
        models: list[ModelInfo] = []
        for item in payload.get("models", []):
            if not isinstance(item, dict):
                continue
            details = item.get("details") or {}
            models.append(
                ModelInfo(
                    name=str(item.get("name", "")),
                    family=details.get("family") if isinstance(details, dict) else None,
                    size=int(item["size"]) if isinstance(item.get("size"), int) else None,
                    modified_at=item.get("modified_at"),
                )
            )
        return models

    def assert_model_available(self, model_name: str) -> None:
        models = self.list_models()
        if model_name not in {model.name for model in models}:
            raise ModelNotFoundError(
                f"Model '{model_name}' was not found in Ollama. Run `ollama pull {model_name}` first."
            )

    def chat(
        self,
        *,
        messages: list[MessagePayload],
        system_prompt: str | None = None,
        history: list[MessagePayload] | None = None,
    ) -> LLMChatResult:
        started = time.perf_counter()
        self.assert_model_available(self._settings.ollama_model)

        final_messages: list[MessagePayload] = []
        if system_prompt:
            final_messages.append(MessagePayload(role="system", content=system_prompt))
        if history:
            final_messages.extend(history)
        final_messages.extend(messages)

        payload: dict[str, Any] = {
            "model": self._settings.ollama_model,
            "messages": [message.to_ollama() for message in final_messages],
            "stream": False,
            "options": {
                "temperature": self._settings.ollama_temperature,
                "num_ctx": self._settings.ollama_num_ctx,
            },
        }
        data = self._request_json("POST", "/api/chat", json=payload)
        message = data.get("message")
        if not isinstance(message, dict):
            raise ProviderProtocolError("Unexpected Ollama chat response: missing message object.")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise EmptyResponseError("Ollama returned an empty assistant message.")
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return LLMChatResult(
            provider=self._settings.provider_name,
            model=self._settings.ollama_model,
            source="local_ollama",
            content=content.strip(),
            latency_ms=elapsed_ms,
            raw_message_count=len(final_messages),
            done_reason=data.get("done_reason"),
            raw=data,
        )

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._request(method, path, **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderProtocolError(f"Ollama returned invalid JSON for {path}.") from exc
        if not isinstance(payload, dict):
            raise ProviderProtocolError(f"Ollama returned unexpected JSON type for {path}.")
        return payload

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self._base}{path}"
        try:
            response = self._session.request(method, url, timeout=self._settings.ollama_timeout, **kwargs)
        except requests.Timeout as exc:
            raise ProviderTimeoutError(
                f"Ollama request to {url} timed out after {self._settings.ollama_timeout} seconds."
            ) from exc
        except requests.RequestException as exc:
            raise ProviderUnavailableError(
                f"Ollama is unavailable at {self._base}. Start it with `ollama serve`."
            ) from exc
        if response.status_code >= 400:
            raise ProviderProtocolError(f"Ollama returned {response.status_code}: {response.text}")
        return response
