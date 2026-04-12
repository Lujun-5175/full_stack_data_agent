from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from full_stack_data_agent.app.databao_runtime import DatabaoRuntime
from full_stack_data_agent.app.runtime_models import DatabaoSessionSnapshot, DatabaoTurnResult
from full_stack_data_agent.config.settings import Settings
from full_stack_data_agent.context.conversation_engine import ConversationEngine
from full_stack_data_agent.context.models import ConversationState, UploadedFileContext
from full_stack_data_agent.llm.models import ProviderHealth


@dataclass(frozen=True)
class ChatServiceResult:
    messages: list[dict[str, Any]]
    provider_status: ProviderHealth
    conversation_snapshot: dict[str, Any]
    last_databao_result: dict[str, Any] | None
    last_runtime_snapshot: dict[str, Any] | None
    last_debug_detailed: dict[str, Any] | None
    last_error: str | None


class ChatService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._runtime = DatabaoRuntime(settings)
        self._conversation_engine = ConversationEngine(turn_window=settings.context_turn_window)

    def create_state(self) -> ConversationState:
        return self._conversation_engine.create_state()

    def provider_status(self) -> ProviderHealth:
        return self._runtime.provider_status()

    def set_uploaded_contexts(self, state: ConversationState, uploaded_contexts: list[UploadedFileContext]) -> None:
        state.debug_state["uploaded_contexts"] = uploaded_contexts

    def send_message(
        self,
        state: ConversationState,
        user_input: str,
        *,
        uploaded_contexts: list[UploadedFileContext] | None = None,
    ) -> tuple[ConversationState, ChatServiceResult]:
        provider_status = self.provider_status()
        last_result: DatabaoTurnResult | None = None
        runtime_snapshot: DatabaoSessionSnapshot | None = None
        last_debug_detailed: dict[str, Any] | None = None
        error: str | None = None

        if uploaded_contexts is not None:
            self.set_uploaded_contexts(state, uploaded_contexts)

        history_queries = [turn.user_message.content for turn in state.turns]
        self._conversation_engine.add_user_message(state, user_input)
        try:
            last_result, runtime_snapshot = self._runtime.ask(
                state.conversation_id,
                user_input,
                uploaded_contexts=uploaded_contexts or [],
                history_queries=history_queries,
            )
            metadata = {
                "provider": self._settings.provider_name,
                "model": provider_status.model,
                "used_databao": True,
                "executor_type": runtime_snapshot.executor_type,
                "row_count": last_result.row_count,
                "columns": last_result.columns or [],
                "dataframe_preview": last_result.dataframe_preview,
                "plot_code": last_result.plot_code,
                "plot_meta": last_result.plot_meta,
                "registered_tables": [table.to_dict() for table in runtime_snapshot.registered_tables],
            }
            self._conversation_engine.add_assistant_message(state, last_result.text, metadata=metadata)
            last_debug_detailed = {
                "runtime_snapshot": runtime_snapshot.to_dict(),
                "thread_meta": last_result.thread_meta,
                "plot_meta": last_result.plot_meta,
                "used_databao": last_result.used_databao,
            }
            state.turns[-1].debug_detailed = last_debug_detailed
        except Exception as exc:
            error = str(exc)
            self._conversation_engine.add_assistant_message(
                state,
                f"Request failed: {error}",
                metadata={
                    "provider": self._settings.provider_name,
                    "model": provider_status.model,
                    "used_databao": True,
                    "error": error,
                },
            )
            last_debug_detailed = {
                "error": error,
                "provider_status": provider_status.to_dict(),
                "uploaded_contexts": [
                    {
                        "file_name": item.file_name,
                        "table_name": item.table_name,
                        "is_tabular": item.is_tabular,
                        "row_count": item.row_count,
                        "columns": item.columns,
                    }
                    for item in uploaded_contexts or []
                ],
            }
            state.turns[-1].debug_detailed = last_debug_detailed

        messages: list[dict[str, Any]] = []
        for turn in state.turns:
            messages.append({"role": "user", "content": turn.user_message.content})
            if turn.assistant_message:
                messages.append({"role": "assistant", "content": turn.assistant_message.content})

        result = ChatServiceResult(
            messages=messages,
            provider_status=provider_status,
            conversation_snapshot=state.to_dict(),
            last_databao_result=last_result.to_dict() if last_result else None,
            last_runtime_snapshot=runtime_snapshot.to_dict() if runtime_snapshot else None,
            last_debug_detailed=last_debug_detailed,
            last_error=error,
        )
        return state, result
