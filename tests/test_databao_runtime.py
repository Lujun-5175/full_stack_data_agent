from __future__ import annotations

import pandas as pd
import pytest

from full_stack_data_agent.app.dependencies import RuntimeDependencyStatus, check_runtime_dependencies
from full_stack_data_agent.app.databao_runtime import DatabaoRuntime
from full_stack_data_agent.config.settings import get_settings
from full_stack_data_agent.context.models import UploadedFileContext
from full_stack_data_agent.llm.models import ProviderUnavailableError


class FakeThread:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.plot_requests: list[str] = []

    def ask(self, query: str):
        self.queries.append(query)
        return self

    def text(self) -> str:
        return "analysis complete"

    def df(self, rows_limit=None):
        return pd.DataFrame([{"borough": "Queens", "salary": 100}, {"borough": "Bronx", "salary": 80}])

    def plot(self, request=None, **kwargs):
        self.plot_requests.append(request or "")
        return type("PlotResult", (), {"code": '{"mark":"bar"}', "meta": {"kind": "bar"}})()

    def meta(self):
        return {"messages": [], "executor": "lighthouse"}


class FakeAgent:
    def __init__(self, thread: FakeThread, llm_name: str = "ollama:gemma4:e4b") -> None:
        self._thread = thread
        self.llm_config = type("Cfg", (), {"name": llm_name})()

    def thread(self, **kwargs):
        return self._thread


class FakeDomain:
    def __init__(self, *, supports_context: bool = False) -> None:
        self.supports_context = supports_context
        self.context_built = False
        self.descriptions: list[str] = []
        self.frames: list[tuple[str, pd.DataFrame]] = []

    def add_description(self, description):
        self.descriptions.append(str(description))

    def add_df(self, df, *, name=None, description=None):
        self.frames.append((name or "df1", df.copy()))

    def is_context_built(self):
        return self.context_built

    def build_context(self):
        self.context_built = True


def test_dependency_check_reports_missing_langchain_core(monkeypatch) -> None:
    def fake_find_spec(name: str):
        return None if name == "langchain_core" else object()

    monkeypatch.setattr("full_stack_data_agent.app.dependencies.find_spec", fake_find_spec)

    status = check_runtime_dependencies()

    assert not status.is_ok
    assert status.missing_modules == ["langchain_core"]
    assert "langchain_core" in (status.error or "")
    assert "pip install -e" in status.install_hint


def test_provider_status_reports_missing_dependencies(monkeypatch) -> None:
    def fake_find_spec(name: str):
        return None if name == "langchain_core" else object()

    monkeypatch.setattr("full_stack_data_agent.app.dependencies.find_spec", fake_find_spec)

    runtime = DatabaoRuntime(get_settings())
    status = runtime.provider_status()

    assert not status.connected
    assert "langchain_core" in (status.error or "")


