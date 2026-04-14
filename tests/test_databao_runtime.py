from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from full_stack_data_agent.app.dependencies import RuntimeDependencyStatus, check_runtime_dependencies
from full_stack_data_agent.app.databao_runtime import DatabaoRuntime
from full_stack_data_agent.config.settings import get_settings
from databao.agent.core.sources import SourcesManager
from full_stack_data_agent.context.models import UploadedFileContext
from full_stack_data_agent.context.semantic_profile import build_semantic_profile
from full_stack_data_agent.llm.models import ProviderUnavailableError


class FakeScopedCache:
    def __init__(self, store: dict[str, dict[str, dict]]) -> None:
        self._store = store
        self._scope = ""

    def put(self, key: str, state: dict) -> None:
        self._store.setdefault(self._scope, {})[key] = state

    def get(self, key: str, default: dict | None = None) -> dict:
        return self._store.get(self._scope, {}).get(key, default or {})

    def scoped(self, scope: str):
        scoped = FakeScopedCache(self._store)
        scoped._scope = scope
        return scoped


class FakeThread:
    def __init__(
        self,
        dataframe: pd.DataFrame | None = None,
        *,
        fail_on_ask: Exception | None = None,
        auto_visualization_result: object | None = None,
    ) -> None:
        self.queries: list[str] = []
        self.plot_requests: list[str] = []
        self._dataframe = dataframe if dataframe is not None else pd.DataFrame([{"borough": "Queens", "salary": 100}, {"borough": "Bronx", "salary": 80}])
        self._fail_on_ask = fail_on_ask
        self._visualization_result = auto_visualization_result

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
        self.cache = FakeScopedCache({})

    def close(self) -> None:
        return None

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
        tabular_payload="borough,salary\nQueens,100\nBronx,80\n",
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


def test_runtime_registers_full_csv_payload_instead_of_preview_markdown(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    fake_thread = FakeThread()
    fake_domain = FakeDomain()
    dataframe = pd.DataFrame(
        {
            "customer_id": [f"C-{index:03d}" for index in range(1, 13)],
            "score": list(range(10, 22)),
        }
    )
    preview_text = dataframe.head(5).to_markdown(index=False)
    payload = dataframe.to_csv(index=False)

    monkeypatch.setattr(runtime, "_create_domain", lambda: fake_domain)
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: FakeAgent(fake_thread))

    upload = UploadedFileContext(
        file_name="customers.csv",
        mime_type="text/csv",
        size_bytes=len(payload.encode("utf-8")),
        summary="customer scores",
        extracted_text=preview_text,
        tabular_payload=payload,
        is_tabular=True,
        table_name="customers",
        row_count=len(dataframe),
        columns=["customer_id", "score"],
    )

    result, snapshot = runtime.ask("session-csv-payload", "summarize the uploaded file", uploaded_contexts=[upload])

    assert fake_domain.frames
    assert fake_domain.frames[0][1].shape[0] == len(dataframe)
    assert snapshot.registered_tables[0].row_count == len(dataframe)
    assert result.text == "analysis complete"


@pytest.mark.parametrize(
    "file_name,payload,row_count,columns",
    [
        ("workbook.xlsx", "name,value\nalpha,1\nbeta,2\n", 2, ["name", "value"]),
        ("scores.json", "team,score\nA,1\nB,2\nC,3\n", 3, ["team", "score"]),
    ],
)
def test_runtime_registers_tabular_payloads_for_excel_and_json(
    monkeypatch,
    file_name: str,
    payload: str,
    row_count: int,
    columns: list[str],
) -> None:
    runtime = DatabaoRuntime(get_settings())
    fake_thread = FakeThread()
    fake_domain = FakeDomain()

    monkeypatch.setattr(runtime, "_create_domain", lambda: fake_domain)
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: FakeAgent(fake_thread))

    upload = UploadedFileContext(
        file_name=file_name,
        mime_type="application/octet-stream",
        size_bytes=len(payload.encode("utf-8")),
        summary="tabular upload",
        extracted_text="Preview only\n| col | value |\n| --- | --- |\n| alpha | 1 |\n",
        tabular_payload=payload,
        is_tabular=True,
        table_name="uploaded_table",
        row_count=row_count,
        columns=columns,
    )

    result, snapshot = runtime.ask(f"session-{file_name}", "summarize the uploaded file", uploaded_contexts=[upload])

    assert fake_domain.frames
    assert fake_domain.frames[0][1].shape[0] == row_count
    assert snapshot.registered_tables[0].row_count == row_count
    assert result.text == "analysis complete"


