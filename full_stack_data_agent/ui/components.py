from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from types import SimpleNamespace
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from databao.agent.visualizers.vega_vis_tool import VegaVisTool
from full_stack_data_agent.context.models import ConversationState, ConversationTurn, UploadedFileContext
from full_stack_data_agent.llm.models import ProviderHealth

_CHART_MIN_HEIGHT = 360
_PROFILE_VALUE_PREVIEW_LIMIT = 10


@dataclass
class MessageView:
    message_id: str
    turn_id: str
    role: str
    content: str
    timestamp: float | None
    streaming: bool = False
    status: str = "complete"
    attachments: list[dict[str, Any]] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    charts: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _fmt_time(timestamp: float | None) -> str:
    if not timestamp:
        return "--:--"
    return datetime.fromtimestamp(timestamp).strftime("%H:%M")


def _fallback_provider_label(status: Any) -> str:
    return str(getattr(status, "fallback_provider", None) or "--")


def _turn_ui_state() -> dict[str, dict[str, Any]]:
    state = st.session_state.get("turn_ui_state")
    if not isinstance(state, dict):
        state = {}
        st.session_state["turn_ui_state"] = state
    return state


def _ensure_turn_ui_state(state: ConversationState) -> dict[str, dict[str, Any]]:
    ui_state = _turn_ui_state()
    total = len(state.turns)
    for index, turn in enumerate(state.turns):
        if not isinstance(ui_state.get(turn.turn_id), dict):
            ui_state[turn.turn_id] = {"expanded": index == total - 1}
        elif "expanded" not in ui_state[turn.turn_id]:
            ui_state[turn.turn_id]["expanded"] = index == total - 1
    return ui_state


def _turn_is_expanded(turn: ConversationTurn, index: int, total: int) -> bool:
    ui_state = _turn_ui_state()
    entry = ui_state.get(turn.turn_id)
    if not isinstance(entry, dict):
        ui_state[turn.turn_id] = {"expanded": index == total - 1}
        entry = ui_state[turn.turn_id]
    if "expanded" not in entry:
        entry["expanded"] = index == total - 1
    return bool(entry.get("expanded", index == total - 1))


def _set_turn_expanded(turn_id: str, expanded: bool) -> None:
    ui_state = _turn_ui_state()
    entry = ui_state.get(turn_id)
    if not isinstance(entry, dict):
        ui_state[turn_id] = {"expanded": expanded}
    else:
        entry["expanded"] = expanded


def _render_plot_image_base64(plot_image_base64: str | None, *, mime_type: str | None = None) -> bool:
    if not plot_image_base64:
        return False
    try:
        raw = base64.b64decode(plot_image_base64)
    except Exception:
        return False
    st.image(raw, use_container_width=True)
    return True


def _is_matplotlib_figure(obj: Any) -> bool:
    return isinstance(obj, Figure)


def _is_matplotlib_axes(obj: Any) -> bool:
    return isinstance(obj, Axes)


def _as_matplotlib_figure(obj: Any) -> Figure | Axes | None:
    if _is_matplotlib_figure(obj):
        return obj
    if _is_matplotlib_axes(obj):
        return obj.figure
    return None


def _response_payload(result: Any | None) -> Any | None:
    if result is None:
        return None
    return getattr(result, "last_databao_result", result)


def _response_payload_dict(response: Any | None) -> dict[str, Any]:
    if response is None:
        return {}
    if isinstance(response, dict):
        return response
    to_dict = getattr(response, "to_dict", None)
    if callable(to_dict):
        try:
            payload = to_dict()
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
    if hasattr(response, "__dict__"):
        return {key: value for key, value in vars(response).items() if not key.startswith("_")}
    return {}


def _chart_debug_from_response(response: Any | None) -> dict[str, Any]:
    payload = _response_payload_dict(_response_payload(response))
    chart_debug = payload.get("chart_debug")
    return chart_debug if isinstance(chart_debug, dict) else {}


def _chart_failed_upstream(chart_debug: dict[str, Any] | None) -> bool:
    if not isinstance(chart_debug, dict):
        return False
    return str(chart_debug.get("planner_status") or "") in {
        "planning_failed",
        "schema_parse_failed",
        "validation_failed",
        "render_failed",
    }


def _ensure_chart_height(chart: Any, *, minimum_height: int = _CHART_MIN_HEIGHT) -> Any:
    to_dict = getattr(chart, "to_dict", None)
    if callable(to_dict):
        try:
            chart_dict = to_dict()
            if isinstance(chart_dict, dict) and chart_dict.get("height") is None:
                return chart.properties(height=minimum_height)
            return chart
        except Exception:
            pass

    height = getattr(chart, "height", None)
    if height is None:
        try:
            return chart.properties(height=minimum_height)
        except Exception:
            return chart
    return chart


