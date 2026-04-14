from __future__ import annotations

import json

import pandas as pd
from matplotlib.axes import Axes

from databao.agent.core.executor import ExecutionResult
from databao.agent.visualizers.seaborn_chat import ChartPlan, SeabornChatVisualizer


def test_horizontal_bar_contract_passes_with_llm_json_plan(monkeypatch) -> None:
    visualizer = SeabornChatVisualizer(llm_config=object())
    dataframe = pd.DataFrame(
        {
            "PaymentMethod": ["Bank", "Card", "Bank", "Card"],
            "customer_count": [12, 8, 7, 5],
        }
    )
    plan = {
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
    monkeypatch.setattr(visualizer, "_call_chart_planner", lambda messages: json.dumps(plan, ensure_ascii=False))

    result = visualizer.visualize(
        "按 PaymentMethod 分组，统计客户数量，画横向柱状图，横轴 customer_count，纵轴 PaymentMethod",
        ExecutionResult(text="ok", meta={}, df=dataframe),
    )

    assert result.kind == "barplot"
    assert result.meta["planner"] == "llm_json"
    assert result.meta["validated"] is True
    assert result.plot_config["orientation"] == "horizontal"
    assert result.png_bytes() is not None


def test_percent_stacked_plan_requires_plot_ready_percentage_df() -> None:
    visualizer = SeabornChatVisualizer()
    dataframe = pd.DataFrame(
        {
            "Contract": ["Month-to-month", "Month-to-month", "Two year", "Two year"],
            "Churn": ["Yes", "No", "Yes", "No"],
            "pct": [40.0, 60.0, 20.0, 80.0],
        }
    )

    raw_plan = ChartPlan(
        kind="barplot",
        x="Contract",
        y="pct",
        hue="Churn",
        value="pct",
        orientation="vertical",
        stack_mode="percent_stacked",
        normalize_mode="percent_of_group",
        source_df_role="raw",
        confidence="high",
    )
    raw_errors = visualizer._validate_chart_plan("100% stacked", dataframe, raw_plan)
    assert any("source_df_role" in error for error in raw_errors)

    plot_ready_plan = raw_plan.model_copy(update={"source_df_role": "plot_ready"})
    plot_ready_errors = visualizer._validate_chart_plan("100% stacked", dataframe, plot_ready_plan)
    assert plot_ready_errors == []


def test_boxplot_explicit_groups_not_dropped(monkeypatch) -> None:
    visualizer = SeabornChatVisualizer(llm_config=object())
    dataframe = pd.DataFrame(
        {
            "Churn": ["Yes", "No", "Yes", "No", "Yes", "No"],
            "TotalCharges": [100.0, 80.0, 130.0, 90.0, 140.0, 95.0],
        }
    )
    plan = {
        "kind": "boxplot",
        "x": "Churn",
        "y": "TotalCharges",
        "hue": None,
        "value": None,
        "orientation": None,
        "stack_mode": "none",
        "normalize_mode": "none",
        "category_order": [],
        "show_value_labels": False,
        "explicit_fields": {"requested_x": "Churn", "requested_y": "TotalCharges"},
        "confidence": "high",
    }
    monkeypatch.setattr(visualizer, "_call_chart_planner", lambda messages: json.dumps(plan, ensure_ascii=False))

    result = visualizer.visualize(
        "比较 Churn=Yes 和 Churn=No 两组的 TotalCharges 箱线图",
        ExecutionResult(text="ok", meta={}, df=dataframe),
    )

    figure = result.figure()
    assert figure is not None
    ax = figure if isinstance(figure, Axes) else figure.axes[0]
    tick_labels = {label.get_text() for label in ax.get_xticklabels() if label.get_text()}
    assert {"Yes", "No"}.issubset(tick_labels)
    assert result.kind == "boxplot"
    assert result.png_bytes() is not None


def test_g4_grouped_bar_to_horizontal_grouped_bar_keeps_hue_contract(monkeypatch) -> None:
    visualizer = SeabornChatVisualizer(llm_config=object())
    grouped_df = pd.DataFrame(
        {
            "PaymentMethod": ["Bank transfer", "Bank transfer", "Credit card", "Credit card"],
            "Churn": ["Yes", "No", "Yes", "No"],
            "customer_count": [36, 64, 52, 48],
        }
    )
    plan = {
        "kind": "barplot",
        "x": "customer_count",
        "y": "PaymentMethod",
        "hue": "Churn",
        "value": "customer_count",
        "orientation": "horizontal",
        "stack_mode": "none",
        "normalize_mode": "none",
        "category_order": ["Bank transfer", "Credit card"],
        "show_value_labels": True,
        "explicit_fields": {
            "requested_orientation": "horizontal",
            "requested_hue": "Churn",
            "requested_x": "customer_count",
            "requested_y": "PaymentMethod",
        },
        "confidence": "high",
    }
    monkeypatch.setattr(visualizer, "_call_chart_planner", lambda messages: json.dumps(plan, ensure_ascii=False))

    result = visualizer.visualize(
        "基于同一个分组结果改成横向分组柱状图，横轴客户数量，纵轴PaymentMethod，颜色按Churn",
        ExecutionResult(text="ok", meta={}, df=grouped_df),
    )

    assert result.kind == "barplot"
    assert result.meta["planner"] == "llm_json"
    assert result.meta["validated"] is True
    assert result.plot_config["orientation"] == "horizontal"
    assert result.plot_config["hue"] == "Churn"
    assert result.meta["chart_debug"]["hue_used"] is True


def test_g5_non_percent_stacked_phrase_does_not_force_percent_stack_mode() -> None:
    visualizer = SeabornChatVisualizer()
    grouped_df = pd.DataFrame(
        {
            "Contract": ["Month-to-month", "Month-to-month"],
            "Churn": ["Yes", "No"],
            "customer_count": [30, 70],
        }
    )

    explicit = visualizer._extract_explicit_field_constraints(
        "画一个普通分组柱状图（不是100%堆叠），横轴：Contract，纵轴：客户数量，颜色：Churn",
        grouped_df,
    )

    assert explicit.get("stack_mode") != "percent_stacked"
    assert explicit.get("normalize_mode") != "percent_of_group"


def test_g5_same_grouped_result_can_render_percent_stacked_view(monkeypatch) -> None:
    visualizer = SeabornChatVisualizer(llm_config=object())
    percent_df = pd.DataFrame(
        {
            "Contract": ["Month-to-month", "Month-to-month", "One year", "One year"],
            "Churn": ["Yes", "No", "Yes", "No"],
            "ratio_pct": [42.0, 58.0, 19.0, 81.0],
        }
    )
    plan = {
        "kind": "barplot",
        "x": "Contract",
        "y": "ratio_pct",
        "hue": "Churn",
        "value": "ratio_pct",
        "orientation": "vertical",
        "stack_mode": "percent_stacked",
        "normalize_mode": "percent_of_group",
        "category_order": ["Month-to-month", "One year"],
        "show_value_labels": True,
        "explicit_fields": {"requested_stack_mode": "percent_stacked", "requested_hue": "Churn"},
        "confidence": "high",
        "source_df_role": "plot_ready",
    }
    monkeypatch.setattr(visualizer, "_call_chart_planner", lambda messages: json.dumps(plan, ensure_ascii=False))

    result = visualizer.visualize(
        "不要重新筛选，基于同一个分组结果画100%堆叠柱状图，横轴Contract，纵轴百分比，颜色Churn",
        ExecutionResult(text="ok", meta={}, df=percent_df),
    )

    assert result.kind == "barplot"
    assert result.meta["planner"] == "llm_json"
    assert result.meta["validated"] is True
    assert result.plot_config["stack_mode"] == "percent_stacked"
    assert result.plot_config["normalize_mode"] == "percent_of_group"
    assert result.meta["chart_debug"]["stack_mode"] == "percent_stacked"
