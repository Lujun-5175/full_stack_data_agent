from __future__ import annotations

import json
from datetime import datetime
from html import escape
from typing import Any

import streamlit as st

from full_stack_data_agent.context.models import ConversationState, UploadedFileContext
from full_stack_data_agent.llm.models import ProviderHealth


def _safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _shorten(text: str, limit: int = 150) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _fmt_time(timestamp: float | None) -> str:
    if not timestamp:
        return "--:--"
    return datetime.fromtimestamp(timestamp).strftime("%H:%M")


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
            <span class="badge">Model: {escape(status.model)}</span>
            <span class="badge">Session: {escape(state.conversation_id[:8])}</span>
            <span class="badge">Turns: {state.turn_count}</span>
            <span class="badge">Uploads: {uploaded_count}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_context_rail_header(status: ProviderHealth) -> None:
    st.markdown(
        f"""
        <div class="rail-brand">
          <div class="rail-brand__icon">FS</div>
          <div>
            <div class="rail-brand__name">Full Stack Data Agent</div>
            <div class="rail-brand__meta">{'local runtime connected' if status.connected else 'runtime disconnected'}</div>
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
        summary = _shorten(item.summary, 90 if compact else 170)
        table_meta = ""
        if item.is_tabular:
            table_meta = f" | {item.row_count or 0} rows | {len(item.columns)} cols"
        st.markdown(
            f"""
            <div class="file-card {'file-card--compact' if compact else ''}">
              <div class="file-card__top">
                <div class="file-card__name">{escape(item.file_name)}</div>
                <div class="file-card__status">loaded</div>
              </div>
              <div class="file-card__meta">
                {item.size_bytes} bytes{f" | {escape(item.mime_type)}" if item.mime_type else ""}{table_meta}
              </div>
              <div class="file-card__summary">{escape(summary)}</div>
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


def render_conversation_history(state: ConversationState) -> None:
    st.markdown(
        """
        <div class="panel-title">Execution History</div>
        <div class="panel-subtitle">Structured conversation and execution output from the current session.</div>
        """,
        unsafe_allow_html=True,
    )
    if not state.turns:
        st.markdown(
            """
            <div class="empty-state conversation-empty">
              <div class="empty-state__title">No execution history yet</div>
              <div class="empty-state__body">Run the first prompt to start a grounded session history.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    for index, turn in enumerate(state.turns):
        user_message = turn.user_message
        assistant_message = turn.assistant_message
        footer_parts = []
        if turn.metadata.get("model"):
            footer_parts.append(str(turn.metadata["model"]))
        if turn.metadata.get("provider"):
            footer_parts.append(str(turn.metadata["provider"]))
        if turn.metadata.get("used_databao"):
            footer_parts.append("databao runtime")
        if turn.metadata.get("row_count") is not None:
            footer_parts.append(f"{turn.metadata['row_count']} rows")
        if turn.metadata.get("plot_code"):
            footer_parts.append("plot")
        if turn.debug_detailed:
            footer_parts.append("trace available")
        footer = " | ".join(footer_parts) if footer_parts else "trace available"

        st.markdown(
            f"""
            <div class="history-turn">
              <div class="history-card history-card--user">
                <div class="history-card__top">
                  <span class="history-card__role">User</span>
                  <span class="history-card__meta">Turn {index + 1} | {_fmt_time(user_message.created_at)}</span>
                </div>
                <div class="history-card__body">{escape(user_message.content)}</div>
              </div>
            """,
            unsafe_allow_html=True,
        )
        if assistant_message is not None:
            st.markdown(
                f"""
                <div class="history-card history-card--assistant">
                  <div class="history-card__top">
                    <span class="history-card__role">Assistant</span>
                    <span class="history-card__meta">{_fmt_time(assistant_message.created_at)}</span>
                  </div>
                  <div class="history-card__body">{escape(assistant_message.content)}</div>
                  <div class="history-card__footer">{escape(footer)}</div>
                </div>
              </div>
                """,
                unsafe_allow_html=True,
            )
            preview = turn.metadata.get("dataframe_preview") or []
            if preview:
                st.dataframe(preview, use_container_width=True, hide_index=True)
            if turn.metadata.get("plot_code"):
                with st.expander("Plot spec", expanded=False):
                    st.code(str(turn.metadata["plot_code"]), language="json")
            if turn.debug_detailed:
                with st.expander("Trace details", expanded=False):
                    st.code(_safe_json(turn.debug_detailed), language="json")
        else:
            st.markdown(
                """
                <div class="empty-state empty-state--compact">
                  <div class="empty-state__title">Pending response</div>
                  <div class="empty-state__body">The model has not returned this turn yet.</div>
                </div>
              </div>
                """,
                unsafe_allow_html=True,
            )


def render_result_panel(result: Any | None) -> None:
    st.markdown(
        """
        <div class="panel-title">Output</div>
        <div class="panel-subtitle">The latest assistant output and future result artifacts will live here.</div>
        """,
        unsafe_allow_html=True,
    )
    if not result or not result.last_databao_result:
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

    response = result.last_databao_result
    text = response.get("text") or ""
    st.markdown(
        f"""
        <div class="inspector-card">
          <div class="inspector-card__title">Latest response</div>
          <div class="inspector-card__body">{escape(_shorten(str(text), 260))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if response.get("dataframe_preview"):
        st.markdown(
            f"""
            <div class="inspector-card">
              <div class="inspector-card__title">Table preview</div>
              <div class="inspector-card__body">
                {response.get('row_count', 0)} rows | {len(response.get('columns') or [])} columns
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.dataframe(response["dataframe_preview"], use_container_width=True, hide_index=True)
    if response.get("plot_code"):
        with st.expander("Latest plot spec", expanded=False):
            st.code(str(response["plot_code"]), language="json")


def render_uploaded_files_panel(uploaded_contexts: list[UploadedFileContext]) -> None:
    st.markdown(
        """
        <div class="panel-title">Uploaded Files</div>
        <div class="panel-subtitle">Context files currently available to the agent.</div>
        """,
        unsafe_allow_html=True,
    )
    render_upload_cards(uploaded_contexts, compact=True)


def render_trace_panel(result: Any | None) -> None:
    st.markdown(
        """
        <div class="panel-title">Trace And Explain</div>
        <div class="panel-subtitle">Debug highlights and structured request state for the latest run.</div>
        """,
        unsafe_allow_html=True,
    )
    if not result:
        st.markdown(
            """
            <div class="empty-state empty-state--compact">
              <div class="empty-state__title">No trace yet</div>
              <div class="empty-state__body">Trace data appears after the first completed run.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    runtime_snapshot = result.last_runtime_snapshot or {}
    response_data = result.last_databao_result or {}
    st.markdown(
        f"""
        <div class="inspector-card">
          <div class="inspector-card__title">Latest trace summary</div>
          <div class="inspector-card__body">
            Executor: <strong>{escape(str(runtime_snapshot.get('executor_type') or 'n/a'))}</strong><br />
            LLM: <strong>{escape(str(runtime_snapshot.get('llm_name') or 'n/a'))}</strong><br />
            Error: <strong>{escape(str(result.last_error or 'none'))}</strong>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.expander("Latest trace payload", expanded=False):
        st.code(
            _safe_json(
                {
                    "last_runtime_snapshot": result.last_runtime_snapshot,
                    "last_databao_result": result.last_databao_result,
                    "last_debug_detailed": result.last_debug_detailed,
                    "last_error": result.last_error,
                }
            ),
            language="json",
        )


def render_turn_overview(state: ConversationState) -> None:
    st.markdown(
        """
        <div class="panel-title">Turn Overview</div>
        <div class="panel-subtitle">Recent turns, latency hints, and trace availability.</div>
        """,
        unsafe_allow_html=True,
    )
    if not state.turns:
        st.markdown(
            """
            <div class="empty-state empty-state--compact">
              <div class="empty-state__title">No turns yet</div>
              <div class="empty-state__body">Turn summaries will appear here once the agent starts running.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    for turn in reversed(state.turns[-5:]):
        flags = []
        if turn.metadata.get("model"):
            flags.append(str(turn.metadata["model"]))
        if turn.metadata.get("used_databao"):
            flags.append("databao")
        if turn.metadata.get("row_count") is not None:
            flags.append(f"{turn.metadata['row_count']} rows")
        if turn.debug_detailed:
            flags.append("trace")
        flag_text = " | ".join(flags) if flags else "runtime available"
        st.markdown(
            f"""
            <div class="timeline-card">
              <div class="timeline-card__top">
                <div class="timeline-card__title">{escape(_shorten(turn.user_message.content, 52))}</div>
                <div class="timeline-card__meta">{_fmt_time(turn.user_message.created_at)}</div>
              </div>
              <div class="timeline-card__body">{escape(_shorten(turn.assistant_message.content if turn.assistant_message else 'Pending response', 110))}</div>
              <div class="timeline-card__footer">{escape(flag_text)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_quick_diagnostics(status: ProviderHealth, result: Any | None, state: ConversationState, uploaded_count: int) -> None:
    st.markdown(
        """
        <div class="panel-title">Quick Diagnostics</div>
        <div class="panel-subtitle">Fast health signals for the runtime and grounding state.</div>
        """,
        unsafe_allow_html=True,
    )
    latency = "n/a"
    if result and result.last_debug_detailed:
        latency = str(result.last_debug_detailed.get("thread_meta", {}).get("latency_ms", "n/a"))
    runtime_snapshot = result.last_runtime_snapshot if result else {}
    st.markdown(
        f"""
        <div class="diagnostic-grid">
          <div class="diagnostic-card">
            <div class="mini-label">Runtime</div>
            <div class="mini-value">{'healthy' if status.connected else 'offline'}</div>
          </div>
          <div class="diagnostic-card">
            <div class="mini-label">Latency</div>
            <div class="mini-value">{escape(latency)}</div>
          </div>
          <div class="diagnostic-card">
            <div class="mini-label">Grounding</div>
            <div class="mini-value">{'tables active' if uploaded_count else 'session only'}</div>
          </div>
          <div class="diagnostic-card">
            <div class="mini-label">Turns</div>
            <div class="mini-value">{state.turn_count}</div>
          </div>
          <div class="diagnostic-card">
            <div class="mini-label">Executor</div>
            <div class="mini-value">{escape(str(runtime_snapshot.get('executor_type', 'n/a')))}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
