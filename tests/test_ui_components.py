from __future__ import annotations

from types import SimpleNamespace

from matplotlib import pyplot as plt

from full_stack_data_agent.context.models import ConversationMessage, ConversationState, ConversationTurn
from full_stack_data_agent.ui import components


def test_render_chart_from_response_uses_workspace_artifact_first(monkeypatch) -> None:
    rendered: list[object] = []
    figure, _axis = plt.subplots()
    monkeypatch.setattr(components.st, "pyplot", lambda chart, use_container_width=True: rendered.append(chart))
    monkeypatch.setattr(components.st, "error", lambda message: None)

    result = SimpleNamespace(
        last_databao_result={
            "result_workspace": {
                "artifacts": [
                    {
                        "artifact_id": "chart:1",
                        "artifact_type": "chart",
                        "render_payload": {"render_kind": "runtime_handle"},
                        "metadata": {},
                    }
                ]
            },
            "grounded_response": {"primary_chart_artifact_id": "chart:1"},
            "result_workspace_obj": "ignored",
        }
    )
    result.last_databao_result = SimpleNamespace(
        grounded_response=SimpleNamespace(to_dict=lambda: {"primary_chart_artifact_id": "chart:1"}),
        result_workspace=SimpleNamespace(
            to_dict=lambda: result.last_databao_result_dict["result_workspace"],  # type: ignore[attr-defined]
            resolve_artifact=lambda artifact_id: SimpleNamespace(runtime_handle=lambda name: figure if name == "plot_result" else None),
        ),
    )
    result.last_databao_result_dict = {
        "result_workspace": {
            "artifacts": [{"artifact_id": "chart:1", "artifact_type": "chart", "render_payload": {"render_kind": "runtime_handle"}}]
        },
        "grounded_response": {"primary_chart_artifact_id": "chart:1"},
    }

    success = components._render_chart_from_response(result, chart_debug={})

    plt.close(figure)
    assert success is True
    assert rendered


def test_workspace_from_response_does_not_request_heavy_payload(monkeypatch) -> None:
    calls: list[tuple[tuple, dict]] = []

    class _Workspace:
        def to_dict(self, *args, **kwargs):
            calls.append((args, kwargs))
            return {"artifacts": []}

    payload = SimpleNamespace(last_databao_result=SimpleNamespace(result_workspace=_Workspace()))

    assert components._workspace_from_response(payload) == {"artifacts": []}
    assert calls == [((), {})]


def test_message_tables_use_workspace_artifact_preview_first() -> None:
    tables = components._message_tables(
        {
            "result_workspace": {
                "artifacts": [
                    {
                        "artifact_id": "filtered_df:1",
                        "artifact_type": "filtered_df",
                        "dataframe_preview": [{"month": "Jan", "revenue": 10}],
                        "row_count": 1,
                        "columns": ["month", "revenue"],
                    }
                ]
            },
            "grounded_response": {"primary_table_artifact_id": "filtered_df:1"},
        }
    )

    assert tables[0]["rows"] == [{"month": "Jan", "revenue": 10}]
    assert tables[0]["columns"] == ["month", "revenue"]


def test_render_chart_from_response_uses_base64_image(monkeypatch) -> None:
    rendered: list[object] = []
    monkeypatch.setattr(components.st, "image", lambda *args, **kwargs: rendered.append("image"))

    result = SimpleNamespace(last_databao_result={"plot_image_base64": "ZmFrZQ=="})
    chart_debug: dict[str, object] = {}

    success = components._render_chart_from_response(result, chart_debug=chart_debug)

    assert success is True
    assert rendered == ["image"]
    assert chart_debug["chart_renderer"] == "image_base64"


def test_render_chart_from_response_records_missing_history_payload(monkeypatch) -> None:
    monkeypatch.setattr(components.st, "error", lambda message: None)
    result = SimpleNamespace(last_databao_result={"plot_spec": {"mark": "line"}, "plot_data": None})
    chart_debug: dict[str, object] = {}

    success = components._render_chart_from_response(result, chart_debug=chart_debug)

    assert success is False
    assert chart_debug["chart_failure_stage"] == "history_payload_validation"
    assert "No renderable" in str(chart_debug["chart_failure_reason"])


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
            "plot_image_base64": "ZmFrZQ==",
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
        metadata={"plot_image_base64": "ZmFrZQ=="},
    )
    state = ConversationState(turns=[turn])

    views = components.build_message_views(state)

    assert views[-1].role == "assistant"
    assert views[-1].streaming is True
    assert views[-1].status == "streaming"
