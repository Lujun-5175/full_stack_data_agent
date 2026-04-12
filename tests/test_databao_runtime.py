from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from full_stack_data_agent.app.dependencies import RuntimeDependencyStatus, check_runtime_dependencies
from full_stack_data_agent.app.databao_runtime import DatabaoRuntime
from full_stack_data_agent.config.settings import get_settings
from full_stack_data_agent.context.models import UploadedFileContext
from full_stack_data_agent.context.semantic_profile import build_semantic_profile
from full_stack_data_agent.llm.models import ProviderUnavailableError


class FakeThread:
    def __init__(self, dataframe: pd.DataFrame | None = None, *, fail_on_ask: Exception | None = None) -> None:
        self.queries: list[str] = []
        self.plot_requests: list[str] = []
        self._dataframe = dataframe if dataframe is not None else pd.DataFrame([{"borough": "Queens", "salary": 100}, {"borough": "Bronx", "salary": 80}])
        self._fail_on_ask = fail_on_ask

    def ask(self, query: str):
        if self._fail_on_ask is not None:
            exc = self._fail_on_ask
            self._fail_on_ask = None
            raise exc
        self.queries.append(query)
        return self

    def text(self) -> str:
        return "analysis complete"

    def df(self, rows_limit=None):
        return self._dataframe

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
        self.frame_descriptions: list[str] = []

    def add_description(self, description):
        self.descriptions.append(str(description))

    def add_df(self, df, *, name=None, description=None):
        self.frames.append((name or "df1", df.copy()))
        if description is not None:
            self.frame_descriptions.append(str(description))

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


