from full_stack_data_agent.app.chat_service import ChatService
from full_stack_data_agent.app.response_grounding import GroundedResponse
from full_stack_data_agent.app.result_artifacts import ResultArtifact
from full_stack_data_agent.app.result_workspace import ResultWorkspace
from full_stack_data_agent.app.runtime_models import DatabaoSessionSnapshot, DatabaoTurnResult
from full_stack_data_agent.config.settings import get_settings
from full_stack_data_agent.context.upload_processor import process_uploaded_file
from full_stack_data_agent.llm.models import ProviderHealth
import json


class FakeRuntime:
    def __init__(self) -> None:
        self.ask_calls = []

    def provider_status(self) -> ProviderHealth:
        return ProviderHealth(
            provider="ollama",
            base_url="http://127.0.0.1:11434",
            model="gemma4:e4b",
            connected=True,
            available_models=["gemma4:e4b"],
        )

    def ask(self, conversation_id: str, query: str, *, uploaded_contexts, prior_turns=None):
        self.ask_calls.append((conversation_id, query))
        workspace = _build_workspace(conversation_id)
        return (
            DatabaoTurnResult(
                text="Databao handled this query. It also explains the result.\nA final note appears here.",
                dataframe_preview=[{"a": 1}],
                columns=["a"],
                row_count=1,
                thread_meta={"source": "databao"},
                result_workspace=workspace,
                grounded_response=_build_grounded_response(),
                primary_artifact_id="filtered_df:turn-1",
                primary_table_artifact_id="filtered_df:turn-1",
                primary_chart_artifact_id="chart:turn-1",
                followup_target_artifact_id="filtered_df:turn-1",
            ),
            DatabaoSessionSnapshot(
                conversation_id=conversation_id,
                llm_name="ollama:gemma4:e4b",
                executor_type="lighthouse",
            ),
        )

    def ask_stream(self, conversation_id: str, query: str, *, uploaded_contexts, prior_turns=None):
        self.ask_calls.append((conversation_id, query, "stream"))
        workspace = _build_workspace(conversation_id)
        yield {"type": "chunk", "text": "Databao handled this query. "}
        yield {"type": "chunk", "text": "It also explains the result.\n"}
        yield {"type": "chunk", "text": "A final note appears here."}
        yield {
            "type": "final",
            "result": DatabaoTurnResult(
                text="Databao handled this query. It also explains the result.\nA final note appears here.",
                dataframe_preview=[{"a": 1}],
                columns=["a"],
                row_count=1,
                thread_meta={"source": "databao"},
                result_workspace=workspace,
                grounded_response=_build_grounded_response(),
                primary_artifact_id="filtered_df:turn-1",
                primary_table_artifact_id="filtered_df:turn-1",
                primary_chart_artifact_id="chart:turn-1",
                followup_target_artifact_id="filtered_df:turn-1",
            ),
            "snapshot": DatabaoSessionSnapshot(
                conversation_id=conversation_id,
                llm_name="ollama:gemma4:e4b",
                executor_type="lighthouse",
            ),
        }


def _build_workspace(conversation_id: str) -> ResultWorkspace:
    workspace = ResultWorkspace(conversation_id=conversation_id, turn_id="turn-1")
    workspace.register_artifact(
        ResultArtifact(
            artifact_id="filtered_df:turn-1",
            artifact_type="filtered_df",
            dataframe_preview=[{"a": 1}],
            metadata={"columns": ["a"], "row_count": 1},
        )
    )
    workspace.register_artifact(
        ResultArtifact(
            artifact_id="text_answer:turn-1",
            artifact_type="text_answer",
            parent_artifact_id="filtered_df:turn-1",
            text_value="Databao handled this query.",
        )
    )
    workspace.register_artifact(
        ResultArtifact(
            artifact_id="chart:turn-1",
            artifact_type="chart",
            parent_artifact_id="filtered_df:turn-1",
            chart_spec={"mark": "bar"},
            chart_data=[{"a": 1}],
        )
    )
    return workspace


def _build_grounded_response() -> GroundedResponse:
    return GroundedResponse(
        primary_text_artifact_id="text_answer:turn-1",
        primary_table_artifact_id="filtered_df:turn-1",
        primary_chart_artifact_id="chart:turn-1",
        primary_explain_artifact_id="filtered_df:turn-1",
        referenced_artifact_ids=["text_answer:turn-1", "filtered_df:turn-1", "chart:turn-1"],
        followup_target_artifact_id="filtered_df:turn-1",
        available_actions_by_artifact={"filtered_df:turn-1": ["chart", "explain", "summarize", "export"]},
        render_payload={
            "primary_table_artifact_id": "filtered_df:turn-1",
            "primary_chart_artifact_id": "chart:turn-1",
            "binding_intent": {"raw_query": "show revenue by month"},
            "binding_bundle": {"chart_target": "chart:turn-1"},
            "binding_decisions": {"show_chart": {"selected_artifact_id": "chart:turn-1"}},
            "decision_mode": "deterministic",
            "llm_used": False,
        },
    )


