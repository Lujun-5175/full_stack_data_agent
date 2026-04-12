from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import altair as alt
import pandas as pd
from langchain_core.messages import HumanMessage

from full_stack_data_agent.app.chat_service import ChatService
from full_stack_data_agent.app.databao_runtime import DatabaoRuntime
from full_stack_data_agent.app.runtime_models import DatabaoSessionSnapshot
from full_stack_data_agent.app.runtime_models import DatabaoTurnResult
from full_stack_data_agent.config.settings import get_settings
from full_stack_data_agent.app.runtime_models import RegisteredTable
from full_stack_data_agent.context.models import ConversationMessage, ConversationState, ConversationTurn
from full_stack_data_agent.ui.components import _render_chart_from_response, render_conversation_history, render_result_panel
from full_stack_data_agent.ui.components import _registered_table_lookup


class _FakePlotResult:
    def __init__(self, dataframe: pd.DataFrame):
        self.code = '{"mark":"bar"}'
        self.meta = {"kind": "bar"}
        self.spec = {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "mark": "bar",
            "encoding": {
                "x": {"field": "grade", "type": "nominal"},
                "y": {"aggregate": "count", "type": "quantitative"},
            },
        }
        self.spec_df = dataframe


class _PlotObjectWithChartMethod:
    def __init__(self, chart: alt.Chart) -> None:
        self._chart = chart

    def to_altair_chart(self) -> alt.Chart:
        return self._chart


class _PlotObjectWithPlotAttribute:
    def __init__(self, chart: alt.Chart) -> None:
        self.plot = chart


class _FakeThread:
    def __init__(
        self,
        dataframe: pd.DataFrame | None = None,
        *,
        initial_text: str,
        follow_up_text: str | None = None,
        plot_factory=None,
        ask_error: Exception | None = None,
    ):
        self._dataframe = dataframe if dataframe is not None else pd.DataFrame(
            [{"borough": "Queens", "salary": 100}, {"borough": "Bronx", "salary": 80}]
        )
        self._initial_text = initial_text
        self._follow_up_text = follow_up_text if follow_up_text is not None else initial_text
        self._plot_factory = plot_factory
        self._ask_error = ask_error
        self._text_mode = "initial"
        self.queries: list[str] = []
        self.plot_calls: list[str | None] = []

    def ask(self, query: str):
        if self._ask_error is not None:
            raise self._ask_error
        self.queries.append(query)
        lowered = query.lower()
        if "missing sub-questions" in lowered or "do not restate the full table" in lowered:
            self._text_mode = "follow_up"
        else:
            self._text_mode = "initial"
        return self

    def df(self, rows_limit=None):
        return self._dataframe

    def text(self) -> str:
        return self._follow_up_text if self._text_mode == "follow_up" else self._initial_text

    def meta(self) -> dict[str, object]:
        messages = [HumanMessage(content=query) for query in self.queries]
        return {"messages": messages, "executor": "lighthouse"}

    def plot(self, request=None, **kwargs):
        self.plot_calls.append(request)
        if self._plot_factory is None:
            raise AssertionError("plot() should not have been called")
        return self._plot_factory(request)


def _load_student_data() -> pd.DataFrame:
    csv_path = Path(__file__).resolve().parents[1] / "data" / "student_performance_data.csv"
    return pd.read_csv(csv_path)


def _make_runtime(thread: _FakeThread) -> DatabaoRuntime:
    runtime = DatabaoRuntime(get_settings())
    runtime._ensure_runtime_ready = lambda: None  # type: ignore[method-assign]
    session = SimpleNamespace(
        conversation_id="session-1",
        thread=thread,
        agent=SimpleNamespace(llm_config=SimpleNamespace(name="ollama:gemma4:e4b")),
        registered_tables=[],
        context_build_error=None,
        datasource_changed=False,
        thread_reset_reason=None,
    )
    runtime._ensure_session = lambda *args, **kwargs: session  # type: ignore[method-assign]
    return runtime