def test_llm_config_uses_deepseek_defaults(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    get_settings.cache_clear()
    try:
        runtime = DatabaoRuntime(get_settings())

        llm_config = runtime._build_llm_config()

        assert llm_config.name == "deepseek-chat"
        assert llm_config.api_base_url == "https://api.deepseek.com"
        assert llm_config.use_responses_api is False
        assert llm_config.ollama_pull_model is False
        assert llm_config.model_kwargs["api_key"] == "sk-test"
    finally:
        get_settings.cache_clear()


def test_llm_config_can_resolve_ollama_fallback(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:e4b")
    get_settings.cache_clear()
    try:
        runtime = DatabaoRuntime(get_settings())

        llm_config = runtime._build_llm_config()

        assert llm_config.name == "ollama:gemma4:e4b"
        assert llm_config.api_base_url is None
        assert llm_config.use_responses_api is False
        assert llm_config.ollama_pull_model is False
        assert llm_config.model_kwargs["num_ctx"] == get_settings().ollama_num_ctx
    finally:
        get_settings.cache_clear()


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


def test_runtime_falls_back_to_ollama_when_primary_provider_raises_provider_error(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    get_settings.cache_clear()
    try:
        runtime = DatabaoRuntime(get_settings())
        fake_domain = FakeDomain()
        deepseek_thread = FakeThread(fail_on_ask=ProviderUnavailableError("DeepSeek temporarily unavailable"))
        ollama_thread = FakeThread()

        def fake_create_agent(domain, llm_config):
            if llm_config.name == "deepseek-chat":
                return FakeAgent(deepseek_thread, llm_name=llm_config.name)
            return FakeAgent(ollama_thread, llm_name=llm_config.name)

        monkeypatch.setattr(
            "full_stack_data_agent.app.databao_runtime.pd.read_csv",
            lambda *_args, **_kwargs: pd.DataFrame([{"borough": "Queens", "salary": 100}]),
        )
        monkeypatch.setattr(runtime, "_create_domain", lambda: fake_domain)
        monkeypatch.setattr(runtime, "_create_agent", fake_create_agent)

        upload = UploadedFileContext(
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

        result, snapshot = runtime.ask("session-fallback", "show salary by borough", uploaded_contexts=[upload])

        assert result.fallback_triggered is True
        assert result.provider_used == "ollama"
        assert result.model_used == get_settings().ollama_model
        assert "fallback_reason" in result.thread_meta
        assert result.thread_meta["fallback_triggered"] is True
        assert fake_domain.frames
        assert snapshot.registered_tables
    finally:
        get_settings.cache_clear()


def test_runtime_normalizes_uploaded_dataframe_before_registration(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    fake_thread = FakeThread()
    fake_domain = FakeDomain()

    monkeypatch.setattr(
        "full_stack_data_agent.app.databao_runtime.pd.read_csv",
        lambda *_args, **_kwargs: pd.DataFrame(
            [
                {
                    "label": "  Alpha  ",
                    "amount": "1,200.50",
                    "flag": "Yes",
                    "event_date": "2024-01-02",
                    "record_id": "10001",
                    "blank_value": "   ",
                },
                {
                    "label": "Beta",
                    "amount": "80",
                    "flag": "No",
                    "event_date": "2024-02-03",
                    "record_id": "10002",
                    "blank_value": "",
                },
            ]
        ),
    )
    monkeypatch.setattr(runtime, "_create_domain", lambda: fake_domain)
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: FakeAgent(fake_thread))

    upload = UploadedFileContext(
        file_name="generic_upload.csv",
        mime_type="text/csv",
        size_bytes=64,
        summary="generic upload",
        extracted_text="label,amount,flag,event_date,record_id,blank_value\nAlpha,1200.50,Yes,2024-01-02,10001, \nBeta,80,No,2024-02-03,10002,\n",
        is_tabular=True,
        table_name="generic_upload",
        row_count=2,
        columns=["label", "amount", "flag", "event_date", "record_id", "blank_value"],
    )

    result, snapshot = runtime.ask("session-normalization", "summarize the uploaded file", uploaded_contexts=[upload])

    assert fake_domain.frames
    normalized_frame = fake_domain.frames[0][1]
    assert normalized_frame["label"].tolist() == ["Alpha", "Beta"]
    assert pd.api.types.is_numeric_dtype(normalized_frame["amount"])
    assert pd.api.types.is_bool_dtype(normalized_frame["flag"])
    assert pd.api.types.is_datetime64_any_dtype(normalized_frame["event_date"])
    assert not pd.api.types.is_numeric_dtype(normalized_frame["record_id"])
    assert pd.isna(normalized_frame["blank_value"].iloc[0])
    assert pd.isna(normalized_frame["blank_value"].iloc[1])
    assert result.row_count == 2
    assert snapshot.registered_tables[0].normalization_report["column_type_map"]["amount"] == "numeric"
    assert snapshot.registered_tables[0].normalization_report["columns_skipped"]
    assert snapshot.normalization_reports


def test_runtime_builds_semantic_description_and_stores_profile(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    fake_thread = FakeThread(
        dataframe=pd.DataFrame(
            [
                {"student_id": "S-1001", "overall_score": 92, "enrollment_date": "2024-01-02", "grade": "A"},
                {"student_id": "S-1002", "overall_score": 85, "enrollment_date": "2024-02-03", "grade": "B"},
                {"student_id": "S-1003", "overall_score": 78, "enrollment_date": "2024-03-04", "grade": "B"},
            ]
        ),
    )
    fake_domain = FakeDomain()

    monkeypatch.setattr(
        "full_stack_data_agent.app.databao_runtime.pd.read_csv",
        lambda *_args, **_kwargs: pd.DataFrame(
            [
                {"student_id": "S-1001", "overall_score": 92, "enrollment_date": "2024-01-02", "grade": "A"},
                {"student_id": "S-1002", "overall_score": 85, "enrollment_date": "2024-02-03", "grade": "B"},
                {"student_id": "S-1003", "overall_score": 78, "enrollment_date": "2024-03-04", "grade": "B"},
            ]
        ),
    )
    monkeypatch.setattr(runtime, "_create_domain", lambda: fake_domain)
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: FakeAgent(fake_thread))

    upload = UploadedFileContext(
        file_name="student_scores.csv",
        mime_type="text/csv",
        size_bytes=96,
        summary="student scores",
        extracted_text="student_id,overall_score,enrollment_date,grade\nS-1001,92,2024-01-02,A\nS-1002,85,2024-02-03,B\nS-1003,78,2024-03-04,B\n",
        is_tabular=True,
        table_name="student_scores",
        row_count=3,
        columns=["student_id", "overall_score", "enrollment_date", "grade"],
    )

    result, snapshot = runtime.ask("session-semantic", "summarize the uploaded data", uploaded_contexts=[upload])

    assert fake_domain.frame_descriptions
    description = fake_domain.frame_descriptions[0]
    assert "Table: student_scores" in description
    assert "Numeric measures" in description
    assert "Time columns" in description
    assert "Identifier columns" in description
    assert "Categorical columns" in description
    assert len(description) <= 2000
    assert snapshot.registered_tables[0].semantic_profile["measure_candidates"] == ["overall_score"]
    assert snapshot.registered_tables[0].semantic_profile["time_candidates"] == ["enrollment_date"]
    assert snapshot.registered_tables[0].semantic_profile["key_candidates"] == ["student_id"]
    assert result.row_count == 3


def test_build_df_description_limits_categorical_examples(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    dataframe = pd.DataFrame(
        {
            "category": [f"value_{index}" for index in range(12)] + [f"value_{index}" for index in range(8)],
            "amount": [index * 10 for index in range(20)],
        }
    )
    profile = build_semantic_profile(dataframe)

    description = runtime._build_df_description("sample_table", "sample.csv", dataframe, profile)

    assert "Table: sample_table (from sample.csv)" in description
    assert "category:" in description
    assert description.count("value_") <= 10
    assert "{" not in description
    assert len(description) <= 2000


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


def test_falls_back_to_in_memory_domain_when_project_domain_is_unsupported(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    fake_thread = FakeThread()
    fake_thread.df = lambda rows_limit=None: pd.DataFrame([{"borough": "Queens", "salary": 100}])  # type: ignore[method-assign]
    fake_domain = FakeDomain()

    def fake_domain_factory(project_dir=None):
        if project_dir is None:
            return fake_domain
        raise ValueError("Only configurable sources from a DCE project are supported.")

    monkeypatch.setitem(sys.modules, "pywintypes", types.ModuleType("pywintypes"))
    monkeypatch.setitem(sys.modules, "win32api", types.ModuleType("win32api"))
    monkeypatch.setitem(sys.modules, "win32con", types.ModuleType("win32con"))
    monkeypatch.setitem(sys.modules, "win32job", types.ModuleType("win32job"))
    monkeypatch.setattr("databao.agent.api.domain", fake_domain_factory)
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: FakeAgent(fake_thread))
    monkeypatch.setattr(
        "full_stack_data_agent.app.databao_runtime.pd.read_csv",
        lambda *_args, **_kwargs: pd.DataFrame([{"borough": "Queens", "salary": 100}]),
    )

    upload = UploadedFileContext(
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

    result, snapshot = runtime.ask("session-fallback", "show salary by borough", uploaded_contexts=[upload])

    assert fake_domain.frames
    assert result.row_count == 1
    assert snapshot.context_build_error == "Only configurable sources from a DCE project are supported."
