from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Generator

from full_stack_data_agent.app.databao_runtime import DatabaoRuntime
from full_stack_data_agent.app.runtime_models import DatabaoSessionSnapshot, DatabaoTurnResult
from full_stack_data_agent.config.settings import Settings
from full_stack_data_agent.context.conversation_engine import ConversationEngine
from full_stack_data_agent.context.models import ConversationState, ConversationTurn, UploadedFileContext
from full_stack_data_agent.llm.models import ProviderHealth

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatServiceResult:
    messages: list[dict[str, Any]]
    provider_status: ProviderHealth
    conversation_snapshot: dict[str, Any]
    last_databao_result: DatabaoTurnResult | None
    last_runtime_snapshot: dict[str, Any] | None
    last_debug_detailed: dict[str, Any] | None
    last_error: str | None
    stream_mode: str = "one_shot"


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

    @staticmethod
    def _payload_to_dict(payload: Any | None) -> dict[str, Any]:
        if payload is None:
            return {}
        if isinstance(payload, dict):
            return payload
        to_dict = getattr(payload, "to_dict", None)
        if callable(to_dict):
            converted = to_dict()
            if isinstance(converted, dict):
                return converted
        return {}

    @classmethod
    def _project_metadata_payload(cls, last_result: DatabaoTurnResult) -> tuple[dict[str, Any], dict[str, Any]]:
        workspace = cls._payload_to_dict(last_result.result_workspace)
        grounded = cls._payload_to_dict(last_result.grounded_response)
        artifacts = {
            str(item.get("artifact_id")): item
            for item in workspace.get("artifacts", [])
            if isinstance(item, dict) and item.get("artifact_id")
        }
        table = artifacts.get(str(grounded.get("primary_table_artifact_id") or ""))
        chart = artifacts.get(str(grounded.get("primary_chart_artifact_id") or ""))
        render_payload = (chart or {}).get("render_payload") if isinstance((chart or {}).get("render_payload"), dict) else {}
        projected = {
            "dataframe_preview": (table or {}).get("dataframe_preview") or last_result.dataframe_preview,
            "plot_plan": render_payload.get("chart_plan") or (chart or {}).get("chart_plan") or last_result.plot_plan,
            "plot_spec": render_payload.get("chart_spec") or (chart or {}).get("chart_spec") or last_result.plot_spec,
            "plot_data": render_payload.get("chart_data") or (chart or {}).get("chart_data") or last_result.plot_data,
            "plot_meta": render_payload.get("chart_meta") or (chart or {}).get("chart_meta") or last_result.plot_meta,
            "plot_backend": render_payload.get("plot_backend") or ((chart or {}).get("metadata") or {}).get("plot_backend") or last_result.plot_backend,
            "plot_kind": render_payload.get("plot_kind") or ((chart or {}).get("metadata") or {}).get("plot_kind") or last_result.plot_kind,
            "plot_image_base64": render_payload.get("plot_image_base64") or ((chart or {}).get("metadata") or {}).get("plot_image_base64") or last_result.plot_image_base64,
            "plot_error": render_payload.get("plot_error") or ((chart or {}).get("metadata") or {}).get("plot_error") or last_result.plot_error,
        }
        return workspace, grounded | projected

    @staticmethod
    def _build_error_debug_payload(
        error: str,
        provider_status: ProviderHealth,
        uploaded_contexts: list[UploadedFileContext] | None,
    ) -> dict[str, Any]:
        return {
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
                for item in (uploaded_contexts or [])
            ],
        }

    @staticmethod
    def _build_turn_metadata(
        provider_status: ProviderHealth,
        last_result: DatabaoTurnResult,
        runtime_snapshot: DatabaoSessionSnapshot,
        *,
        stream_mode: str,
    ) -> dict[str, Any]:
        workspace, grounded_projection = ChatService._project_metadata_payload(last_result)
        return {
            "provider": last_result.provider_used or provider_status.provider,
            "model": last_result.model_used or provider_status.model,
            "provider_used": last_result.provider_used,
            "model_used": last_result.model_used,
            "stream_mode": stream_mode,
            "fallback_triggered": last_result.fallback_triggered,
            "fallback_reason": last_result.fallback_reason,
            "used_databao": True,
            "executor_type": runtime_snapshot.executor_type,
            "row_count": last_result.row_count,
            "columns": last_result.columns or [],
            "dataframe_preview": grounded_projection.get("dataframe_preview"),
            "plot_code": last_result.plot_code,
            "plot_plan": grounded_projection.get("plot_plan"),
            "plot_spec": grounded_projection.get("plot_spec"),
            "plot_data": grounded_projection.get("plot_data"),
            "plot_meta": grounded_projection.get("plot_meta"),
            "plot_backend": grounded_projection.get("plot_backend"),
            "plot_kind": grounded_projection.get("plot_kind"),
            "plot_image_base64": grounded_projection.get("plot_image_base64"),
            "plot_image_mime_type": last_result.plot_image_mime_type,
            "plot_error": grounded_projection.get("plot_error"),
            "chart_debug": last_result.chart_debug,
            "completion_validation": last_result.completion_validation,
            "result_workspace": workspace,
            "grounded_response": ChatService._payload_to_dict(last_result.grounded_response),
            "primary_artifact_id": last_result.primary_artifact_id,
            "primary_table_artifact_id": last_result.primary_table_artifact_id,
            "primary_chart_artifact_id": last_result.primary_chart_artifact_id,
            "followup_target_artifact_id": last_result.followup_target_artifact_id,
            "binding_intent": last_result.binding_intent or {},
            "binding_bundle": last_result.binding_bundle or {},
            "binding_decisions": last_result.binding_decisions or {},
            "decision_mode": last_result.decision_mode,
            "llm_used": last_result.llm_used,
            "turn_failure_state": last_result.turn_failure_state,
            "failure_reason": last_result.failure_reason,
            "business_result_present": last_result.business_result_present,
            "query_obligations": last_result.thread_meta.get("query_obligations"),
            "parsed_sql_summary": last_result.thread_meta.get("parsed_sql_summary"),
            "sql_guardrail_report": last_result.thread_meta.get("sql_guardrail_report"),
            "execution_validation_report": last_result.thread_meta.get("execution_validation_report"),
            "sql_retry_history": last_result.thread_meta.get("sql_retry_history"),
            "final_guardrail_status": last_result.thread_meta.get("final_guardrail_status"),
            "repair_attempts": last_result.thread_meta.get("repair_attempts"),
            "final_failure_reason": last_result.thread_meta.get("final_failure_reason"),
            "registered_tables": [table.to_dict() for table in runtime_snapshot.registered_tables],
            "normalization_reports": runtime_snapshot.normalization_reports,
            "status": "complete" if stream_mode == "one_shot" else "streaming",
        }

    @staticmethod
    def _build_debug_payload(
        provider_status: ProviderHealth,
        last_result: DatabaoTurnResult,
        runtime_snapshot: DatabaoSessionSnapshot,
        *,
        stream_mode: str,
    ) -> dict[str, Any]:
        return {
            "provider_status": provider_status.to_dict(),
            "runtime_snapshot": runtime_snapshot.to_dict(),
            "thread_meta": last_result.thread_meta,
            "provider_used": last_result.provider_used,
            "model_used": last_result.model_used,
            "stream_mode": stream_mode,
            "fallback_triggered": last_result.fallback_triggered,
            "fallback_reason": last_result.fallback_reason,
            "plot_meta": last_result.plot_meta,
            "plot_backend": last_result.plot_backend,
            "plot_kind": last_result.plot_kind,
            "plot_image_base64": last_result.plot_image_base64,
            "plot_image_mime_type": last_result.plot_image_mime_type,
            "plot_plan": last_result.plot_plan,
            "plot_spec": last_result.plot_spec,
            "plot_data": last_result.plot_data,
            "plot_error": last_result.plot_error,
            "chart_debug": last_result.chart_debug,
            "completion_validation": last_result.completion_validation,
            "binding_intent": last_result.binding_intent,
            "binding_bundle": last_result.binding_bundle,
            "binding_decisions": last_result.binding_decisions,
            "decision_mode": last_result.decision_mode,
            "llm_used": last_result.llm_used,
            "turn_failure_state": last_result.turn_failure_state,
            "failure_reason": last_result.failure_reason,
            "business_result_present": last_result.business_result_present,
            "query_obligations": last_result.thread_meta.get("query_obligations"),
            "parsed_sql_summary": last_result.thread_meta.get("parsed_sql_summary"),
            "sql_guardrail_report": last_result.thread_meta.get("sql_guardrail_report"),
            "execution_validation_report": last_result.thread_meta.get("execution_validation_report"),
            "sql_retry_history": last_result.thread_meta.get("sql_retry_history"),
            "final_guardrail_status": last_result.thread_meta.get("final_guardrail_status"),
            "repair_attempts": last_result.thread_meta.get("repair_attempts"),
            "final_failure_reason": last_result.thread_meta.get("final_failure_reason"),
            "used_databao": last_result.used_databao,
            "thread_reset_reason": runtime_snapshot.thread_reset_reason,
            "datasource_changed": runtime_snapshot.datasource_changed,
            "normalization_reports": runtime_snapshot.normalization_reports,
        }

    def _build_messages_snapshot(self, state: ConversationState) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for turn in state.turns:
            messages.append({"role": "user", "content": turn.user_message.content})
            if turn.assistant_message:
                messages.append(
                    {
                        "role": "assistant",
                        "content": turn.assistant_message.content,
                        "status": turn.assistant_message.status,
                    }
                )
        return messages

    def _runtime_stream_events(
        self,
        conversation_id: str,
        user_input: str,
        *,
        uploaded_contexts: list[UploadedFileContext] | None = None,
        prior_turns: list[ConversationTurn] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        ask_stream = getattr(self._runtime, "ask_stream", None)
        if callable(ask_stream):
            yield from ask_stream(
                conversation_id,
                user_input,
                uploaded_contexts=uploaded_contexts or [],
                prior_turns=prior_turns or [],
            )
            return

        last_result, runtime_snapshot = self._runtime.ask(
            conversation_id,
            user_input,
            uploaded_contexts=uploaded_contexts or [],
            prior_turns=prior_turns or [],
        )
        yield {"type": "final", "result": last_result, "snapshot": runtime_snapshot}

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
            metadata = self._build_turn_metadata(provider_status, last_result, runtime_snapshot, stream_mode="one_shot")
            self._conversation_engine.add_assistant_message(state, last_result.text, metadata=metadata)
            last_debug_detailed = self._build_debug_payload(
                provider_status,
                last_result,
                runtime_snapshot,
                stream_mode="one_shot",
            )
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
                    "status": "error",
                },
            )
            last_debug_detailed = self._build_error_debug_payload(error, provider_status, uploaded_contexts)
            state.turns[-1].debug_detailed = last_debug_detailed

        result = ChatServiceResult(
            messages=self._build_messages_snapshot(state),
            provider_status=provider_status,
            conversation_snapshot=state.to_dict(),
            last_databao_result=last_result,
            last_runtime_snapshot=runtime_snapshot.to_dict() if runtime_snapshot else None,
            last_debug_detailed=last_debug_detailed,
            last_error=error,
            stream_mode="one_shot",
        )
        return state, result

    def send_message_stream(
        self,
        state: ConversationState,
        user_input: str,
        *,
        uploaded_contexts: list[UploadedFileContext] | None = None,
    ) -> Generator[str, None, ChatServiceResult]:
        provider_status = self.provider_status()
        last_result: DatabaoTurnResult | None = None
        runtime_snapshot: DatabaoSessionSnapshot | None = None
        last_debug_detailed: dict[str, Any] | None = None
        error: str | None = None

        if uploaded_contexts is not None:
            self.set_uploaded_contexts(state, uploaded_contexts)

        self._conversation_engine.add_user_message(state, user_input)
        self._conversation_engine.begin_assistant_message(
            state,
            content="",
            metadata={
                "provider": provider_status.provider,
                "model": provider_status.model,
                "used_databao": True,
                "stream_mode": "streaming",
                "status": "streaming",
            },
        )
        active_turn_id = state.turns[-1].turn_id
        state.debug_state["_streaming_mode"] = "streaming"
        state.debug_state["_stream_result_mode"] = "streaming"
        state.debug_state["_active_stream_turn_id"] = active_turn_id

        events = self._runtime_stream_events(
            state.conversation_id,
            user_input,
            uploaded_contexts=uploaded_contexts or [],
            prior_turns=state.turns[:-1],
        )

        def _runner() -> Generator[str, None, ChatServiceResult]:
            nonlocal last_result, runtime_snapshot, last_debug_detailed, error
            buffer = ""
            try:
                for event in events:
                    event_type = str(event.get("type") or "")
                    if event_type == "chunk":
                        chunk = str(event.get("text") or "")
                        if not chunk:
                            continue
                        buffer = f"{buffer}{chunk}"
                        state.debug_state["_active_stream_buffer"] = buffer
                        state.debug_state["_active_stream_status"] = "streaming"
                        self._conversation_engine.update_assistant_message(
                            state,
                            buffer,
                            metadata=state.turns[-1].metadata,
                        )
                        yield chunk
                        continue

                    if event_type == "final":
                        last_result = event.get("result")
                        runtime_snapshot = event.get("snapshot")
                        break

                    if event_type == "error":
                        error = str(event.get("error") or "Streaming failed")
                        break

                if error is None and last_result is not None and runtime_snapshot is not None:
                    metadata = self._build_turn_metadata(provider_status, last_result, runtime_snapshot, stream_mode="streaming")
                    metadata["status"] = "streaming"
                    self._conversation_engine.update_assistant_message(state, buffer, metadata=metadata)

                    assistant_text = last_result.text
                    metadata["status"] = "complete"
                    self._conversation_engine.finalize_assistant_message(
                        state,
                        assistant_text,
                        metadata=metadata,
                        status="complete",
                    )
                    last_debug_detailed = self._build_debug_payload(
                        provider_status,
                        last_result,
                        runtime_snapshot,
                        stream_mode="streaming",
                    )
                    state.turns[-1].debug_detailed = last_debug_detailed
                elif error is not None:
                    self._conversation_engine.finalize_assistant_message(
                        state,
                        f"Request failed: {error}",
                        metadata={
                            "provider": provider_status.provider,
                            "model": provider_status.model,
                            "used_databao": True,
                            "error": error,
                            "stream_mode": "streaming",
                            "status": "error",
                        },
                        status="error",
                    )
                    last_debug_detailed = self._build_error_debug_payload(error, provider_status, uploaded_contexts)
                    state.turns[-1].debug_detailed = last_debug_detailed
                else:
                    error = "Streaming finished without a final result"
                    self._conversation_engine.finalize_assistant_message(
                        state,
                        f"Request failed: {error}",
                        metadata={
                            "provider": provider_status.provider,
                            "model": provider_status.model,
                            "used_databao": True,
                            "error": error,
                            "stream_mode": "streaming",
                            "status": "error",
                        },
                        status="error",
                    )
                    last_debug_detailed = self._build_error_debug_payload(error, provider_status, uploaded_contexts)
                    state.turns[-1].debug_detailed = last_debug_detailed
            except Exception as exc:
                error = str(exc)
                self._conversation_engine.finalize_assistant_message(
                    state,
                    f"Request failed: {error}",
                    metadata={
                        "provider": provider_status.provider,
                        "model": provider_status.model,
                        "used_databao": True,
                        "error": error,
                        "stream_mode": "streaming",
                        "status": "error",
                    },
                    status="error",
                )
                last_debug_detailed = self._build_error_debug_payload(error, provider_status, uploaded_contexts)
                state.turns[-1].debug_detailed = last_debug_detailed
            finally:
                state.debug_state["_active_stream_status"] = "complete" if error is None else "error"

            result = ChatServiceResult(
                messages=self._build_messages_snapshot(state),
                provider_status=provider_status,
                conversation_snapshot=state.to_dict(),
                last_databao_result=last_result,
                last_runtime_snapshot=runtime_snapshot.to_dict() if runtime_snapshot else None,
                last_debug_detailed=last_debug_detailed,
                last_error=error,
                stream_mode="streaming",
            )
            state.debug_state["_last_chat_service_result"] = result
            return result

        return _runner()