def test_runtime_parse_dataframe_requires_tabular_payload() -> None:
    runtime = DatabaoRuntime(get_settings())
    upload = UploadedFileContext(
        file_name="preview_only.csv",
        mime_type="text/csv",
        size_bytes=48,
        summary="preview only",
        extracted_text="| a | b |\n| --- | --- |\n| 1 | 2 |\n",
        is_tabular=True,
        table_name="preview_only",
        row_count=1,
        columns=["a", "b"],
    )

    with pytest.raises(ValueError, match="reconstructable payload"):
        runtime._parse_dataframe(upload)


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
            tabular_payload="borough,salary\nQueens,100\n",
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


def test_runtime_collects_auto_visualization_without_explicit_chart_intent(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    auto_plot = type(
        "PlotResult",
        (),
        {
            "code": '{"mark":"line"}',
            "meta": {"kind": "line"},
            "spec": {"mark": "line"},
            "spec_df": pd.DataFrame([{"x": 1, "y": 2}, {"x": 2, "y": 3}]),
        },
    )()
    fake_thread = FakeThread(auto_visualization_result=auto_plot)
    fake_domain = FakeDomain()

    monkeypatch.setattr(runtime, "_create_domain", lambda: fake_domain)
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: FakeAgent(fake_thread))

    result, _ = runtime.ask("session-auto-vis", "summarize the trend", uploaded_contexts=[])

    assert result.plot_code == '{"mark":"line"}'
    assert result.chart_debug["chart_requested"] is True
    assert result.plot_spec == {"mark": "line"}
    assert result.plot_data == [{"x": 1, "y": 2}, {"x": 2, "y": 3}]


def test_runtime_propagates_planning_failed_chart_debug(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    plot_result = type(
        "PlotResult",
        (),
        {
            "code": "{}",
            "kind": "barplot",
            "meta": {
                "plot_error": "Chart planner did not return valid JSON",
                "chart_debug": {
                    "planner": "llm",
                    "planner_status": "planning_failed",
                    "validated": False,
                    "planner_error": "Chart planner did not return valid JSON",
                    "validation_errors": [],
                    "fallback_blocked": True,
                    "raw_planner_response": "not json",
                },
            },
        },
    )()
    fake_thread = FakeThread()
    fake_thread.plot = lambda request=None, **kwargs: plot_result
    fake_domain = FakeDomain()

    monkeypatch.setattr(runtime, "_create_domain", lambda: fake_domain)
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: FakeAgent(fake_thread))

    result, _ = runtime.ask("session-plan-fail", "show a bar chart with x=borough and y=salary", uploaded_contexts=[])

    assert result.chart_debug["planner_status"] == "planning_failed"
    assert result.chart_debug["fallback_blocked"] is True
    assert result.chart_debug["chart_generated"] is False
    assert result.chart_debug["chart_failure_stage"] == "planning_failed"
    assert "valid JSON" in str(result.chart_debug["chart_failure_reason"])


def test_runtime_propagates_schema_parse_failed_chart_debug(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    plot_result = type(
        "PlotResult",
        (),
        {
            "code": "{}",
            "kind": "barplot",
            "meta": {
                "plot_error": "Chart planner JSON does not match schema",
                "chart_debug": {
                    "planner": "llm_json",
                    "planner_status": "schema_parse_failed",
                    "validated": False,
                    "planner_error": "Chart planner JSON does not match schema",
                    "validation_errors": [],
                    "fallback_blocked": True,
                },
            },
        },
    )()
    fake_thread = FakeThread()
    fake_thread.plot = lambda request=None, **kwargs: plot_result
    fake_domain = FakeDomain()

    monkeypatch.setattr(runtime, "_create_domain", lambda: fake_domain)
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: FakeAgent(fake_thread))

    result, _ = runtime.ask("session-schema-fail", "show a bar chart with x=borough and y=salary", uploaded_contexts=[])

    assert result.chart_debug["planner_status"] == "schema_parse_failed"
    assert result.chart_debug["fallback_blocked"] is True
    assert result.chart_debug["chart_generated"] is False
    assert result.chart_debug["chart_failure_stage"] == "schema_parse_failed"