def _deliverable_query() -> str:
    return (
        "Please summarize different grade groups and answer the following explicit deliverables: "
        "\u4f60\u6700\u7ec8\u81f3\u5c11\u8981\u56de\u7b54\uff1a"
        "\u54ea\u4e2a grade \u7684\u5b66\u751f\u4eba\u6570\u6700\u591a\uff1f"
        "\u54ea\u4e2a grade \u7684\u5e73\u5747\u5b66\u4e60\u65f6\u957f\u6700\u9ad8\uff1f"
        "\u6210\u7ee9\u7b49\u7ea7\u548c\u51fa\u52e4\u7387\u662f\u5426\u5927\u81f4\u540c\u5411\u53d8\u5316\uff1f"
    )


def test_completion_validator_asks_follow_up_for_missing_deliverables() -> None:
    runtime = DatabaoRuntime(get_settings())
    dataframe = _load_student_data()
    query = _deliverable_query()
    thread = _FakeThread(
        dataframe,
        initial_text="\u8be5\u8868\u5c55\u793a\u4e86\u4e0d\u540c grade \u7684\u805a\u5408\u7ed3\u679c\u3002",
        follow_up_text=(
            "grade C \u7684\u5b66\u751f\u4eba\u6570\u6700\u591a\uff0c"
            "grade D \u7684\u5e73\u5747\u5b66\u4e60\u65f6\u957f\u6700\u9ad8\uff0c"
            "\u6210\u7ee9\u7b49\u7ea7\u548c\u51fa\u52e4\u7387\u6574\u4f53\u5927\u81f4\u540c\u5411\u53d8\u5316\u3002"
        ),
    )

    text, validation = runtime._complete_response(thread, query, thread.text(), dataframe)

    assert validation["supplement_added"] is True
    assert validation["follow_up_query"]
    assert len(thread.queries) == 1
    assert "\u5b66\u751f\u4eba\u6570\u6700\u591a" in text
    assert "\u5e73\u5747\u5b66\u4e60\u65f6\u957f\u6700\u9ad8" in text
    assert "\u540c\u5411\u53d8\u5316" in text


def test_completion_validator_skips_when_initial_answer_already_covers_deliverables() -> None:
    runtime = DatabaoRuntime(get_settings())
    dataframe = _load_student_data()
    query = _deliverable_query()
    answer = (
        "grade C \u7684\u5b66\u751f\u4eba\u6570\u6700\u591a\uff0c"
        "grade D \u7684\u5e73\u5747\u5b66\u4e60\u65f6\u957f\u6700\u9ad8\uff0c"
        "\u6210\u7ee9\u7b49\u7ea7\u548c\u51fa\u52e4\u7387\u6574\u4f53\u5927\u81f4\u540c\u5411\u53d8\u5316\u3002"
    )
    thread = _FakeThread(dataframe, initial_text=answer, follow_up_text="should not be used")

    text, validation = runtime._complete_response(thread, query, answer, dataframe)

    assert validation["supplement_added"] is False
    assert validation["missing_deliverables"] == []
    assert thread.queries == []
    assert text == answer


def test_completion_validator_skips_without_explicit_deliverables() -> None:
    runtime = DatabaoRuntime(get_settings())
    dataframe = _load_student_data()
    query = "Please summarize grade groups by count and mean score."
    thread = _FakeThread(dataframe, initial_text="\u53ea\u6709\u6982\u89c8\u56de\u7b54")

    text, validation = runtime._complete_response(thread, query, thread.text(), dataframe)

    assert validation["supplement_added"] is False
    assert validation["explicit_deliverables"] == []
    assert thread.queries == []
    assert text == "\u53ea\u6709\u6982\u89c8\u56de\u7b54"


def test_completion_validator_reports_follow_up_failure() -> None:
    runtime = DatabaoRuntime(get_settings())
    dataframe = _load_student_data()
    query = _deliverable_query()
    thread = _FakeThread(
        dataframe,
        initial_text="\u8be5\u8868\u5c55\u793a\u4e86\u4e0d\u540c grade \u7684\u805a\u5408\u7ed3\u679c\u3002",
        ask_error=RuntimeError("follow-up failed"),
    )

    text, validation = runtime._complete_response(thread, query, thread.text(), dataframe)

    assert validation["supplement_added"] is False
    assert validation["follow_up_error"] == "follow-up failed"
    assert text == "\u8be5\u8868\u5c55\u793a\u4e86\u4e0d\u540c grade \u7684\u805a\u5408\u7ed3\u679c\u3002"