def test_chat_service_uses_databao_runtime() -> None:
    service = ChatService(get_settings())
    fake_runtime = FakeRuntime()
    service._runtime = fake_runtime  # type: ignore[attr-defined]
    state = service.create_state()

    state, result = service.send_message(state, "show revenue by month")

    assert fake_runtime.ask_calls
    assert result.last_databao_result is not None
    assert result.last_databao_result.used_databao is True
    assert result.stream_mode == "one_shot"
    assert state.turns[-1].metadata["used_databao"] is True
    assert state.turns[-1].metadata["result_workspace"]["artifacts"]
    assert state.turns[-1].metadata["grounded_response"]["primary_table_artifact_id"] == "filtered_df:turn-1"
    assert state.turns[-1].metadata["binding_intent"] == {}
    assert state.turns[-1].metadata["binding_bundle"] == {}
    assert state.turns[-1].metadata["binding_decisions"] == {}
    assert state.turns[-1].metadata["llm_used"] is False
    assert "query_obligations" in state.turns[-1].metadata
    assert "sql_guardrail_report" in state.turns[-1].metadata
    assert "execution_validation_report" in state.turns[-1].metadata
    assert "sql_retry_history" in state.turns[-1].metadata
    assert "final_failure_reason" in state.turns[-1].metadata
    json.dumps(state.turns[-1].metadata, ensure_ascii=False)
    assert len(fake_runtime.ask_calls[0]) == 2


def test_chat_service_streams_chunks_and_preserves_final_result() -> None:
    service = ChatService(get_settings())
    fake_runtime = FakeRuntime()
    service._runtime = fake_runtime  # type: ignore[attr-defined]
    state = service.create_state()
    upload = process_uploaded_file("revenue.csv", b"month,revenue\nJan,10\nFeb,12\nMar,15\n", "text/csv")
    assert upload is not None

    chunks = list(
        service.send_message_stream(
            state,
            "plot revenue by month",
            uploaded_contexts=[upload],
        )
    )

    cached_result = state.debug_state.pop("_last_chat_service_result", None)

    assert fake_runtime.ask_calls
    assert len(chunks) >= 2
    assert all(isinstance(chunk, str) and chunk for chunk in chunks)
    assert cached_result is not None
    assert cached_result.last_databao_result is not None
    assert cached_result.last_databao_result.text.startswith("Databao handled this query.")
    assert cached_result.stream_mode == "streaming"
    assert state.debug_state["_streaming_mode"] == "streaming"
    assert state.turns[-1].metadata["used_databao"] is True


def test_chat_service_creates_inline_streaming_placeholder_before_iteration() -> None:
    service = ChatService(get_settings())
    fake_runtime = FakeRuntime()
    service._runtime = fake_runtime  # type: ignore[attr-defined]
    state = service.create_state()

    stream = service.send_message_stream(state, "show revenue", uploaded_contexts=[])

    assert state.turns[-1].user_message.content == "show revenue"
    assert state.turns[-1].assistant_message is not None
    assert state.turns[-1].assistant_message.status == "streaming"
    assert state.turns[-1].assistant_message.content == ""
    assert state.turns[-1].metadata["status"] == "streaming"

    list(stream)

    assert state.turns[-1].assistant_message is not None
    assert state.turns[-1].assistant_message.status == "complete"
    assert state.turns[-1].assistant_message.content.startswith("Databao handled this query.")
    assert state.turns[-1].metadata["status"] == "complete"


def test_chat_service_marks_streaming_errors_on_same_assistant_message() -> None:
    class BrokenRuntime(FakeRuntime):
        def ask_stream(self, conversation_id: str, query: str, *, uploaded_contexts, prior_turns=None):
            raise RuntimeError("backend unavailable")

        def ask(self, conversation_id: str, query: str, *, uploaded_contexts, prior_turns=None):
            raise RuntimeError("backend unavailable")

    service = ChatService(get_settings())
    service._runtime = BrokenRuntime()  # type: ignore[attr-defined]
    state = service.create_state()

    list(service.send_message_stream(state, "show revenue", uploaded_contexts=[]))

    assert state.turns[-1].assistant_message is not None
    assert state.turns[-1].assistant_message.status == "error"
    assert "Request failed" in state.turns[-1].assistant_message.content
    assert state.turns[-1].metadata["status"] == "error"
