from __future__ import annotations

from dataclasses import asdict, dataclass, field
from time import time
from typing import Any, Literal


class ProviderError(RuntimeError):
    pass


class ProviderUnavailableError(ProviderError):
    pass


class ModelNotFoundError(ProviderError):
    pass


class ProviderTimeoutError(ProviderError):
    pass


class EmptyResponseError(ProviderError):
    pass


class ProviderProtocolError(ProviderError):
    pass


@dataclass(frozen=True)
class ModelInfo:
    name: str
    family: str | None = None
    size: int | None = None
    modified_at: str | None = None


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    base_url: str
    model: str
    connected: bool
    available_models: list[str] = field(default_factory=list)
    error: str | None = None
    checked_at: float = field(default_factory=time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MessagePayload:
    role: Literal["system", "user", "assistant"]
    content: str

    def to_ollama(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class LLMChatResult:
    provider: str
    model: str
    source: str
    content: str
    latency_ms: int
    raw_message_count: int
    done_reason: str | None = None
    error: str | None = None
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