def _render_chart_from_response(result: Any, *, chart_debug: dict[str, Any] | None = None) -> bool:
    response = _response_payload(result)
    if response is None:
        return False

    chart_debug = chart_debug if isinstance(chart_debug, dict) else {}
    chart_debug["chart_render_called"] = True
    if _chart_failed_upstream(chart_debug):
        return False

    plot_object = getattr(response, "plot_object", None)
    if plot_object is not None:
        try:
            altair_builder = getattr(plot_object, "altair", None)
            if callable(altair_builder):
                chart = _ensure_chart_height(altair_builder())
                if chart is not None:
                    chart_debug["chart_renderable"] = True
                    chart_debug["chart_renderer"] = "altair_chart"
                    chart_debug["chart_type"] = type(chart).__name__
                    st.altair_chart(chart, use_container_width=True)
                    return True

            to_altair_chart = getattr(plot_object, "to_altair_chart", None)
            if callable(to_altair_chart):
                chart = _ensure_chart_height(to_altair_chart())
                if chart is not None:
                    chart_debug["chart_renderable"] = True
                    chart_debug["chart_renderer"] = "altair_chart"
                    chart_debug["chart_type"] = type(chart).__name__
                    st.altair_chart(chart, use_container_width=True)
                    return True

            plot = getattr(plot_object, "plot", None)
            if plot is not None:
                matplotlib_plot = _as_matplotlib_figure(plot)
                if matplotlib_plot is not None:
                    chart_debug["chart_renderable"] = True
                    chart_debug["chart_renderer"] = "pyplot"
                    chart_debug["chart_type"] = type(plot).__name__
                    st.pyplot(matplotlib_plot, use_container_width=True)
                    return True
                chart_debug["chart_renderable"] = True
                chart_debug["chart_renderer"] = "altair_chart"
                chart_debug["chart_type"] = type(plot).__name__
                st.altair_chart(_ensure_chart_height(plot), use_container_width=True)
                return True
        except Exception as exc:
            chart_debug["chart_failure_stage"] = "ui_render_failed"
            chart_debug["chart_failure_reason"] = str(exc)
            st.error(f"Chart render failed: {exc}")
            return False

        matplotlib_plot = _as_matplotlib_figure(plot_object)
        if matplotlib_plot is not None:
            chart_debug["chart_renderable"] = True
            chart_debug["chart_renderer"] = "pyplot"
            chart_debug["chart_type"] = type(plot_object).__name__
            st.pyplot(matplotlib_plot, use_container_width=True)
            return True

        png_bytes = getattr(plot_object, "png_bytes", None)
        if callable(png_bytes):
            try:
                image_bytes = png_bytes()
                if image_bytes:
                    chart_debug["chart_renderable"] = True
                    chart_debug["chart_renderer"] = "image"
                    chart_debug["chart_type"] = type(plot_object).__name__
                    st.image(image_bytes, use_container_width=True)
                    return True
            except Exception:
                pass

    response_dict = _response_payload_dict(response)
    plot_image_base64 = response_dict.get("plot_image_base64")
    if not plot_image_base64:
        plot_meta = response_dict.get("plot_meta")
        if isinstance(plot_meta, dict):
            plot_image_base64 = plot_meta.get("plot_image_base64")
    if plot_image_base64:
        chart_debug["chart_renderable"] = True
        chart_debug["chart_renderer"] = "image_base64"
        chart_debug["chart_type"] = response_dict.get("plot_kind") or response_dict.get("plot_backend") or "image"
        if _render_plot_image_base64(str(plot_image_base64), mime_type=response_dict.get("plot_image_mime_type")):
            return True

    plot_spec = response_dict.get("plot_spec")
    plot_data = response_dict.get("plot_data")
    if not plot_spec or not plot_data:
        chart_debug["chart_failure_stage"] = "history_payload_validation"
        chart_debug["chart_failure_reason"] = (
            f"plot_spec={'present' if plot_spec else 'missing'}, plot_data={'present' if plot_data else 'missing'}"
        )
        return False

    try:
        dataframe = pd.DataFrame(plot_data)
        prepared_spec = VegaVisTool.prepare_spec(plot_spec, dataframe)
        chart = alt.Chart.from_dict(prepared_spec, validate=False)
        chart_debug["chart_renderable"] = True
        chart_debug["chart_renderer"] = "altair_chart_from_spec"
        chart_debug["chart_type"] = str(plot_spec.get("mark") or "vega-lite")
        st.altair_chart(_ensure_chart_height(chart), use_container_width=True)
        return True
    except Exception as exc:
        chart_debug["chart_failure_stage"] = "ui_render_failed"
        chart_debug["chart_failure_reason"] = str(exc)
        chart_debug["plot_spec_dump"] = plot_spec
        st.error(f"Chart render failed: {exc}")
        return False


