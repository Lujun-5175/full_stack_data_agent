from __future__ import annotations

from databao.agent.configs.llm import LLMConfig

from full_stack_data_agent.agent.models import AgentRequest, AgentResponse, DebugTrace
from full_stack_data_agent.config.settings import Settings
from full_stack_data_agent.llm.base import LLMProvider
from full_stack_data_agent.llm.models import MessagePayload


class ChatAgent:
    def __init__(self, settings: Settings, provider: LLMProvider):
        self._settings = settings
        self._provider = provider
        self._llm_config = LLMConfig(
            name=f"ollama:{settings.ollama_model}",
            temperature=settings.ollama_temperature,
            timeout=int(settings.ollama_timeout),
            use_responses_api=False,
            ollama_pull_model=False,
            model_kwargs={"num_ctx": settings.ollama_num_ctx},
        )

    @property
    def llm_config(self) -> LLMConfig:
        return self._llm_config

    def respond(self, request: AgentRequest) -> AgentResponse:
        system_prompt = self._build_system_prompt(request)
        message_trace = [
            MessagePayload(role=message.role, content=message.content)
            for message in request.context_packet.recent_messages
            if message.role in {"user", "assistant"}
        ]
        llm_result = self._provider.chat(
            messages=[MessagePayload(role="user", content=request.user_input)],
            system_prompt=system_prompt,
            history=message_trace,
        )

        retrieved_summary = "\n\n".join(
            f"[{item.source}] {item.title}\n{item.content[:400]}"
            for item in request.context_packet.retrieved_contexts
        )
        debug_trace = DebugTrace(
            system_prompt_preview=system_prompt[:1200],
            message_trace=[message.to_ollama() for message in [*message_trace, MessagePayload(role='user', content=request.user_input)]],
            retrieval_mode=request.context_packet.debug.retrieval_mode,
            notes=[
                f"Databao domain wired to: {self._settings.domain_dir}",
                f"Databao llm config: {self._llm_config.name}",
                f"Provider source: {llm_result.source}",
                "Databao host integration uses the copied Databao package with a host-side chat facade.",
            ],
        )
        return AgentResponse(
            text=llm_result.content,
            provider=llm_result.provider,
            model=llm_result.model,
            conversation_id=request.context_packet.conversation_id,
            turn_count=request.context_packet.turn_count,
            context_summary=request.context_packet.context_summary,
            retrieved_context_summary=retrieved_summary or "No retrieved context snippets.",
            debug_trace=debug_trace,
            llm_result=llm_result,
            raw_message_trace=[*message_trace, MessagePayload(role="user", content=request.user_input)],
        )

    def _build_system_prompt(self, request: AgentRequest) -> str:
        retrieved_context = "\n\n".join(
            f"Context Source: {item.title}\nSource Kind: {item.source}\nContent:\n{item.content[:1000]}"
            for item in request.context_packet.retrieved_contexts
        )
        return "\n".join(
            [
                "You are a local Databao-style assistant running entirely on the user's machine.",
                "Be explicit that your responses come from the local Ollama provider when asked about model origin.",
                "Use the recent conversation memory first for follow-up questions.",
                "Use the retrieved project domain context next when it helps answer accurately.",
                "If the answer depends on missing facts, say what is missing instead of fabricating.",
                "",
                f"Provider: {request.provider}",
                f"Model: {request.model}",
                f"Conversation ID: {request.context_packet.conversation_id}",
                "",
                "Conversation Summary:",
                request.context_packet.context_summary,
                "",
                "Retrieved Context:",
                retrieved_context or "No retrieved context available.",
            ]
        )