def test_runtime_propagates_validation_failed_chart_debug(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    plot_result = type(
        "PlotResult",
        (),
        {
            "code": "{}",
            "kind": "barplot",
            "meta": {
                "plot_error": "User explicitly requested hue=churn_status, but plan omitted it.",
                "chart_debug": {
                    "planner": "llm",
                    "planner_status": "validation_failed",
                    "validated": False,
                    "planner_error": "final plan failed validation",
                    "validation_errors": ["User explicitly requested hue=churn_status, but plan omitted it."],
                    "fallback_blocked": True,
                },
            },
        },
    )()
    fake_thread = FakeThread()
    fake_thread.plot = lambda request=None, **kwargs: plot_result
    fake_domain = FakeDomain()

    monkeypatch.setattr(runtime, "_create_domain", lambda: fake_domain)
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: FakeAgent(fake_thread))

    result, _ = runtime.ask(
        "session-validation-fail",
        "show a grouped bar chart with x=PaymentMethod, y=customer_count, hue=churn_status",
        uploaded_contexts=[],
    )

    assert result.chart_debug["planner_status"] == "validation_failed"
    assert result.chart_debug["chart_generated"] is False
    assert result.chart_debug["chart_failure_stage"] == "validation_failed"
    assert "hue" in " ".join(result.chart_debug["validation_errors"]).lower()


def test_runtime_propagates_render_failed_chart_debug_even_with_image_artifact(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())

    class PlotResult:
        code = "{}"
        kind = "barplot"
        meta = {
            "plot_error": "boom",
            "chart_debug": {
                "planner": "llm",
                "planner_status": "render_failed",
                "validated": False,
                "render_error": "boom",
                "fallback_blocked": True,
            },
        }

        @staticmethod
        def png_base64():
            return "ZmFrZQ=="

    fake_thread = FakeThread()
    fake_thread.plot = lambda request=None, **kwargs: PlotResult()
    fake_domain = FakeDomain()

    monkeypatch.setattr(runtime, "_create_domain", lambda: fake_domain)
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: FakeAgent(fake_thread))

    result, _ = runtime.ask("session-render-fail", "show a bar chart with x=borough and y=salary", uploaded_contexts=[])

    assert result.chart_debug["planner_status"] == "render_failed"
    assert result.chart_debug["chart_generated"] is False
    assert result.chart_debug["chart_failure_stage"] == "render_failed"
    assert result.chart_debug["chart_failure_reason"] == "boom"


def test_runtime_generic_visual_words_do_not_trigger_plot(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    fake_thread = FakeThread()
    fake_domain = FakeDomain()

    monkeypatch.setattr(runtime, "_create_domain", lambda: fake_domain)
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: FakeAgent(fake_thread))

    result, _ = runtime.ask("session-no-plot-trigger", "Please give a distribution overview and visual summary in text only.")

    assert result.chart_debug["chart_requested"] is False
    assert fake_thread.plot_requests == []


def test_runtime_explicit_chart_request_still_triggers_plot(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    fake_thread = FakeThread()
    fake_domain = FakeDomain()

    monkeypatch.setattr(runtime, "_create_domain", lambda: fake_domain)
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: FakeAgent(fake_thread))

    result, _ = runtime.ask("session-explicit-plot-trigger", "show a bar chart with x=borough and y=salary")

    assert result.chart_debug["chart_requested"] is True
    assert len(fake_thread.plot_requests) == 1


def test_drop_session_removes_cached_runtime_state(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    fake_thread = FakeThread()
    fake_domain = FakeDomain()
    fake_agent = FakeAgent(fake_thread)

    monkeypatch.setattr(runtime, "_create_domain", lambda: fake_domain)
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: fake_agent)

    runtime.ask("session-drop", "hello", uploaded_contexts=[])

    assert "session-drop" in runtime._sessions

    runtime.drop_session("session-drop")

    assert "session-drop" not in runtime._sessions


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
        tabular_payload="label,amount,flag,event_date,record_id,blank_value\nAlpha,1200.50,Yes,2024-01-02,10001, \nBeta,80,No,2024-02-03,10002,\n",
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
        tabular_payload="student_id,overall_score,enrollment_date,grade\nS-1001,92,2024-01-02,A\nS-1002,85,2024-02-03,B\nS-1003,78,2024-03-04,B\n",
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


