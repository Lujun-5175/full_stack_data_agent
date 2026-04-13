from __future__ import annotations

import base64
import json
from datetime import datetime
from html import escape
from types import SimpleNamespace
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from full_stack_data_agent.context.models import ConversationState, ConversationTurn, UploadedFileContext
from full_stack_data_agent.llm.models import ProviderHealth
from databao.agent.visualizers.vega_vis_tool import VegaVisTool

_PROFILE_VALUE_PREVIEW_LIMIT = 10
_CHART_MIN_HEIGHT = 360


def _fallback_provider_label(status: Any) -> str:
    return str(getattr(status, "fallback_provider", None) or "--")


def _safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _is_matplotlib_figure(obj: Any) -> bool:
    return isinstance(obj, Figure)


def _is_matplotlib_axes(obj: Any) -> bool:
    return isinstance(obj, Axes)


def _render_plot_image_base64(plot_image_base64: str | None, *, mime_type: str | None = None) -> bool:
    if not plot_image_base64:
        return False
    try:
        raw = base64.b64decode(plot_image_base64)
    except Exception:
        return False
    st.image(raw, use_container_width=True)
    return True


def _as_matplotlib_figure(obj: Any) -> Figure | Axes | None:
    if _is_matplotlib_figure(obj):
        return obj
    if _is_matplotlib_axes(obj):
        return obj.figure
    return None


def _fmt_time(timestamp: float | None) -> str:
    if not timestamp:
        return "--:--"
    return datetime.fromtimestamp(timestamp).strftime("%H:%M")


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
            chart_debug["chart_failure_stage"] = "ui_render_or_layout"
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

        image = getattr(plot_object, "image", None)
        if callable(image):
            try:
                image_obj = image()
                if image_obj is not None:
                    chart_debug["chart_renderable"] = True
                    chart_debug["chart_renderer"] = "image"
                    chart_debug["chart_type"] = type(plot_object).__name__
                    st.image(image_obj, use_container_width=True)
                    return True
            except Exception:
                pass

    response_dict = _response_payload_dict(response)
    plot_image_base64 = response_dict.get("plot_image_base64")
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
        chart_debug["chart_failure_stage"] = "ui_render_or_layout"
        chart_debug["chart_failure_reason"] = str(exc)
        chart_debug["plot_spec_dump"] = plot_spec
        st.error(f"Chart render failed: {exc}")
        return False