def _registered_table_lookup(result: Any | None) -> dict[str, dict[str, Any]]:
    if result is None:
        return {}
    runtime_snapshot = getattr(result, "last_runtime_snapshot", None)
    if runtime_snapshot is None:
        return {}

    if isinstance(runtime_snapshot, dict):
        registered_tables = runtime_snapshot.get("registered_tables") or []
    else:
        registered_tables = getattr(runtime_snapshot, "registered_tables", None) or []
        registered_tables = [
            table.to_dict() if hasattr(table, "to_dict") else (table if isinstance(table, dict) else {})
            for table in registered_tables
        ]

    lookup: dict[str, dict[str, Any]] = {}
    for table in registered_tables:
        if not isinstance(table, dict):
            continue
        source_file = str(table.get("source_file") or "")
        table_name = str(table.get("name") or "")
        if source_file:
            lookup[source_file] = table
        if table_name and table_name not in lookup:
            lookup[table_name] = table
    return lookup


def _render_profile_summary(profile: dict[str, Any], normalization_report: dict[str, Any] | None = None) -> None:
    column_hints = profile.get("column_type_hints", {}) if isinstance(profile, dict) else {}
    grouped_columns: dict[str, list[str]] = {"measure": [], "time": [], "key": [], "categorical": [], "boolean": []}
    for column, hint in column_hints.items():
        if not isinstance(hint, dict):
            continue
        semantic_type = str(hint.get("semantic_type") or "")
        if semantic_type in grouped_columns:
            grouped_columns[semantic_type].append(str(column))

    for title, key in (
        ("Numeric measures", "measure"),
        ("Time columns", "time"),
        ("Identifier columns", "key"),
        ("Categorical columns", "categorical"),
        ("Boolean columns", "boolean"),
    ):
        if grouped_columns[key]:
            st.markdown(f"**{title}:** {', '.join(grouped_columns[key])}")

    canonical_maps = profile.get("canonical_categorical_value_maps", {}) if isinstance(profile, dict) else {}
    if canonical_maps:
        st.markdown("**Canonicalized variants detected**")
        for column, mapping in canonical_maps.items():
            if not isinstance(mapping, dict) or not mapping:
                continue
            preview_pairs = [f"{source} -> {target}" for source, target in list(mapping.items())[:_PROFILE_VALUE_PREVIEW_LIMIT]]
            st.markdown(f"- {escape(str(column))}: {escape('; '.join(preview_pairs))}")

    if isinstance(normalization_report, dict):
        warnings = normalization_report.get("warnings") or []
        if warnings:
            st.markdown("**Normalization warnings**")
            for warning in warnings:
                st.markdown(f"- {escape(str(warning))}")