def test_runtime_replays_history_when_datasource_changes(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    first_thread = FakeThread()
    second_thread = FakeThread()
    first_domain = FakeDomain(supports_context=True)
    second_domain = FakeDomain(supports_context=True)
    domains = [first_domain, second_domain]
    first_agent = FakeAgent(first_thread)
    second_agent = FakeAgent(second_thread)
    agents = [first_agent, second_agent]

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
        tabular_payload="borough,salary\nQueens,100\n",
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
        tabular_payload="borough,salary\nQueens,100\nBronx,80\n",
        is_tabular=True,
        table_name="salary_v2",
        row_count=2,
        columns=["borough", "salary"],
    )

    first_result, _ = runtime.ask("session-2", "show salary by borough", uploaded_contexts=[first_upload])
    prior_turns = [
        type(
            "Turn",
            (),
            {
                "user_message": type("Msg", (), {"content": "show salary by borough"})(),
                "assistant_message": type("Msg", (), {"content": first_result.text})(),
            },
        )()
    ]
    result, snapshot = runtime.ask(
        "session-2",
        "plot salary by borough",
        uploaded_contexts=[second_upload],
        prior_turns=prior_turns,
    )

    assert first_thread.queries == ["show salary by borough"]
    assert second_thread.queries == ["plot salary by borough"]
    assert snapshot.datasource_changed is True
    assert snapshot.context_replayed is True
    assert snapshot.thread_reset_reason == "datasource_rebuilt_with_history_replay"
    assert result.plot_code == '{"mark":"bar"}'

    replay_cache = second_agent.cache.scoped("fsda/session-2").get("state", {})
    assert replay_cache["messages"][0].content == "show salary by borough"
    assert replay_cache["messages"][1].content == "analysis complete"


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
        tabular_payload="borough,salary\nQueens,100\n",
        is_tabular=True,
        table_name="salary",
        row_count=1,
        columns=["borough", "salary"],
    )

    result, snapshot = runtime.ask("session-fallback", "show salary by borough", uploaded_contexts=[upload])

    assert fake_domain.frames
    assert result.row_count == 1
    assert snapshot.context_build_error == "Only configurable sources from a DCE project are supported."


def test_sources_manager_allows_empty_finalize() -> None:
    manager = SourcesManager()

    manager.finalize()


