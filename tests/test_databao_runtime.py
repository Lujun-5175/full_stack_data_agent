from __future__ import annotations

import pandas as pd

from full_stack_data_agent.app.databao_runtime import DatabaoRuntime
from full_stack_data_agent.config.settings import get_settings
from full_stack_data_agent.context.models import UploadedFileContext
from full_stack_data_agent.llm.models import ProviderHealth


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
    def __init__(self, thread: FakeThread) -> None:
        self._thread = thread
        self.llm_config = type("Cfg", (), {"name": "ollama:gemma4:e4b"})()

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


def test_runtime_registers_uploaded_dataframe_and_returns_preview(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    fake_thread = FakeThread()
    fake_domain = FakeDomain()

    monkeypatch.setattr(runtime, "provider_status", lambda: ProviderHealth("ollama", "", "gemma4:e4b", True, ["gemma4:e4b"]))
    monkeypatch.setattr("full_stack_data_agent.app.databao_runtime.bao_api.domain", lambda *args, **kwargs: fake_domain)
    monkeypatch.setattr(
        "full_stack_data_agent.app.databao_runtime.bao_api.agent",
        lambda *args, **kwargs: FakeAgent(fake_thread),
    )

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

    result, snapshot = runtime.ask("session-1", "show salary by borough", uploaded_contexts=[upload], history_queries=[])

    assert fake_domain.frames
    assert result.row_count == 2
    assert result.columns == ["borough", "salary"]
    assert result.dataframe_preview is not None
    assert snapshot.registered_tables[0].name == "salary"


def test_runtime_plot_path_and_thread_continuity(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    fake_thread = FakeThread()
    fake_domain = FakeDomain(supports_context=True)

    monkeypatch.setattr("full_stack_data_agent.app.databao_runtime.bao_api.domain", lambda *args, **kwargs: fake_domain)
    monkeypatch.setattr(
        "full_stack_data_agent.app.databao_runtime.bao_api.agent",
        lambda *args, **kwargs: FakeAgent(fake_thread),
    )

    first_result, _ = runtime.ask("session-2", "show salary by borough", uploaded_contexts=[], history_queries=[])
    second_result, _ = runtime.ask("session-2", "plot salary by borough", uploaded_contexts=[], history_queries=["show salary by borough"])

    assert first_result.used_databao is True
    assert second_result.plot_code == '{"mark":"bar"}'
    assert fake_thread.queries == ["show salary by borough", "plot salary by borough"]
    assert len(runtime._sessions) == 1
    assert fake_domain.context_built is True