def test_analysis_question_does_not_auto_visualize() -> None:
    dataframe = pd.DataFrame(
        {
            "grade": ["A", "B", "C"],
            "overall_score": [90, 80, 70],
            "study_hours_per_day": [4.0, 5.0, 6.0],
        }
    )
    thread = _FakeThread(dataframe, initial_text="summary")
    runtime = _make_runtime(thread)

    result_obj, _snapshot = runtime.ask(
        "session-1",
        "Please summarize the grade groups, average overall score, average study hours per day, and average attendance percentage, ordered by average overall score.",
        uploaded_contexts=[],
    )

    assert thread.plot_calls == []
    assert result_obj.plot_spec is None
    assert result_obj.plot_data is None
    assert runtime._has_explicit_chart_intent("Please summarize the grade groups, average overall score.") is False
    assert runtime._maybe_collect_plot(thread, "Please summarize the grade groups, average overall score.") == (
        None,
        None,
    )


def test_chart_request_collects_chart_artifact() -> None:
    dataframe = _load_student_data()
    thread = _FakeThread(
        dataframe,
        initial_text="chart ready",
        plot_factory=lambda _request: _FakePlotResult(dataframe.groupby("grade", as_index=False).size()),
    )
    runtime = _make_runtime(thread)

    result, _snapshot = runtime.ask(
        "session-1",
        "study_hours_per_day distribution chart, split into four charts by grade",
        uploaded_contexts=[],
    )

    assert thread.plot_calls == ["study_hours_per_day distribution chart, split into four charts by grade"]
    assert result.plot_spec is not None
    assert result.plot_data is not None
    assert result.plot_code == '{"mark":"bar"}'
    assert result.plot_error is None
    assert result.chart_debug["chart_requested"] is True
    assert result.chart_debug["chart_generation_called"] is True
    assert result.chart_debug["chart_generated"] is True
    assert result.chart_debug["chart_renderable"] is True
    assert result.chart_debug["plot_spec_present"] is True
    assert result.chart_debug["plot_data_rows"] == len(result.plot_data)
    assert result.chart_debug["chart_saved_to_history"] is True
    assert result.chart_debug["chart_artifact_id"]


def test_single_filtered_chart_preserves_filter_and_render_payload() -> None:
    dataframe = _load_student_data()

    def plot_factory(request: str | None):
        assert request is not None
        filtered = dataframe.loc[dataframe["grade"] == "C", ["grade", "overall_score"]].copy()
        return _FakePlotResult(filtered)

    thread = _FakeThread(dataframe, initial_text="filtered chart ready", plot_factory=plot_factory)
    runtime = _make_runtime(thread)

    result, _snapshot = runtime.ask(
        "session-1",
        "Please plot the overall_score distribution for students where grade is C.",
        uploaded_contexts=[],
    )

    assert thread.plot_calls == ["Please plot the overall_score distribution for students where grade is C."]
    assert result.plot_data is not None
    assert {row["grade"] for row in result.plot_data} == {"C"}
    assert all("overall_score" in row for row in result.plot_data)


