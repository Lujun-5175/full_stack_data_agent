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
    last_databao_result: DatabaoTurnResult | None
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

    def drop_session(self, conversation_id: str) -> None:
        self._runtime.drop_session(conversation_id)

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

        self._conversation_engine.add_user_message(state, user_input)
        try:
            last_result, runtime_snapshot = self._runtime.ask(
                state.conversation_id,
                user_input,
                uploaded_contexts=uploaded_contexts or [],
                prior_turns=state.turns[:-1],
            )
            metadata = {
                "provider": last_result.provider_used or provider_status.provider,
                "model": last_result.model_used or provider_status.model,
                "provider_used": last_result.provider_used,
                "model_used": last_result.model_used,
                "fallback_triggered": last_result.fallback_triggered,
                "fallback_reason": last_result.fallback_reason,
                "used_databao": True,
                "executor_type": runtime_snapshot.executor_type,
                "row_count": last_result.row_count,
                "columns": last_result.columns or [],
                "dataframe_preview": last_result.dataframe_preview,
                "plot_code": last_result.plot_code,
                "plot_spec": last_result.plot_spec,
                "plot_data": last_result.plot_data,
                "plot_meta": last_result.plot_meta,
                "plot_error": last_result.plot_error,
                "chart_debug": last_result.chart_debug,
                "completion_validation": last_result.completion_validation,
                "registered_tables": [table.to_dict() for table in runtime_snapshot.registered_tables],
                "normalization_reports": runtime_snapshot.normalization_reports,
            }
            self._conversation_engine.add_assistant_message(state, last_result.text, metadata=metadata)
            last_debug_detailed = {
                "provider_status": provider_status.to_dict(),
                "runtime_snapshot": runtime_snapshot.to_dict(),
                "thread_meta": last_result.thread_meta,
                "provider_used": last_result.provider_used,
                "model_used": last_result.model_used,
                "fallback_triggered": last_result.fallback_triggered,
                "fallback_reason": last_result.fallback_reason,
                "plot_meta": last_result.plot_meta,
                "plot_spec": last_result.plot_spec,
                "plot_data": last_result.plot_data,
                "plot_error": last_result.plot_error,
                "chart_debug": last_result.chart_debug,
                "completion_validation": last_result.completion_validation,
                "used_databao": last_result.used_databao,
                "thread_reset_reason": runtime_snapshot.thread_reset_reason,
                "datasource_changed": runtime_snapshot.datasource_changed,
                "normalization_reports": runtime_snapshot.normalization_reports,
            }
            state.turns[-1].debug_detailed = last_debug_detailed
        except Exception as exc:
            error = str(exc)
            self._conversation_engine.add_assistant_message(
                state,
                f"Request failed: {error}",
                metadata={
                    "provider": provider_status.provider,
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
            last_databao_result=last_result,
            last_runtime_snapshot=runtime_snapshot.to_dict() if runtime_snapshot else None,
            last_debug_detailed=last_debug_detailed,
            last_error=error,
        )
        return state, result