def render_upload_cards(uploaded_contexts: list[UploadedFileContext], *, compact: bool = False) -> None:
    if not uploaded_contexts:
        st.markdown('<div class="empty-card">No uploaded files yet.</div>', unsafe_allow_html=True)
        return

    for item in uploaded_contexts:
        summary = "text context" if not item.is_tabular else f"{item.row_count or 0} rows x {len(item.columns)} cols"
        if not item.is_tabular:
            summary = item.summary[:160]
        st.markdown(
            f"""
            <div class="sidebar-item {'sidebar-item--compact' if compact else ''}">
              <div class="sidebar-item__title">{escape(item.file_name)}</div>
              <div class="sidebar-item__meta">{escape('table' if item.is_tabular else 'text')} · {escape(summary)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_registered_tables_panel(result: Any | None) -> None:
    table_lookup = _registered_table_lookup(result)
    if not table_lookup:
        st.markdown('<div class="empty-card">No registered tables yet.</div>', unsafe_allow_html=True)
        return

    seen: set[str] = set()
    for table in table_lookup.values():
        name = str(table.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        columns = [str(column) for column in (table.get("columns") or [])[:5]]
        column_preview = ", ".join(columns) if columns else "no columns"
        st.markdown(
            f"""
            <div class="sidebar-item">
              <div class="sidebar-item__title">{escape(name)}</div>
              <div class="sidebar-item__meta">{int(table.get('row_count') or 0)} rows · {escape(column_preview)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        profile = table.get("semantic_profile") or {}
        normalization_report = table.get("normalization_report") or {}
        if profile or normalization_report:
            with st.expander(f"Schema details · {name}", expanded=False):
                _render_profile_summary(profile if isinstance(profile, dict) else {}, normalization_report if isinstance(normalization_report, dict) else {})


def render_runtime_snapshot(status: ProviderHealth, state: ConversationState, uploaded_count: int) -> None:
    connected_text = "Connected" if status.connected else "Disconnected"
    st.markdown(
        f"""
        <div class="session-card">
          <div class="session-card__row"><span>Provider</span><strong>{escape(status.provider)}</strong></div>
          <div class="session-card__row"><span>Model</span><strong>{escape(status.model)}</strong></div>
          <div class="session-card__row"><span>Fallback</span><strong>{escape(_fallback_provider_label(status))}</strong></div>
          <div class="session-card__row"><span>Runtime</span><strong>{connected_text}</strong></div>
          <div class="session-card__row"><span>Turns</span><strong>{state.turn_count}</strong></div>
          <div class="session-card__row"><span>Uploads</span><strong>{uploaded_count}</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _message_tables(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    preview = metadata.get("dataframe_preview") or []
    if not preview:
        return []
    return [
        {
            "label": "Table preview",
            "rows": preview,
            "row_count": metadata.get("row_count"),
            "columns": metadata.get("columns") or [],
        }
    ]


def _message_artifacts(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for key in ("completion_validation", "normalization_reports", "registered_tables"):
        value = metadata.get(key)
        if value:
            artifacts.append({"label": key.replace("_", " ").title(), "value": value})
    return artifacts


def build_message_views(state: ConversationState) -> list[MessageView]:
    views: list[MessageView] = []
    for turn in state.turns:
        views.append(
            MessageView(
                message_id=turn.user_message.message_id,
                turn_id=turn.turn_id,
                role="user",
                content=turn.user_message.content,
                timestamp=turn.user_message.created_at,
                status=turn.user_message.status,
                metadata={},
            )
        )
        if turn.assistant_message is None:
            continue
        metadata = turn.metadata if isinstance(turn.metadata, dict) else {}
        chart_requested = any(
            [
                metadata.get("plot_spec"),
                metadata.get("plot_data"),
                metadata.get("plot_image_base64"),
                metadata.get("plot_backend"),
                metadata.get("plot_object"),
                metadata.get("chart_debug"),
            ]
        )
        charts = [{"label": "Chart"}] if chart_requested else []
        views.append(
            MessageView(
                message_id=turn.assistant_message.message_id,
                turn_id=turn.turn_id,
                role="assistant",
                content=turn.assistant_message.content,
                timestamp=turn.assistant_message.created_at,
                streaming=turn.assistant_message.status == "streaming",
                status=turn.assistant_message.status,
                attachments=[],
                trace=turn.debug_detailed or {},
                artifacts=_message_artifacts(metadata),
                charts=charts,
                tables=_message_tables(metadata),
                metadata=metadata,
            )
        )
    return views


def _render_table_renderer(table: dict[str, Any], *, key_suffix: str) -> None:
    rows = table.get("rows") or []
    if not rows:
        return
    st.markdown(
        f'<div class="message-subtitle">{escape(str(table.get("label") or "Table"))}</div>',
        unsafe_allow_html=True,
    )
    preview_height = min(340, max(220, 56 + (len(rows) * 28)))
    st.dataframe(rows, use_container_width=True, hide_index=True, height=preview_height)


def _render_chart_renderer(metadata: dict[str, Any], *, key_suffix: str) -> None:
    chart_debug = _chart_debug_from_response(metadata) or dict(metadata.get("chart_debug") or {})
    chart_rendered = _render_chart_from_response(SimpleNamespace(last_databao_result=metadata), chart_debug=chart_debug)
    plot_error = chart_debug.get("plot_error") or metadata.get("plot_error")
    if _chart_failed_upstream(chart_debug):
        failure_stage = chart_debug.get("planner_status") or chart_debug.get("chart_failure_stage") or "generation_failed"
        failure_reason = (
            chart_debug.get("render_error")
            or "; ".join(str(item) for item in (chart_debug.get("validation_errors") or []))
            or chart_debug.get("planner_error")
            or chart_debug.get("chart_failure_reason")
            or plot_error
            or "chart requested but failed upstream"
        )
        st.error(f"Chart generation failed ({failure_stage}): {failure_reason}")
    elif plot_error:
        st.warning(f"Chart generation failed: {plot_error}")
    elif chart_debug.get("chart_requested") and not chart_rendered:
        failure_stage = chart_debug.get("chart_failure_stage") or "ui_render_failed"
        failure_reason = chart_debug.get("chart_failure_reason") or "chart requested but did not render"
        st.warning(f"Chart render issue ({failure_stage}): {failure_reason}")


def _render_artifact_renderer(artifacts: list[dict[str, Any]], *, key_suffix: str) -> None:
    for artifact in artifacts:
        with st.expander(str(artifact.get("label") or "Artifact"), expanded=False):
            st.code(_safe_json(artifact.get("value")), language="json")


def _render_trace_panel(trace: dict[str, Any], *, key_suffix: str) -> None:
    with st.expander("Trace / Details", expanded=False):
        st.code(_safe_json(trace or {}), language="json")


def _render_user_message(message: MessageView) -> None:
    st.markdown(f'<div class="message-text">{escape(message.content)}</div>', unsafe_allow_html=True)


def _render_assistant_message(message: MessageView) -> None:
    if message.content.strip():
        st.markdown(message.content)
    elif message.streaming:
        st.markdown('<div class="message-streaming">Thinking...</div>', unsafe_allow_html=True)

    for index, table in enumerate(message.tables):
        _render_table_renderer(table, key_suffix=f"{message.message_id}-table-{index}")

    if message.charts:
        _render_chart_renderer(message.metadata, key_suffix=f"{message.message_id}-chart")

    if message.artifacts:
        _render_artifact_renderer(message.artifacts, key_suffix=f"{message.message_id}-artifact")

    if message.trace or message.metadata:
        _render_trace_panel(message.trace or message.metadata, key_suffix=f"{message.message_id}-trace")


def _render_chat_message(message: MessageView) -> None:
    if message.role == "assistant":
        left, right = st.columns([0.82, 0.18], gap="small")
        target = left
        shell_class = "chat-shell chat-shell--assistant"
    else:
        left, right = st.columns([0.18, 0.82], gap="small")
        target = right
        shell_class = "chat-shell chat-shell--user"

    if message.streaming:
        shell_class += " chat-shell--streaming"
    if message.status == "error":
        shell_class += " chat-shell--error"

    with target:
        st.markdown(f'<div class="{shell_class}">', unsafe_allow_html=True)
        with st.chat_message(message.role, avatar=None):
            st.markdown(
                f"""
                <div class="message-header">
                  <span class="message-role">{escape(message.role)}</span>
                  <span class="message-time">{escape(_fmt_time(message.timestamp))}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if message.role == "assistant":
                _render_assistant_message(message)
            else:
                _render_user_message(message)
        st.markdown("</div>", unsafe_allow_html=True)


def render_conversation_history(state: ConversationState, last_result: Any | None = None) -> None:
    _ensure_turn_ui_state(state)
    st.markdown(
        """
        <div class="conversation-shell">
          <div class="conversation-title">Conversation</div>
          <div class="conversation-subtitle">One message flow for history, streaming, charts, tables, and trace.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    views = build_message_views(state)
    if not views:
        st.markdown(
            """
            <div class="chat-empty-state">
              <div class="chat-empty-state__title">Start a conversation</div>
              <div class="chat-empty-state__body">Ask a question, attach a file, and the reply will stay inline here.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    for message in views:
        _render_chat_message(message)


def render_result_panel(result: Any | None) -> None:
    response = _response_payload(result)
    if response is None:
        st.markdown('<div class="empty-card">No output yet.</div>', unsafe_allow_html=True)
        return

    response_dict = _response_payload_dict(response)
    if response_dict.get("text"):
        st.markdown(response_dict["text"])
    if response_dict.get("dataframe_preview"):
        _render_table_renderer(
            {
                "label": "Latest table preview",
                "rows": response_dict["dataframe_preview"],
                "row_count": response_dict.get("row_count"),
                "columns": response_dict.get("columns") or [],
            },
            key_suffix="latest",
        )
    chart_debug = _chart_debug_from_response(response)
    has_plot_object = getattr(response, "plot_object", None) is not None
    if (
        chart_debug.get("chart_requested")
        or response_dict.get("plot_spec")
        or response_dict.get("plot_data")
        or response_dict.get("plot_image_base64")
        or response_dict.get("plot_backend")
        or has_plot_object
    ):
        _render_chart_renderer(response_dict | {"plot_object": getattr(response, "plot_object", None)}, key_suffix="latest-chart")
