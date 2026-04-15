from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pandas as pd
import pytest
from matplotlib import pyplot as plt

from databao.agent import api as bao_api
from databao.agent.core.executor import ExecutionResult
from databao.agent.visualizers.seaborn_chat import SeabornChatResult, SeabornChatVisualizer, _plot_like


def test_seaborn_chat_result_png_exports() -> None:
    visualizer = SeabornChatVisualizer()
    result = visualizer.visualize(
        "show a bar chart by category",
        ExecutionResult(
            text="ok",
            meta={},
            df=pd.DataFrame({"category": ["A", "B", "A"], "value": [1, 2, 3]}),
        ),
    )

    assert isinstance(result, SeabornChatResult)
    png_bytes = result.png_bytes()
    assert png_bytes is not None
    assert png_bytes.startswith(b"\x89PNG")

    png_base64 = result.png_base64()
    assert png_base64 is not None
    decoded = base64.b64decode(png_base64)
    assert decoded.startswith(b"\x89PNG")
    assert result.image() is not None
    assert result.meta["plot_backend"] == "seaborn"
    assert result.meta["plot_kind"] is not None


def test_agent_defaults_to_seaborn_visualizer(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            captured["visualizer"] = kwargs["visualizer"]

    monkeypatch.setattr(bao_api, "Agent", FakeAgent)
    bao_api.agent(object(), data_executor=object())

    assert isinstance(captured["visualizer"], SeabornChatVisualizer)


def test_llm_chart_request_json_parses_for_horizontal_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    visualizer = SeabornChatVisualizer(llm_config=object())
    dataframe = pd.DataFrame({"PaymentMethod": ["Bank", "Card"], "customer_count": [10, 8]})

    plan_json = {
        "kind": "barplot",
        "x": "customer_count",
        "y": "PaymentMethod",
        "hue": None,
        "value": "customer_count",
        "orientation": "horizontal",
        "stack_mode": "none",
        "normalize_mode": "none",
        "category_order": [],
        "show_value_labels": True,
        "explicit_fields": {"requested_orientation": "horizontal"},
        "confidence": "high",
    }
    monkeypatch.setattr(visualizer, "_call_chart_planner", lambda messages: json.dumps(plan_json, ensure_ascii=False))

    plan, planning_error = visualizer._build_chart_plan(
        "按 PaymentMethod 画横向柱状图",
        dataframe,
        visualizer._profile(dataframe),
        dataframe_role="aggregated",
    )

    assert planning_error is None
    assert plan is not None
    assert plan.orientation == "horizontal"
    assert plan.planner_source == "llm_json"
    assert plan.kind == "barplot"


def test_llm_chart_request_json_parses_for_grouped_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    visualizer = SeabornChatVisualizer(llm_config=object())
    dataframe = pd.DataFrame(
        {
            "Contract": ["Month-to-month", "Month-to-month", "Two year", "Two year"],
            "Churn": ["Yes", "No", "Yes", "No"],
            "ratio": [40.0, 60.0, 20.0, 80.0],
        }
    )

    plan_json = {
        "kind": "barplot",
        "x": "Contract",
        "y": "ratio",
        "hue": "Churn",
        "value": "ratio",
        "orientation": "vertical",
        "stack_mode": "percent_stacked",
        "normalize_mode": "percent_of_group",
        "category_order": [],
        "show_value_labels": True,
        "explicit_fields": {"requested_hue": "Churn", "requested_stack_mode": "percent_stacked"},
        "confidence": "high",
    }
    monkeypatch.setattr(visualizer, "_call_chart_planner", lambda messages: json.dumps(plan_json, ensure_ascii=False))

    plan, planning_error = visualizer._build_chart_plan(
        "按 Contract 和 Churn 做 100% 堆叠柱状图",
        dataframe,
        visualizer._profile(dataframe),
        dataframe_role="plot_ready",
    )

    assert planning_error is None
    assert plan is not None
    assert plan.hue == "Churn"
    assert plan.stack_mode == "percent_stacked"


def test_llm_chart_request_rejects_unknown_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    visualizer = SeabornChatVisualizer(llm_config=object())
    dataframe = pd.DataFrame({"PaymentMethod": ["Bank", "Card"], "customer_count": [10, 8]})

    bad_plan_json = {
        "kind": "barplot",
        "x": "missing_x",
        "y": "PaymentMethod",
        "hue": None,
        "value": "customer_count",
        "orientation": "horizontal",
        "stack_mode": "none",
        "normalize_mode": "none",
        "category_order": [],
        "show_value_labels": True,
        "explicit_fields": {},
        "confidence": "low",
    }
    monkeypatch.setattr(visualizer, "_call_chart_planner", lambda messages: json.dumps(bad_plan_json, ensure_ascii=False))

    plan, planning_error = visualizer._build_chart_plan(
        "按 PaymentMethod 画横向柱状图",
        dataframe,
        visualizer._profile(dataframe),
        dataframe_role="aggregated",
    )

    assert plan is None
    assert planning_error is not None
    assert planning_error.error_code == "schema_parse_failed"


def test_llm_json_plan_does_not_get_semantically_rewritten_by_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    visualizer = SeabornChatVisualizer(llm_config=object())
    dataframe = pd.DataFrame({"PaymentMethod": ["Bank", "Card"], "customer_count": [10, 8]})

    plan_json = {
        "kind": "barplot",
        "x": "customer_count",
        "y": "PaymentMethod",
        "hue": None,
        "value": "customer_count",
        "orientation": "horizontal",
        "stack_mode": "none",
        "normalize_mode": "none",
        "category_order": [],
        "show_value_labels": True,
        "explicit_fields": {},
        "confidence": "high",
    }
    monkeypatch.setattr(visualizer, "_call_chart_planner", lambda messages: json.dumps(plan_json, ensure_ascii=False))
    monkeypatch.setattr(visualizer, "_render", lambda kind, df, columns: (_ for _ in ()).throw(RuntimeError("boom")))

    result = visualizer.visualize(
        "按 PaymentMethod 分组，画横向柱状图，横轴 customer_count",
        ExecutionResult(text="ok", meta={}, df=dataframe),
    )

    assert result.meta["planner"] == "llm_json"
    assert result.meta["planner_status"] == "render_failed"
    assert result.meta["fallback_blocked"] is True
    assert result.plot is None
    assert result.kind == "barplot"
    assert result.plot_config["orientation"] == "horizontal"
    assert result.plot_config["stack_mode"] == "none"
    assert result.chart_plan["kind"] == "barplot"
    assert result.meta["chart_plan"]["orientation"] == "horizontal"


def test_plot_like_handles_figure_and_axes_without_requested_bug() -> None:
    figure, axis = plt.subplots()
    try:
        assert _plot_like(figure) is figure
        assert _plot_like(axis) is axis
        wrapper = SimpleNamespace(figure=figure)
        assert _plot_like(wrapper) is figure
    finally:
        plt.close(figure)


def test_extract_category_order_is_compatible_with_pandas3_stack() -> None:
    visualizer = SeabornChatVisualizer()
    dataframe = pd.DataFrame(
        {
            "PaymentMethod": ["Bank transfer", "Credit card", "Mailed check"],
            "customer_count": [12, 10, 8],
        }
    )

    order = visualizer._extract_category_order("排序: Credit card, Bank transfer, Mailed check", dataframe)

    assert order == ["Credit card", "Bank transfer", "Mailed check"]


def test_extract_explicit_constraints_maps_100_percent_stacked_semantics() -> None:
    visualizer = SeabornChatVisualizer()
    dataframe = pd.DataFrame(
        {
            "Contract": ["Month-to-month", "One year", "Two year"],
            "Churn": ["Yes", "No", "No"],
            "pct": [40.0, 20.0, 10.0],
        }
    )

    explicit = visualizer._extract_explicit_field_constraints("请画 100% 堆叠柱状图，横轴：Contract，颜色：Churn", dataframe)

    assert explicit["stack_mode"] == "percent_stacked"
    assert explicit["normalize_mode"] == "percent_of_group"


def test_result_exposes_serializable_chart_plan() -> None:
    visualizer = SeabornChatVisualizer()
    result = visualizer.visualize(
        "show a histogram with x=value",
        ExecutionResult(text="ok", meta={}, df=pd.DataFrame({"value": [1, 2, 3, 4]})),
    )

    assert result.chart_plan["kind"] == "histogram"
    assert result.meta["chart_plan"] == result.chart_plan


def test_explicit_percent_stacked_request_blocks_semantic_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    visualizer = SeabornChatVisualizer(llm_config=object())
    dataframe = pd.DataFrame(
        {
            "Contract": ["Month-to-month", "Month-to-month", "One year", "One year"],
            "Churn": ["Yes", "No", "Yes", "No"],
            "pct": [45.0, 55.0, 20.0, 80.0],
        }
    )
    monkeypatch.setattr(visualizer, "_call_chart_planner", lambda messages: "{}")

    result = visualizer.visualize(
        "按 Contract 和 Churn 分组，画 100% 堆叠柱状图，横轴：Contract，纵轴：百分比，颜色：Churn",
        ExecutionResult(text="ok", meta={}, df=dataframe),
    )

    assert result.meta["planner"] == "llm_json"
    assert result.meta["fallback_blocked"] is True
    assert result.meta["planner_status"] in {"planning_failed", "schema_parse_failed", "validation_failed"}
    assert result.meta["planner_source"] == "llm_json"
