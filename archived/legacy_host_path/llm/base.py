from __future__ import annotations

from abc import ABC, abstractmethod

from full_stack_data_agent.llm.models import LLMChatResult, MessagePayload, ModelInfo, ProviderHealth


class LLMProvider(ABC):
    @abstractmethod
    def health_check(self) -> ProviderHealth:
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        raise NotImplementedError

    @abstractmethod
    def assert_model_available(self, model_name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def chat(
        self,
        *,
        messages: list[MessagePayload],
        system_prompt: str | None = None,
        history: list[MessagePayload] | None = None,
    ) -> LLMChatResult:
        raise NotImplementedError
