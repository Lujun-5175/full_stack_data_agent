from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from full_stack_data_agent.context.models import ContextPacket
from full_stack_data_agent.llm.models import LLMChatResult, MessagePayload


@dataclass(frozen=True)
class AgentRequest:
    user_input: str
    context_packet: ContextPacket
    provider: str
    model: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_input": self.user_input,
            "context_packet": self.context_packet.to_dict(),
            "provider": self.provider,
            "model": self.model,
        }


@dataclass(frozen=True)
class DebugTrace:
    system_prompt_preview: str
    message_trace: list[dict[str, str]]
    retrieval_mode: str
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AgentResponse:
    text: str
    provider: str
    model: str
    conversation_id: str
    turn_count: int
    context_summary: str
    retrieved_context_summary: str
    debug_trace: DebugTrace
    llm_result: LLMChatResult
    raw_message_trace: list[MessagePayload]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "provider": self.provider,
            "model": self.model,
            "conversation_id": self.conversation_id,
            "turn_count": self.turn_count,
            "context_summary": self.context_summary,
            "retrieved_context_summary": self.retrieved_context_summary,
            "debug_trace": asdict(self.debug_trace),
            "llm_result": self.llm_result.to_dict(),
            "raw_message_trace": [asdict(message) for message in self.raw_message_trace],
            "error": self.error,
        }
