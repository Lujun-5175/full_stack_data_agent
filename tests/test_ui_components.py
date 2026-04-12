from __future__ import annotations

from types import SimpleNamespace

from full_stack_data_agent.ui import components


def test_render_chart_from_response_uses_plot_spec_fallback(monkeypatch) -> None:
    rendered: list[object] = []
    monkeypatch.setattr(components.st, "altair_chart", lambda chart, use_container_width=True: rendered.append(chart))
    monkeypatch.setattr(components.st, "error", lambda message: None)

    result = SimpleNamespace(
        last_databao_result={
            "plot_spec": {
                "mark": "bar",
                "encoding": {
                    "x": {"field": "category", "type": "nominal"},
                    "y": {"field": "value", "type": "quantitative"},
                },
            },
            "plot_data": [{"category": "A", "value": 1}, {"category": "B", "value": 2}],
        }
    )
    chart_debug: dict[str, object] = {}

    success = components._render_chart_from_response(result, chart_debug=chart_debug)

    assert success is True
    assert rendered
    assert chart_debug["chart_renderer"] == "altair_chart_from_spec"


def test_render_chart_from_response_records_missing_history_payload(monkeypatch) -> None:
    monkeypatch.setattr(components.st, "error", lambda message: None)

    result = SimpleNamespace(last_databao_result={"plot_spec": {"mark": "line"}, "plot_data": None})
    chart_debug: dict[str, object] = {}

    success = components._render_chart_from_response(result, chart_debug=chart_debug)

    assert success is False
    assert chart_debug["chart_failure_stage"] == "history_payload_validation"
    assert "plot_data=missing" in str(chart_debug["chart_failure_reason"])
