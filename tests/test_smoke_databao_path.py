from full_stack_data_agent.app.chat_service import ChatService
from full_stack_data_agent.app.runtime_models import DatabaoSessionSnapshot, DatabaoTurnResult
from full_stack_data_agent.config.settings import get_settings
from full_stack_data_agent.context.upload_processor import process_uploaded_file
from full_stack_data_agent.llm.models import ProviderHealth


class SmokeRuntime:
    def __init__(self) -> None:
        self.calls = []

    def provider_status(self) -> ProviderHealth:
        return ProviderHealth(
            provider="ollama",
            base_url="http://127.0.0.1:11434",
            model="gemma4:e4b",
            connected=True,
            available_models=["gemma4:e4b"],
        )

    def ask(self, conversation_id: str, query: str, *, uploaded_contexts):
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "query": query,
                "uploaded_count": len(uploaded_contexts),
            }
        )
        return (
            DatabaoTurnResult(
                text="Revenue rises from Jan to Mar.",
                dataframe_preview=[
                    {"month": "Jan", "revenue": 10},
                    {"month": "Feb", "revenue": 12},
                ],
                columns=["month", "revenue"],
                row_count=3,
                plot_code='{"mark":"line"}',
                plot_meta={"kind": "line"},
                thread_meta={"executor": "lighthouse", "source": "smoke"},
            ),
            DatabaoSessionSnapshot(
                conversation_id=conversation_id,
                llm_name="gemma4:e4b",
                executor_type="lighthouse",
            ),
        )


def test_default_path_returns_databao_structured_result() -> None:
    service = ChatService(get_settings())
    service._runtime = SmokeRuntime()  # type: ignore[attr-defined]
    state = service.create_state()
    upload = process_uploaded_file("revenue.csv", b"month,revenue\nJan,10\nFeb,12\nMar,15\n", "text/csv")
    assert upload is not None

    state, result = service.send_message(
        state,
        "plot revenue by month",
        uploaded_contexts=[upload],
    )

    assert result.last_error is None
    assert result.last_databao_result is not None
    assert result.last_databao_result["used_databao"] is True
    assert result.last_databao_result["plot_code"] == '{"mark":"line"}'
    assert state.turns[-1].metadata["row_count"] == 3
    assert state.turns[-1].metadata["dataframe_preview"]