def render_shell_header(status: ProviderHealth, state: ConversationState, uploaded_count: int) -> None:
    st.markdown(
        f"""
        <div class="shell-header">
          <div>
            <div class="shell-header__eyebrow">Workspace / Data Agent / Session</div>
            <div class="shell-header__title-row">
              <h1>Full Stack Data Agent</h1>
              <span class="status-dot {'status-dot--ok' if status.connected else 'status-dot--warn'}"></span>
            </div>
            <p>Local-first analysis workspace for grounded questions, uploads, results, and traceable execution.</p>
          </div>
          <div class="shell-header__badges">
            <span class="badge {'badge--ok' if status.connected else 'badge--warn'}">{'Connected' if status.connected else 'Disconnected'}</span>
            <span class="badge">Provider: {escape(status.provider)}</span>
            <span class="badge">Model: {escape(status.model)}</span>
            <span class="badge">Fallback: {escape(_fallback_provider_label(status))}</span>
            <span class="badge">Session: {escape(state.conversation_id[:8])}</span>
            <span class="badge">Turns: {state.turn_count}</span>
            <span class="badge">Uploads: {uploaded_count}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_context_rail_header(status: ProviderHealth, state: ConversationState, uploaded_count: int) -> None:
    st.markdown(
        f"""
        <div class="rail-brand">
          <div class="rail-brand__icon">FS</div>
          <div>
            <div class="rail-brand__name">Full Stack Data Agent</div>
            <div class="rail-brand__meta">{escape(status.provider)} {'connected' if status.connected else 'disconnected'}</div>
            <div class="rail-brand__detail">{escape(status.model)} | fallback {escape(_fallback_provider_label(status))} | Turn {state.turn_count} | {uploaded_count} files</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_nav_items() -> None:
    st.markdown(
        """
        <div class="panel-title">Navigation</div>
        <div class="nav-list">
          <div class="nav-item nav-item--active">Workspace</div>
          <div class="nav-item">Uploads</div>
          <div class="nav-item">Results</div>
          <div class="nav-item">Trace</div>
          <div class="nav-item">Runtime</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_upload_cards(uploaded_contexts: list[UploadedFileContext], *, compact: bool = False) -> None:
    if not uploaded_contexts:
        st.markdown(
            """
            <div class="empty-state empty-state--compact">
              <div class="empty-state__title">No uploaded files</div>
              <div class="empty-state__body">Upload text, markdown, CSV, JSON, or code files to ground the workspace.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    for item in uploaded_contexts:
        summary = "text context" if not item.is_tabular else f"{item.row_count or 0} rows × {len(item.columns)} cols"
        if not item.is_tabular:
            summary = item.summary[:160]
        st.markdown(
            f"""
            <div class="file-card {'file-card--compact' if compact else ''}">
              <div class="file-card__top">
                <div class="file-card__name">{escape(item.file_name)}</div>
                <div class="file-card__status">{'table' if item.is_tabular else 'text'}</div>
              </div>
              <div class="file-card__summary">{escape(summary)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_profile_summary(profile: dict[str, Any], normalization_report: dict[str, Any] | None = None) -> None:
    column_hints = profile.get("column_type_hints", {}) if isinstance(profile, dict) else {}
    grouped_columns: dict[str, list[str]] = {"measure": [], "time": [], "key": [], "categorical": [], "boolean": []}
    for column, hint in column_hints.items():
        if not isinstance(hint, dict):
            continue
        semantic_type = str(hint.get("semantic_type") or "")
        if semantic_type in grouped_columns:
            grouped_columns[semantic_type].append(str(column))

    def _render_group(title: str, columns: list[str]) -> None:
        if columns:
            st.markdown(f"**{title}:** {', '.join(columns)}")

    _render_group("Numeric measures", grouped_columns["measure"])
    _render_group("Time columns", grouped_columns["time"])
    _render_group("Identifier columns", grouped_columns["key"])
    _render_group("Categorical columns", grouped_columns["categorical"])
    _render_group("Boolean columns", grouped_columns["boolean"])

    canonical_maps = profile.get("canonical_categorical_value_maps", {}) if isinstance(profile, dict) else {}
    if canonical_maps:
        st.markdown("**Canonicalized variants detected**")
        for column, mapping in canonical_maps.items():
            if not isinstance(mapping, dict) or not mapping:
                continue
            preview_pairs = [f"{source} -> {target}" for source, target in list(mapping.items())[:3]]
            st.markdown(f"- {escape(str(column))}: {escape('; '.join(preview_pairs))}")

    if isinstance(normalization_report, dict):
        warnings = normalization_report.get("warnings") or []
        if warnings:
            st.markdown("**Normalization warnings**")
            for warning in warnings:
                st.markdown(f"- {escape(str(warning))}")


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


def render_registered_tables_panel(result: Any | None) -> None:
    st.markdown(
        """
        <div class="panel-title">Registered Tables</div>
        <div class="panel-subtitle">Tabular data normalized and registered for the current session.</div>
        """,
        unsafe_allow_html=True,
    )
    table_lookup = _registered_table_lookup(result)
    if not table_lookup:
        st.markdown(
            """
            <div class="empty-state empty-state--compact">
              <div class="empty-state__title">No data tables</div>
              <div class="empty-state__body">Upload a CSV file and the registered tables will appear here.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
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
            <div class="file-card">
              <div class="file-card__top">
                <div class="file-card__name">{escape(name)}</div>
                <div class="file-card__status">{int(table.get('row_count') or 0)} rows</div>
              </div>
              <div class="file-card__summary">{escape(column_preview)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
def render_runtime_snapshot(status: ProviderHealth, state: ConversationState, uploaded_count: int) -> None:
    st.markdown(
        f"""
        <div class="stat-stack">
          <div class="stat-chip">
            <span class="stat-chip__label">Provider</span>
            <span class="stat-chip__value">{escape(status.provider)}</span>
          </div>
          <div class="stat-chip">
            <span class="stat-chip__label">Model</span>
            <span class="stat-chip__value">{escape(status.model)}</span>
          </div>
          <div class="stat-chip">
            <span class="stat-chip__label">Fallback</span>
            <span class="stat-chip__value">{escape(_fallback_provider_label(status))}</span>
          </div>
          <div class="stat-chip">
            <span class="stat-chip__label">Turns</span>
            <span class="stat-chip__value">{state.turn_count}</span>
          </div>
          <div class="stat-chip">
            <span class="stat-chip__label">Uploads</span>
            <span class="stat-chip__value">{uploaded_count}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_workspace_heading() -> None:
    st.markdown(
        """
        <div class="workspace-breadcrumb">Tools / Full Stack Data Agent / Current Session</div>
        <div class="workspace-heading">
          <div>
            <div class="workspace-heading__title">Agent Workspace</div>
            <div class="workspace-heading__subtitle">
              Configure a prompt, ground it with uploads, run the local model, and inspect output and trace side by side.
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_composer_intro() -> None:
    st.markdown(
        """
        <div class="panel-title">Ask The Agent</div>
        <div class="panel-subtitle">
          Treat this panel like a task runner for the local data agent, not a plain chat box.
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_chart_from_turn(turn: ConversationTurn) -> bool:
    return _render_chart_from_response(SimpleNamespace(last_databao_result=turn.metadata))


def render_conversation_history(state: ConversationState, last_result: Any | None = None) -> None:
    st.markdown(
        """
        <div class="panel-title">Conversation</div>
        <div class="panel-subtitle">The assistant response, data preview, charts, and trace all live inline.</div>
        """,
        unsafe_allow_html=True,
    )
    if not state.turns:
        st.markdown(
            """
            <div class="empty-state conversation-empty">
              <div class="empty-state__title">No conversation yet</div>
              <div class="empty-state__body">Run the first prompt to start a grounded analysis thread.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    for index, turn in enumerate(state.turns):
        user_message = turn.user_message
        assistant_message = turn.assistant_message
        st.markdown(
            f"""
            <div class="history-card history-card--user">
              <div class="history-card__top">
                <span class="history-card__role">user</span>
                <span class="history-card__meta">Turn {index + 1} | {_fmt_time(user_message.created_at)}</span>
              </div>
              <div class="history-card__body">{escape(user_message.content)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if assistant_message is None:
            st.markdown(
                """
                <div class="empty-state empty-state--compact">
                  <div class="empty-state__title">Pending response</div>
                  <div class="empty-state__body">The model has not returned this turn yet.</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            continue

        st.markdown(
            f"""
            <div class="history-card history-card--assistant">
              <div class="history-card__top">
                <span class="history-card__role">assistant</span>
                <span class="history-card__meta">{_fmt_time(assistant_message.created_at)}</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(assistant_message.content)
        preview = turn.metadata.get("dataframe_preview") or []
        if preview:
            st.dataframe(preview, use_container_width=True, hide_index=True)
        chart_source: Any | None = last_result if last_result is not None and index == len(state.turns) - 1 else turn.metadata
        chart_debug = _chart_debug_from_response(chart_source) or dict(turn.metadata.get("chart_debug") or {})
        chart_rendered = False
        if (
            chart_debug.get("chart_requested")
            or turn.metadata.get("plot_spec")
            or turn.metadata.get("plot_data")
            or chart_source is not None
        ):
            chart_rendered = _render_chart_from_response(chart_source, chart_debug=chart_debug)
            turn.metadata["chart_debug"] = chart_debug
            turn.debug_detailed.setdefault("chart_debug", chart_debug)
            plot_error = chart_debug.get("plot_error") or turn.metadata.get("plot_error")
            if plot_error:
                st.warning(f"Chart generation failed: {plot_error}")
            elif chart_debug.get("chart_requested") and not chart_rendered:
                failure_stage = chart_debug.get("chart_failure_stage") or "ui_render_or_layout"
                failure_reason = chart_debug.get("chart_failure_reason") or "chart requested but did not render"
                chart_debug["chart_failure_stage"] = failure_stage
                chart_debug["chart_failure_reason"] = failure_reason
                st.warning(f"Chart render issue ({failure_stage}): {failure_reason}")
        footer_parts = []
        if turn.metadata.get("model"):
            footer_parts.append(str(turn.metadata["model"]))
        if turn.metadata.get("row_count") is not None:
            footer_parts.append(f"{turn.metadata['row_count']} rows")
        footer_parts.append(_fmt_time(assistant_message.created_at))
        st.markdown(
            f"<div class=\"history-card__footer\">{escape(' | '.join(footer_parts))}</div>",
            unsafe_allow_html=True,
        )
        with st.expander("Trace", expanded=False):
            st.code(_safe_json(turn.debug_detailed or {}), language="json")
def render_result_panel(result: Any | None) -> None:
    st.markdown(
        """
        <div class="panel-title">Output</div>
        <div class="panel-subtitle">The latest assistant output and future result artifacts will live here.</div>
        """,
        unsafe_allow_html=True,
    )
    response = _response_payload(result)
    if response is None:
        st.markdown(
            """
            <div class="empty-state empty-state--compact">
              <div class="empty-state__title">No output yet</div>
              <div class="empty-state__body">Run a prompt to populate the output inspector.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    response_dict = _response_payload_dict(response)
    text = response_dict.get("text") or ""
    chart_debug = _chart_debug_from_response(response)
    st.markdown(
        f"""
        <div class="inspector-card">
          <div class="inspector-card__title">Latest response</div>
          <div class="inspector-card__body">{escape(str(text))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if response_dict.get("dataframe_preview"):
        st.markdown(
            f"""
            <div class="inspector-card">
              <div class="inspector-card__title">Table preview</div>
              <div class="inspector-card__body">
                {response_dict.get('row_count', 0)} rows | {len(response_dict.get('columns') or [])} columns
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.dataframe(response_dict["dataframe_preview"], use_container_width=True, hide_index=True)
    chart_rendered = _render_chart_from_response(result, chart_debug=chart_debug)
    if response_dict.get("plot_error"):
        st.warning(f"Chart generation failed: {response_dict['plot_error']}")
    elif chart_debug.get("chart_requested") and not chart_rendered:
        failure_stage = chart_debug.get("chart_failure_stage") or "ui_render_or_layout"
        failure_reason = chart_debug.get("chart_failure_reason") or "chart requested but did not render"
        chart_debug["chart_failure_stage"] = failure_stage
        chart_debug["chart_failure_reason"] = failure_reason
        st.warning(f"Chart render issue ({failure_stage}): {failure_reason}")
    plot_backend = response_dict.get("plot_backend") or chart_debug.get("plot_backend")
    plot_kind = response_dict.get("plot_kind") or chart_debug.get("plot_kind")
    if plot_backend == "seaborn":
        with st.expander("Latest plot artifact", expanded=False):
            if response_dict.get("plot_image_base64"):
                st.caption(f"backend={plot_backend} | kind={plot_kind or '--'}")
                _render_plot_image_base64(str(response_dict["plot_image_base64"]), mime_type=response_dict.get("plot_image_mime_type"))
            if response_dict.get("plot_code"):
                st.code(str(response_dict["plot_code"]), language="json")
            elif response_dict.get("plot_meta"):
                st.code(_safe_json(response_dict["plot_meta"]), language="json")
    elif response_dict.get("plot_code"):
        with st.expander("Latest plot spec", expanded=False):
            st.code(str(response_dict["plot_code"]), language="json")
    elif response_dict.get("plot_spec") and not chart_rendered:
        with st.expander("Latest plot spec", expanded=False):
            st.code(_safe_json(response_dict["plot_spec"]), language="json")


def render_uploaded_files_panel(uploaded_contexts: list[UploadedFileContext], result: Any | None = None) -> None:
    st.markdown(
        """
        <div class="panel-title">Uploaded Files</div>
        <div class="panel-subtitle">Context files currently available to the agent.</div>
        """,
        unsafe_allow_html=True,
    )
    if not uploaded_contexts:
        render_upload_cards([], compact=True)
        return
    table_lookup = _registered_table_lookup(result)
    for item in uploaded_contexts:
        render_upload_cards([item], compact=True)
        if not item.is_tabular:
            continue
        table_info = table_lookup.get(item.file_name) or table_lookup.get(item.table_name or "")
        profile = item.semantic_profile or (table_info.get("semantic_profile") if table_info else {})
        normalization_report = table_info.get("normalization_report") if table_info else {}
        if profile or normalization_report:
            with st.expander(f"Column profile: {item.file_name}", expanded=False):
                _render_profile_summary(profile or {}, normalization_report if isinstance(normalization_report, dict) else {})