def test_runtime_extracts_deliverables_with_llm(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    monkeypatch.setattr(runtime, "_trigger_explicit_deliverable_extraction", lambda query: True)
    monkeypatch.setattr(
        runtime,
        "_llm_json",
        lambda llm_config, messages: {
            "language": "zh",
            "deliverables": [
                "\u6309 gender \u5206\u7ec4\u5e76\u8ba1\u7b97\u5b66\u751f\u4eba\u6570\u3001\u5e73\u5747 overall_score\u3001\u5e73\u5747 study_hours_per_day",
                "\u753b\u4e00\u5f20\u67f1\u72b6\u56fe\uff0cx \u8f74\u662f gender\uff0cy \u8f74\u662f\u5e73\u5747 overall_score",
                "\u89e3\u91ca\u54ea\u4e2a\u6027\u522b\u5e73\u5747\u5206\u66f4\u9ad8\u4ee5\u53ca\u5dee\u8ddd\u5927\u4e0d\u5927",
            ],
        },
    )

    clauses, debug = runtime._extract_explicit_deliverables(
        "\u8bf7\u5b8c\u6210\u4ee5\u4e0b\u4efb\u52a1\uff1a\u6309 gender \u5206\u7ec4\uff0c\u753b\u56fe\u5e76\u89e3\u91ca\u5dee\u5f02",
        llm_config=object(),
    )

    assert clauses == [
        "\u6309 gender \u5206\u7ec4\u5e76\u8ba1\u7b97\u5b66\u751f\u4eba\u6570\u3001\u5e73\u5747 overall_score\u3001\u5e73\u5747 study_hours_per_day",
        "\u753b\u4e00\u5f20\u67f1\u72b6\u56fe\uff0cx \u8f74\u662f gender\uff0cy \u8f74\u662f\u5e73\u5747 overall_score",
        "\u89e3\u91ca\u54ea\u4e2a\u6027\u522b\u5e73\u5747\u5206\u66f4\u9ad8\u4ee5\u53ca\u5dee\u8ddd\u5927\u4e0d\u5927",
    ]
    assert debug["source"] == "llm"
    assert debug["language"] == "zh"


def test_runtime_falls_back_to_regex_deliverable_extraction_when_llm_fails(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    monkeypatch.setattr(runtime, "_trigger_explicit_deliverable_extraction", lambda query: True)
    monkeypatch.setattr(runtime, "_llm_json", lambda llm_config, messages: (_ for _ in ()).throw(RuntimeError("boom")))

    clauses, debug = runtime._extract_explicit_deliverables(
        "\u8bf7\u56de\u7b54\uff1a1. \u6309 gender \u5206\u7ec4\u30022. \u753b\u67f1\u72b6\u56fe\u30023. \u89e3\u91ca\u5dee\u5f02\u3002",
        llm_config=object(),
    )

    assert clauses
    assert any("gender" in clause or "\u67f1\u72b6\u56fe" in clause for clause in clauses)
    assert debug["source"] == "legacy_fallback"
    assert "error" in debug


def test_runtime_uses_semantic_judge_when_available(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    monkeypatch.setattr(
        runtime,
        "_llm_json",
        lambda llm_config, messages: {
            "covered": True,
            "reason": "The answer states that female students have a slightly higher average score.",
        },
    )

    covered, debug = runtime._response_covers_clause(
        "Female students have a slightly higher average score.",
        "\u54ea\u4e2a\u6027\u522b\u5e73\u5747\u5206\u66f4\u9ad8",
        query="\u54ea\u4e2a\u6027\u522b\u5e73\u5747\u5206\u66f4\u9ad8",
        llm_config=object(),
    )

    assert covered is True
    assert debug["source"] == "llm"
    assert "higher average score" in debug["reason"]


def test_runtime_falls_back_to_lexical_coverage_judge_when_llm_fails(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    monkeypatch.setattr(runtime, "_llm_json", lambda llm_config, messages: (_ for _ in ()).throw(RuntimeError("boom")))

    covered, debug = runtime._response_covers_clause(
        "Female students have a slightly higher average score.",
        "female students have a slightly higher average score",
        query="Which gender has the higher average score?",
        llm_config=object(),
    )

    assert covered is True
    assert debug["source"] == "lexical_fallback"


def test_runtime_builds_follow_up_in_query_language(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    monkeypatch.setattr(
        runtime,
        "_llm_json",
        lambda llm_config, messages: {"follow_up": "\u8bf7\u53ea\u8865\u5145\u7f3a\u5931\u90e8\u5206\uff0c\u4e0d\u8981\u91cd\u8ff0\u6574\u5f20\u8868\u3002"},
    )

    follow_up = runtime._build_follow_up(
        "\u8bf7\u7ee7\u7eed\u5206\u6790\u54ea\u4e2a\u6027\u522b\u5e73\u5747\u5206\u66f4\u9ad8",
        ["\u8bf4\u660e\u54ea\u4e2a\u6027\u522b\u5e73\u5747\u5206\u66f4\u9ad8", "\u89e3\u91ca\u5dee\u8ddd\u662f\u5426\u660e\u663e"],
        llm_config=object(),
    )

    assert follow_up is not None
    assert follow_up.startswith("\u8bf7")
    assert "\u6574\u5f20\u8868" in follow_up


def test_runtime_falls_back_to_chinese_follow_up_template_when_llm_fails(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    monkeypatch.setattr(runtime, "_llm_json", lambda llm_config, messages: (_ for _ in ()).throw(RuntimeError("boom")))

    follow_up = runtime._build_follow_up(
        "\u8bf7\u7ee7\u7eed\u5206\u6790\u54ea\u4e2a\u6027\u522b\u5e73\u5747\u5206\u66f4\u9ad8",
        ["\u8bf4\u660e\u54ea\u4e2a\u6027\u522b\u5e73\u5747\u5206\u66f4\u9ad8", "\u89e3\u91ca\u5dee\u8ddd\u662f\u5426\u660e\u663e"],
        llm_config=object(),
    )

    assert follow_up is not None
    assert follow_up.startswith("\u8bf7\u53ea\u57fa\u4e8e\u540c\u4e00\u4e2a dataframe")
