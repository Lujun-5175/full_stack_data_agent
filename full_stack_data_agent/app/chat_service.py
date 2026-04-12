from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from full_stack_data_agent.agent.chat_agent import ChatAgent
from full_stack_data_agent.agent.models import AgentRequest, AgentResponse
from full_stack_data_agent.config.settings import Settings
from full_stack_data_agent.context.context_packet_builder import ContextPacketBuilder
from full_stack_data_agent.context.conversation_engine import ConversationEngine
from full_stack_data_agent.context.models import ContextPacket, ConversationState
from full_stack_data_agent.llm.models import ProviderHealth
from full_stack_data_agent.llm.ollama_provider import OllamaProvider


@dataclass(frozen=True)
class ChatServiceResult:
    messages: list[dict[str, Any]]
    provider_status: ProviderHealth
    conversation_snapshot: dict[str, Any]
    last_context_packet: dict[str, Any] | None
    last_agent_request: dict[str, Any] | None
    last_agent_response: dict[str, Any] | None
    last_error: str | None


class ChatService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._provider = OllamaProvider(settings)
        self._conversation_engine = ConversationEngine(turn_window=settings.context_turn_window)
        self._packet_builder = ContextPacketBuilder(settings, self._conversation_engine)
        self._agent = ChatAgent(settings, self._provider)

    def create_state(self) -> ConversationState:
        return self._conversation_engine.create_state()

    def provider_status(self) -> ProviderHealth:
        return self._provider.health_check()

    def send_message(self, state: ConversationState, user_input: str) -> tuple[ConversationState, ChatServiceResult]:
        provider_status = self.provider_status()
        last_context_packet: ContextPacket | None = None
        last_request: AgentRequest | None = None
        last_response: AgentResponse | None = None
        error: str | None = None

        self._conversation_engine.add_user_message(state, user_input)
        try:
            last_context_packet = self._packet_builder.build_packet(state, user_input)
            last_request = AgentRequest(
                user_input=user_input,
                context_packet=last_context_packet,
                provider=self._settings.provider_name,
                model=self._settings.ollama_model,
            )
            last_response = self._agent.respond(last_request)
            self._conversation_engine.add_assistant_message(
                state,
                last_response.text,
                metadata={"provider": last_response.provider, "model": last_response.model},
            )
        except Exception as exc:
            error = str(exc)
            self._conversation_engine.add_assistant_message(
                state,
                f"Request failed: {error}",
                metadata={"error": error},
            )

        messages = []
        for turn in state.turns:
            messages.append({"role": "user", "content": turn.user_message.content})
            if turn.assistant_message:
                messages.append({"role": "assistant", "content": turn.assistant_message.content})

        result = ChatServiceResult(
            messages=messages,
            provider_status=provider_status,
            conversation_snapshot=state.to_dict(),
            last_context_packet=last_context_packet.to_dict() if last_context_packet else None,
            last_agent_request=last_request.to_dict() if last_request else None,
            last_agent_response=last_response.to_dict() if last_response else None,
            last_error=error,
        )
        return state, result
