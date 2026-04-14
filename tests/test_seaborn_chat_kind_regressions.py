from __future__ import annotations

import json

import pandas as pd
import pytest

from databao.agent.core.executor import ExecutionResult
from databao.agent.visualizers.seaborn_chat import SeabornChatVisualizer


def test_detect_requested_kind_does_not_misclassify_history_as_histogram() -> None:
    visualizer = SeabornChatVisualizer(llm_config=object())

    kind = visualizer._detect_requested_kind(
        "User question history:\nshow churn history over time\nInstructions:\nshow a bar chart with x=Churn and y=customer_count"
    )

    assert kind == "barplot"


@pytest.mark.parametrize(
    ("query_text", "expected_kind"),
    [
        ("show a bar chart", "barplot"),
        ("show a histogram", "histogram"),
        ("\u753b\u4e00\u4e2a\u67f1\u72b6\u56fe", "barplot"),
        ("\u753b\u4e00\u4e2a\u76f4\u65b9\u56fe", "histogram"),
    ],
)
def test_detect_requested_kind_canonicalizes_explicit_aliases(query_text: str, expected_kind: str) -> None:
    visualizer = SeabornChatVisualizer(llm_config=object())

    assert visualizer._detect_requested_kind(query_text) == expected_kind


def test_detect_requested_kind_does_not_infer_histogram_from_generic_distribution_text() -> None:
    visualizer = SeabornChatVisualizer(llm_config=object())

    assert visualizer._detect_requested_kind("\u8bf7\u8bf4\u660e\u4eba\u6570\u5206\u5e03\u5e76\u603b\u7ed3\u5dee\u5f02") is None


def test_validation_failure_may_fallback_when_semantic_lock_not_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    visualizer = SeabornChatVisualizer(llm_config=object())
    dataframe = pd.DataFrame({"Churn": ["No", "Yes"], "customer_count": [5174, 1869]})
    plan = json.dumps(
        {
            "kind": "barplot",
            "x": "Churn",
            "y": "customer_count",
            "hue": None,
            "value": "customer_count",
            "orientation": "vertical",
            "stack_mode": "none",
            "normalize_mode": "none",
            "category_order": [],
            "show_value_labels": True,
            "explicit_fields": {},
            "confidence": "high",
        }
    )

    monkeypatch.setattr(visualizer, "_call_chart_planner", lambda messages: plan)
    monkeypatch.setattr(
        visualizer,
        "_validate_chart_plan",
        lambda request, df, plan: ["synthetic validation failure"] if plan.kind == "barplot" and plan.y == "customer_count" else [],
    )

    result = visualizer.visualize(
        "show a bar chart with x=Churn and y=customer_count",
        ExecutionResult(text="ok", meta={}, df=dataframe),
    )

    assert result.meta["planner"] == "fallback"
    assert result.meta["planner_status"] == "fallback"
    assert result.meta["fallback_blocked"] is False
    assert result.kind is not None


def test_end_to_end_pdf_case_keeps_barplot_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    visualizer = SeabornChatVisualizer(llm_config=object())
    dataframe = pd.DataFrame({"Churn": ["No Churn", "Churn"], "customer_count": [5174, 1869]})
    plan = {
        "kind": "barplot",
        "x": "Churn",
        "y": "customer_count",
        "hue": None,
        "variables": [],
        "value": None,
        "mode": None,
        "title": "Churn counts",
        "confidence": 0.98,
        "reason": "explicit bar chart request",
    }
    monkeypatch.setattr(visualizer, "_call_chart_planner", lambda messages: json.dumps(plan))

    result = visualizer.visualize(
        "User question history:\nshow churn history over time\nInstructions:\n\u8bf7\u753b\u4e00\u4e2a\u67f1\u72b6\u56fe\uff0c\u6a2a\u8f74\uff1aChurn\uff0c\u7eb5\u8f74\uff1acustomer_count",
        ExecutionResult(text="ok", meta={}, df=dataframe),
    )

    assert result.meta["planner"] == "llm_json"
    assert result.meta["planner_status"] == "ready"
    assert result.kind == "barplot"
    assert result.meta["chart_debug"]["explicit_contract_kind"] == "barplot"
    assert result.meta["chart_debug"]["parsed_plan_kind"] == "barplot"
    assert result.meta["chart_debug"]["final_plan"]["kind"] == "barplot"


def test_histogram_boolean_x_fails_fast() -> None:
    visualizer = SeabornChatVisualizer()
    dataframe = pd.DataFrame({"Churn": [True, False, True]})

    with pytest.raises(ValueError, match="histplot does not support boolean x columns"):
        visualizer._render("histogram", dataframe, {"x": "Churn", "y": None, "hue": None, "variables": []})