def test_runtime_raises_clear_error_when_dependencies_missing(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    monkeypatch.setattr(
        "full_stack_data_agent.app.databao_runtime.check_runtime_dependencies",
        lambda: RuntimeDependencyStatus(
            missing_modules=["langchain_core"],
            available_modules=["langchain", "langgraph"],
            error="Missing runtime dependencies: langchain_core.",
        ),
    )

    with pytest.raises(ProviderUnavailableError, match="langchain_core"):
        runtime.ask("session-missing-deps", "hello")


def test_llm_config_uses_ollama_native_defaults() -> None:
    runtime = DatabaoRuntime(get_settings())

    llm_config = runtime._build_llm_config()

    assert llm_config.name == "ollama:gemma4:e4b"
    assert llm_config.api_base_url is None
    assert llm_config.use_responses_api is False
    assert llm_config.ollama_pull_model is False
    assert llm_config.model_kwargs["num_ctx"] == get_settings().ollama_num_ctx


def test_runtime_registers_uploaded_dataframe_and_reuses_thread_when_signature_unchanged(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    fake_thread = FakeThread()
    fake_domain = FakeDomain()

    monkeypatch.setattr(
        "full_stack_data_agent.app.databao_runtime.pd.read_csv",
        lambda *_args, **_kwargs: pd.DataFrame([{"borough": "Queens", "salary": 100}, {"borough": "Bronx", "salary": 80}]),
    )
    monkeypatch.setattr(runtime, "_create_domain", lambda: fake_domain)
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: FakeAgent(fake_thread))

    upload = UploadedFileContext(
        file_name="salary.csv",
        mime_type="text/csv",
        size_bytes=32,
        summary="salary data",
        extracted_text="borough,salary\nQueens,100\nBronx,80\n",
        is_tabular=True,
        table_name="salary",
        row_count=2,
        columns=["borough", "salary"],
    )

    first_result, first_snapshot = runtime.ask("session-1", "show salary by borough", uploaded_contexts=[upload])
    second_result, second_snapshot = runtime.ask("session-1", "plot salary by borough", uploaded_contexts=[upload])

    assert fake_domain.frames
    assert first_result.row_count == 2
    assert second_result.plot_code == '{"mark":"bar"}'
    assert fake_thread.queries == ["show salary by borough", "plot salary by borough"]
    assert len(runtime._sessions) == 1
    assert first_snapshot.datasource_changed is False
    assert second_snapshot.datasource_changed is False


def test_no_history_replay_when_datasource_changes(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    first_thread = FakeThread()
    second_thread = FakeThread()
    first_domain = FakeDomain(supports_context=True)
    second_domain = FakeDomain(supports_context=True)
    domains = [first_domain, second_domain]
    agents = [FakeAgent(first_thread), FakeAgent(second_thread)]

    monkeypatch.setattr(
        "full_stack_data_agent.app.databao_runtime.pd.read_csv",
        lambda *_args, **_kwargs: pd.DataFrame([{"borough": "Queens", "salary": 100}]),
    )
    monkeypatch.setattr(runtime, "_create_domain", lambda: domains.pop(0))
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: agents.pop(0))

    first_upload = UploadedFileContext(
        file_name="salary.csv",
        mime_type="text/csv",
        size_bytes=32,
        summary="salary data",
        extracted_text="borough,salary\nQueens,100\n",
        is_tabular=True,
        table_name="salary",
        row_count=1,
        columns=["borough", "salary"],
    )
    second_upload = UploadedFileContext(
        file_name="salary_v2.csv",
        mime_type="text/csv",
        size_bytes=40,
        summary="salary data updated",
        extracted_text="borough,salary\nQueens,100\nBronx,80\n",
        is_tabular=True,
        table_name="salary_v2",
        row_count=2,
        columns=["borough", "salary"],
    )

    runtime.ask("session-2", "show salary by borough", uploaded_contexts=[first_upload])
    result, snapshot = runtime.ask("session-2", "plot salary by borough", uploaded_contexts=[second_upload])

    assert first_thread.queries == ["show salary by borough"]
    assert second_thread.queries == ["plot salary by borough"]
    assert snapshot.datasource_changed is True
    assert snapshot.context_replayed is False
    assert snapshot.thread_reset_reason == "datasource_changed"
    assert result.plot_code == '{"mark":"bar"}'


def test_domain_descriptions_are_deduped_across_rebuilds(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    first_domain = FakeDomain()
    second_domain = FakeDomain()
    domains = [first_domain, second_domain]
    agents = [FakeAgent(FakeThread()), FakeAgent(FakeThread())]

    monkeypatch.setattr(runtime, "_create_domain", lambda: domains.pop(0))
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: agents.pop(0))

    note = UploadedFileContext(
        file_name="notes.txt",
        mime_type="text/plain",
        size_bytes=16,
        summary="same note",
        snippets=["1. same note"],
        extracted_text="same note",
        is_tabular=False,
    )
    note_again = UploadedFileContext(
        file_name="notes_v2.txt",
        mime_type="text/plain",
        size_bytes=20,
        summary="same note",
        snippets=["1. same note"],
        extracted_text="same note",
        is_tabular=False,
    )

    runtime.ask("session-3", "summarize notes", uploaded_contexts=[note])
    runtime.ask("session-3", "summarize notes again", uploaded_contexts=[note_again])

    assert len(first_domain.descriptions) == 2
    assert second_domain.descriptions == []