def test_result_panel_renders_plot_object_via_to_altair_chart(monkeypatch) -> None:
    calls: dict[str, object] = {}

    monkeypatch.setattr("streamlit.markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.code", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.error", lambda *args, **kwargs: calls.setdefault("error", args[0] if args else ""))
    monkeypatch.setattr("streamlit.altair_chart", lambda chart, **kwargs: calls.setdefault("chart", chart))

    class _DummyExpander:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("streamlit.expander", lambda *args, **kwargs: _DummyExpander())

    chart = alt.Chart(pd.DataFrame([{"grade": "C", "overall_score": 72.5}])).mark_bar()
    response = DatabaoTurnResult(
        text="chart answer",
        dataframe_preview=[{"grade": "C", "overall_score": 72.5}],
        columns=["grade", "overall_score"],
        row_count=1,
        plot_code='{"mark":"bar"}',
        plot_object=_PlotObjectWithChartMethod(chart),
    )

    render_result_panel(SimpleNamespace(last_databao_result=response))

    assert calls["chart"].to_dict()["mark"]["type"] == "bar"
    assert calls["chart"].to_dict()["height"] == 360
    assert "error" not in calls


def test_result_panel_renders_plot_object_via_plot_attribute(monkeypatch) -> None:
    calls: dict[str, object] = {}

    monkeypatch.setattr("streamlit.markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.code", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.error", lambda *args, **kwargs: calls.setdefault("error", args[0] if args else ""))
    monkeypatch.setattr("streamlit.altair_chart", lambda chart, **kwargs: calls.setdefault("chart", chart))

    class _DummyExpander:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("streamlit.expander", lambda *args, **kwargs: _DummyExpander())

    chart = alt.Chart(pd.DataFrame([{"grade": "C", "overall_score": 72.5}])).mark_bar()
    response = DatabaoTurnResult(
        text="chart answer",
        dataframe_preview=[{"grade": "C", "overall_score": 72.5}],
        columns=["grade", "overall_score"],
        row_count=1,
        plot_code='{"mark":"bar"}',
        plot_object=_PlotObjectWithPlotAttribute(chart),
    )

    render_result_panel(SimpleNamespace(last_databao_result=response))

    assert calls["chart"].to_dict()["mark"]["type"] == "bar"
    assert calls["chart"].to_dict()["height"] == 360
    assert "error" not in calls


def test_result_panel_falls_back_to_plot_spec_and_data(monkeypatch) -> None:
    calls: dict[str, object] = {}

    monkeypatch.setattr("streamlit.markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.code", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.error", lambda *args, **kwargs: calls.setdefault("error", args[0] if args else ""))
    monkeypatch.setattr("streamlit.altair_chart", lambda chart, **kwargs: calls.setdefault("chart", chart))

    class _DummyExpander:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("streamlit.expander", lambda *args, **kwargs: _DummyExpander())

    response = DatabaoTurnResult(
        text="chart answer",
        dataframe_preview=[{"grade": "C", "overall_score": 72.5}],
        columns=["grade", "overall_score"],
        row_count=1,
        plot_code='{"mark":"bar"}',
        plot_spec={
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "mark": "bar",
            "encoding": {
                "x": {"field": "grade", "type": "nominal"},
                "y": {"field": "overall_score", "type": "quantitative"},
            },
        },
        plot_data=[{"grade": "C", "overall_score": 72.5}],
    )

    render_result_panel(SimpleNamespace(last_databao_result=response))

    assert "chart" in calls
    assert calls["chart"].to_dict()["height"] == 360
    assert "error" not in calls


def test_result_panel_returns_false_when_fallback_payload_is_incomplete(monkeypatch) -> None:
    monkeypatch.setattr("streamlit.markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.code", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.error", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.altair_chart", lambda *args, **kwargs: None)

    class _DummyExpander:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("streamlit.expander", lambda *args, **kwargs: _DummyExpander())

    response = DatabaoTurnResult(
        text="chart answer",
        plot_code='{"mark":"bar"}',
        plot_spec={"mark": "bar"},
        plot_data=None,
    )

    assert _render_chart_from_response(SimpleNamespace(last_databao_result=response)) is False


def test_result_panel_surfaces_renderer_failure(monkeypatch) -> None:
    errors: list[str] = []

    monkeypatch.setattr("streamlit.markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.code", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.error", lambda message, **kwargs: errors.append(str(message)))
    monkeypatch.setattr("streamlit.altair_chart", lambda *args, **kwargs: None)

    class _DummyExpander:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("streamlit.expander", lambda *args, **kwargs: _DummyExpander())

    response = DatabaoTurnResult(
        text="chart answer",
        plot_object=type(
            "BrokenPlotObject",
            (),
            {
                "to_altair_chart": lambda self: (_ for _ in ()).throw(ValueError("broken chart object")),
            },
        )(),
    )

    assert _render_chart_from_response(SimpleNamespace(last_databao_result=response)) is False
    assert any("Chart render failed" in message for message in errors)


def test_result_panel_warns_on_plot_error(monkeypatch) -> None:
    warnings: list[str] = []

    monkeypatch.setattr("streamlit.markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.code", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.warning", lambda message, **kwargs: warnings.append(str(message)))
    monkeypatch.setattr("streamlit.altair_chart", lambda *args, **kwargs: None)

    class _DummyExpander:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("streamlit.expander", lambda *args, **kwargs: _DummyExpander())

    response = DatabaoTurnResult(
        text="chart answer",
        plot_code='{"mark":"bar"}',
        plot_spec={"mark": "bar"},
        plot_data=[{"grade": "C", "overall_score": 72.5}],
        plot_error="broken chart payload",
        chart_debug={"chart_requested": True, "chart_failure_stage": "generation_failed"},
    )

    render_result_panel(SimpleNamespace(last_databao_result=response))

    assert any("Chart generation failed" in warning for warning in warnings)


def test_conversation_history_warns_on_plot_error(monkeypatch) -> None:
    warnings: list[str] = []

    monkeypatch.setattr("streamlit.markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.code", lambda *args, **kwargs: None)
    monkeypatch.setattr("streamlit.warning", lambda message, **kwargs: warnings.append(str(message)))
    monkeypatch.setattr("streamlit.altair_chart", lambda *args, **kwargs: None)

    class _DummyExpander:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("streamlit.expander", lambda *args, **kwargs: _DummyExpander())

    state = ConversationState(
        conversation_id="session-warning",
        turns=[
            ConversationTurn(
                user_message=ConversationMessage(role="user", content="plot a chart"),
                assistant_message=ConversationMessage(role="assistant", content="here is the chart"),
                metadata={
                    "chart_debug": {
                        "chart_requested": True,
                        "chart_failure_stage": "generation_failed",
                        "chart_failure_reason": "broken chart payload",
                    },
                    "plot_error": "broken chart payload",
                    "plot_spec": {"mark": "bar"},
                    "plot_data": [{"grade": "C", "overall_score": 72.5}],
                },
            )
        ],
    )

    render_conversation_history(state)

    assert any("Chart generation failed" in warning for warning in warnings)


def test_chat_service_persists_chart_debug_in_conversation_snapshot(monkeypatch) -> None:
    service = ChatService(get_settings())
    state = service.create_state()

    fake_result = DatabaoTurnResult(
        text="chart answer",
        row_count=1,
        plot_code='{"mark":"bar"}',
        plot_spec={"mark": "bar"},
        plot_data=[{"grade": "C", "overall_score": 72.5}],
        chart_debug={
            "chart_requested": True,
            "chart_generated": True,
            "chart_renderable": True,
            "chart_artifact_id": "chart-123",
        },
    )
    fake_snapshot = DatabaoSessionSnapshot(
        conversation_id=state.conversation_id,
        llm_name="deepseek-chat",
        executor_type="lighthouse",
    )

    monkeypatch.setattr(service._runtime, "ask", lambda *args, **kwargs: (fake_result, fake_snapshot))

    new_state, result = service.send_message(state, "show chart", uploaded_contexts=[])

    assert result.conversation_snapshot["turns"][0]["metadata"]["chart_debug"]["chart_requested"] is True
    assert new_state.turns[0].debug_detailed["chart_debug"]["chart_artifact_id"] == "chart-123"


def test_registered_table_lookup_works_with_snapshot_object() -> None:
    table = RegisteredTable(
        name="student_data",
        source_file="student.csv",
        row_count=100,
        columns=["grade", "score"],
        description="test",
        semantic_profile={"measure_candidates": ["score"]},
    )
    snapshot = SimpleNamespace(registered_tables=[table])
    result = SimpleNamespace(last_runtime_snapshot=snapshot)

    lookup = _registered_table_lookup(result)

    assert "student.csv" in lookup
    assert lookup["student.csv"]["name"] == "student_data"
    assert lookup["student.csv"]["semantic_profile"]["measure_candidates"] == ["score"]


def test_registered_table_lookup_works_with_snapshot_dict() -> None:
    snapshot_dict = {
        "registered_tables": [
            {
                "name": "salary_data",
                "source_file": "salary.csv",
                "row_count": 50,
                "columns": ["borough", "salary"],
                "description": "test",
                "semantic_profile": {"measure_candidates": ["salary"]},
            }
        ]
    }
    result = SimpleNamespace(last_runtime_snapshot=snapshot_dict)

    lookup = _registered_table_lookup(result)

    assert "salary.csv" in lookup
    assert lookup["salary.csv"]["semantic_profile"]["measure_candidates"] == ["salary"]
