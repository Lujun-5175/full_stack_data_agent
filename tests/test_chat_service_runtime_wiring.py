from full_stack_data_agent.app.chat_service import ChatService
from full_stack_data_agent.app.runtime_models import DatabaoSessionSnapshot, DatabaoTurnResult
from full_stack_data_agent.config.settings import get_settings
from full_stack_data_agent.llm.models import ProviderHealth


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

    def ask(self, conversation_id: str, query: str, *, uploaded_contexts):
        self.ask_calls.append((conversation_id, query))
        return (
            DatabaoTurnResult(
                text="Databao handled this query.",
                dataframe_preview=[{"a": 1}],
                columns=["a"],
                row_count=1,
                thread_meta={"source": "databao"},
            ),
            DatabaoSessionSnapshot(
                conversation_id=conversation_id,
                llm_name="ollama:gemma4:e4b",
                executor_type="lighthouse",
            ),
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
    assert state.turns[-1].metadata["used_databao"] is True
    assert len(fake_runtime.ask_calls[0]) == 2
