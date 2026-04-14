from __future__ import annotations

from types import SimpleNamespace

from full_stack_data_agent.context.models import ConversationMessage, ConversationState, ConversationTurn
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


def test_render_chart_from_response_respects_upstream_chart_failure(monkeypatch) -> None:
    rendered: list[object] = []
    monkeypatch.setattr(components.st, "image", lambda *args, **kwargs: rendered.append("image"))

    result = SimpleNamespace(
        last_databao_result={
            "plot_image_base64": "ZmFrZQ==",
            "chart_debug": {
                "planner_status": "render_failed",
                "render_error": "boom",
                "fallback_blocked": True,
            },
        }
    )
    chart_debug = dict(result.last_databao_result["chart_debug"])

    success = components._render_chart_from_response(result, chart_debug=chart_debug)

    assert success is False
    assert rendered == []


def test_render_chart_from_response_keeps_base64_history_success_path(monkeypatch) -> None:
    rendered: list[object] = []
    monkeypatch.setattr(components.st, "image", lambda *args, **kwargs: rendered.append("image"))

    result = SimpleNamespace(last_databao_result={"plot_image_base64": "ZmFrZQ=="})
    chart_debug: dict[str, object] = {}

    success = components._render_chart_from_response(result, chart_debug=chart_debug)

    assert success is True
    assert rendered == ["image"]
    assert chart_debug["chart_renderer"] == "image_base64"


def test_history_render_uses_png_or_image_artifact_when_plot_spec_missing(monkeypatch) -> None:
    rendered: list[object] = []
    monkeypatch.setattr(components.st, "image", lambda *args, **kwargs: rendered.append("image"))

    result = SimpleNamespace(
        last_databao_result={
            "plot_meta": {"plot_image_base64": "ZmFrZQ=="},
            "plot_spec": None,
            "plot_data": None,
        }
    )
    chart_debug: dict[str, object] = {}

    success = components._render_chart_from_response(result, chart_debug=chart_debug)

    assert success is True
    assert rendered == ["image"]
    assert chart_debug["chart_renderer"] == "image_base64"
    assert chart_debug.get("chart_failure_stage") != "history_payload_validation"


def test_history_replay_prefers_image_artifact(monkeypatch) -> None:
    rendered: list[object] = []
    monkeypatch.setattr(components.st, "image", lambda *args, **kwargs: rendered.append("image"))
    monkeypatch.setattr(components.st, "altair_chart", lambda *args, **kwargs: rendered.append("altair"))

    result = SimpleNamespace(
        last_databao_result={
            "plot_image_base64": "ZmFrZQ==",
            "plot_spec": {"mark": "bar"},
            "plot_data": [{"category": "A", "value": 1}],
        }
    )
    chart_debug: dict[str, object] = {}

    success = components._render_chart_from_response(result, chart_debug=chart_debug)

    assert success is True
    assert rendered == ["image"]
    assert chart_debug["chart_renderer"] == "image_base64"


def test_render_chart_from_response_records_ui_render_failed(monkeypatch) -> None:
    monkeypatch.setattr(
        components.st,
        "altair_chart",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("ui exploded")),
    )
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
            "plot_data": [{"category": "A", "value": 1}],
        }
    )
    chart_debug: dict[str, object] = {}

    success = components._render_chart_from_response(result, chart_debug=chart_debug)

    assert success is False
    assert chart_debug["chart_failure_stage"] == "ui_render_failed"


def test_turn_ui_state_defaults_latest_turn_expanded(monkeypatch) -> None:
    monkeypatch.setattr(components.st, "session_state", {})

    first_turn = ConversationTurn(user_message=ConversationMessage(role="user", content="first"))
    second_turn = ConversationTurn(user_message=ConversationMessage(role="user", content="second"))
    state = ConversationState(turns=[first_turn, second_turn])

    components._ensure_turn_ui_state(state)

    assert components.st.session_state["turn_ui_state"][first_turn.turn_id]["expanded"] is False
    assert components.st.session_state["turn_ui_state"][second_turn.turn_id]["expanded"] is True
    assert components._turn_is_expanded(first_turn, 0, 2) is False
    assert components._turn_is_expanded(second_turn, 1, 2) is True

    components._set_turn_expanded(first_turn.turn_id, True)
    assert components.st.session_state["turn_ui_state"][first_turn.turn_id]["expanded"] is True


def test_build_message_views_flattens_turns_into_single_flow() -> None:
    turn = ConversationTurn(
        user_message=ConversationMessage(role="user", content="show revenue"),
        assistant_message=ConversationMessage(role="assistant", content="here is revenue", status="complete"),
        metadata={
            "dataframe_preview": [{"month": "Jan", "revenue": 10}],
            "columns": ["month", "revenue"],
            "row_count": 1,
            "plot_spec": {"mark": "bar"},
            "plot_data": [{"month": "Jan", "revenue": 10}],
            "completion_validation": {"ok": True},
        },
        debug_detailed={"sql": "select * from revenue"},
    )
    state = ConversationState(turns=[turn])

    views = components.build_message_views(state)

    assert [view.role for view in views] == ["user", "assistant"]
    assistant_view = views[-1]
    assert assistant_view.tables
    assert assistant_view.charts
    assert assistant_view.trace["sql"] == "select * from revenue"
    assert assistant_view.artifacts[0]["label"] == "Completion Validation"


def test_build_message_views_marks_streaming_assistant() -> None:
    turn = ConversationTurn(
        user_message=ConversationMessage(role="user", content="plot it"),
        assistant_message=ConversationMessage(role="assistant", content="working", status="streaming"),
        metadata={"plot_spec": {"mark": "line"}, "plot_data": [{"x": 1, "y": 2}]},
    )
    state = ConversationState(turns=[turn])

    views = components.build_message_views(state)

    assert views[-1].role == "assistant"
    assert views[-1].streaming is True
    assert views[-1].status == "streaming"
