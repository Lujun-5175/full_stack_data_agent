from __future__ import annotations

from html import escape
import logging
from typing import TYPE_CHECKING, Any

import streamlit as st

from full_stack_data_agent.bootstrap import bootstrap
from full_stack_data_agent.app.conversation_export import build_conversation_export_bundle
from full_stack_data_agent.config.settings import get_settings
from full_stack_data_agent.context.models import UploadedFileContext
from full_stack_data_agent.context.upload_processor import SUPPORTED_UPLOAD_EXTENSIONS, process_uploaded_file
from full_stack_data_agent.ui.components import (
    render_conversation_history,
    render_registered_tables_panel,
    render_runtime_snapshot,
    render_upload_cards,
)
from full_stack_data_agent.ui.theme import get_ui_css

if TYPE_CHECKING:
    from full_stack_data_agent.app.chat_service import ChatService

logger = logging.getLogger(__name__)

def configure_page() -> None:
    st.set_page_config(page_title="Full Stack Data Agent", layout="wide", initial_sidebar_state="collapsed")
    st.markdown(get_ui_css(), unsafe_allow_html=True)


@st.cache_resource
def _create_service() -> "ChatService":
    return bootstrap(get_settings())


def get_service() -> "ChatService":
    return _create_service()


def _init_session_state() -> None:
    service = get_service()
    defaults = {
        "conversation_state": service.create_state(),
        "last_result": None,
        "uploaded_contexts": [],
        "last_prompt": "",
        "composer_text": "",
        "upload_signature": (),
        "composer_reset_pending": False,
        "turn_ui_state": {},
        "sidebar_open": False,
        "active_stream_turn_id": None,
        "active_stream_buffer": "",
        "active_stream_status": "idle",
        "auto_scroll_armed": True,
        "scroll_nonce": 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _file_signature(uploaded_files: list[Any] | None) -> tuple[tuple[str, int, str | None], ...]:
    return tuple(
        (file_obj.name, int(getattr(file_obj, "size", 0) or 0), getattr(file_obj, "type", None))
        for file_obj in (uploaded_files or [])
    )


def _process_uploads(uploaded_files: list[Any] | None) -> tuple[list[UploadedFileContext], tuple[tuple[str, int, str | None], ...]]:
    processed: list[UploadedFileContext] = []
    seen: set[tuple[str, int, str | None]] = set()
    for file_obj in uploaded_files or []:
        signature = (file_obj.name, int(getattr(file_obj, "size", 0) or 0), getattr(file_obj, "type", None))
        if signature in seen:
            continue
        seen.add(signature)
        raw = file_obj.getvalue()
        item = process_uploaded_file(file_obj.name, raw, getattr(file_obj, "type", None))
        if item is not None:
            processed.append(item)
    return processed, _file_signature(uploaded_files)


def _sync_uploaded_contexts() -> None:
    composer_files = st.session_state.get("composer_uploader_value")
    processed, signature = _process_uploads(composer_files)
    if signature != st.session_state.upload_signature:
        st.session_state.upload_signature = signature
        st.session_state.uploaded_contexts = processed


def _apply_pending_ui_resets() -> None:
    if st.session_state.get("composer_reset_pending"):
        st.session_state["composer_text"] = ""
        st.session_state["composer_reset_pending"] = False


def _render_conversation(mount: Any, *, force_scroll: bool = False) -> None:
    mount.empty()
    with mount.container():
        render_conversation_history(st.session_state.conversation_state, st.session_state.last_result)


def _clear_uploads() -> None:
    st.session_state.uploaded_contexts = []
    st.session_state.upload_signature = ()
    if "composer_uploader_value" in st.session_state:
        del st.session_state["composer_uploader_value"]


def _submit_prompt(prompt: str, conversation_mount: Any) -> None:
    service = get_service()
    message = prompt.strip()
    if not message:
        return

    st.session_state.last_prompt = message
    st.session_state.active_stream_buffer = ""
    st.session_state.active_stream_status = "streaming"
    st.session_state.scroll_nonce += 1

    state = st.session_state.conversation_state
    stream = service.send_message_stream(
        state,
        message,
        uploaded_contexts=st.session_state.uploaded_contexts,
    )
    st.session_state.active_stream_turn_id = state.debug_state.get("_active_stream_turn_id")
    _render_conversation(conversation_mount, force_scroll=True)

    try:
        for _chunk in stream:
            st.session_state.active_stream_buffer = state.debug_state.get("_active_stream_buffer", "")
            st.session_state.active_stream_status = state.debug_state.get("_active_stream_status", "streaming")
            _render_conversation(conversation_mount, force_scroll=False)
    except Exception as exc:
        logger.exception("Streaming submit failed")
        st.session_state.active_stream_status = "error"
        st.session_state.last_result = state.debug_state.get("_last_chat_service_result")
        st.error(f"Request failed: {exc}")
    finally:
        st.session_state.conversation_state = state
        st.session_state.last_result = state.debug_state.get("_last_chat_service_result")
        st.session_state.active_stream_turn_id = None
        st.session_state.active_stream_buffer = ""
        st.session_state.active_stream_status = state.debug_state.get("_active_stream_status", "complete")
        st.session_state.composer_reset_pending = True
        st.session_state.scroll_nonce += 1
        _render_conversation(conversation_mount, force_scroll=True)


def _clear_chat() -> None:
    service = get_service()
    previous_state = st.session_state.conversation_state
    service.drop_session(previous_state.conversation_id)
    st.session_state.conversation_state = service.create_state()
    st.session_state.last_result = None
    st.session_state.last_prompt = ""
    st.session_state.composer_reset_pending = True
    st.session_state.active_stream_turn_id = None
    st.session_state.active_stream_buffer = ""
    st.session_state.active_stream_status = "idle"


def _render_sidebar(status: Any) -> None:
    settings = get_settings()
    sidebar_class = "sidebar-shell" if st.session_state.sidebar_open else "sidebar-shell sidebar-shell--collapsed"
    st.markdown(f'<div class="{sidebar_class}">', unsafe_allow_html=True)

    if st.session_state.sidebar_open:
        st.markdown(
            """
            <div class="sidebar-brand">
              <div class="sidebar-brand__logo">FS</div>
              <div>
                <div class="sidebar-brand__name">Full Stack Data Agent</div>
                <div class="sidebar-brand__meta">Conversation-first data workspace</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <div class="sidebar-brand sidebar-brand--compact">
              <div class="sidebar-brand__logo">FS</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    toggle_label = "Collapse" if st.session_state.sidebar_open else "Expand"
    if st.button(toggle_label, use_container_width=True, key="sidebar-toggle"):
        st.session_state.sidebar_open = not st.session_state.sidebar_open
        st.rerun()

    if st.button("New chat", type="primary", use_container_width=True, key="sidebar-new-chat"):
        _clear_chat()
        st.rerun()

    if not st.session_state.sidebar_open:
        st.markdown("</div>", unsafe_allow_html=True)
        return

    with st.expander("Files", expanded=False):
        render_upload_cards(st.session_state.uploaded_contexts, compact=True)
        if st.button("Clear files", use_container_width=True, key="clear-files"):
            _clear_uploads()
            st.rerun()

    with st.expander("Datasets", expanded=False):
        render_registered_tables_panel(st.session_state.last_result)

    with st.expander("Session", expanded=False):
        render_runtime_snapshot(status, st.session_state.conversation_state, len(st.session_state.uploaded_contexts))

    with st.expander("Settings", expanded=False):
        provider_label = escape(str(status.provider))
        model_label = escape(str(status.model))
        fallback_label = escape(str(getattr(status, "fallback_provider", None) or "--"))
        st.markdown(
            f"""
            <div class="settings-list">
              <div><strong>Provider</strong><span>{provider_label}</span></div>
              <div><strong>Model</strong><span>{model_label}</span></div>
              <div><strong>Fallback</strong><span>{fallback_label}</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with st.expander("Export", expanded=False):
        st.caption("Export the current conversation as a reviewer-friendly bundle.")
        include_full_metadata = st.checkbox("Include full metadata", value=True, key="export-include-full-metadata")
        include_trace = st.checkbox("Include trace", value=True, key="export-include-trace")
        include_plot_specs = st.checkbox("Include plot specs", value=True, key="export-include-plot-specs")
        compact_markdown_preview = st.checkbox("Compact markdown preview", value=False, key="export-compact-markdown")

        export_bundle = build_conversation_export_bundle(
            st.session_state.conversation_state,
            session_context={
                "provider_status": status,
                "last_result": st.session_state.last_result,
                "uploaded_contexts": st.session_state.uploaded_contexts,
                "settings": settings,
                "last_runtime_snapshot": getattr(st.session_state.last_result, "thread_meta", None) if st.session_state.last_result else None,
            },
            include_full_metadata=include_full_metadata,
            include_workspace=True,
            include_grounding=True,
            include_bindings=True,
            include_completion_validation=True,
            include_normalization=True,
            include_registered_tables=True,
            include_trace=include_trace,
            include_errors=True,
            include_chart_summaries=True,
            include_plot_specs=include_plot_specs,
            max_markdown_table_rows=12 if compact_markdown_preview else 30,
        )
        st.download_button(
            "Export Full Bundle (.zip)",
            data=export_bundle.zip_bytes,
            file_name=export_bundle.archive_name,
            mime="application/zip",
            use_container_width=True,
            key="export-full-bundle",
        )
        st.caption(
            f"Includes {len(export_bundle.turn_json_map)} turn JSON files, markdown, plain text, workspace index, binding index, and error summary."
        )

    st.markdown("</div>", unsafe_allow_html=True)


def _render_status_notice(status: Any) -> None:
    if st.session_state.last_result and st.session_state.last_result.last_error:
        st.markdown(
            f'<div class="status-banner status-banner--error">{escape(str(st.session_state.last_result.last_error))}</div>',
            unsafe_allow_html=True,
        )
    elif not status.connected and not status.fallback_connected:
        st.markdown(
            f'<div class="status-banner status-banner--warn">{escape(status.provider)} is disconnected. Requests will fail until the runtime is available.</div>',
            unsafe_allow_html=True,
        )
    elif not status.connected and status.fallback_connected:
        st.markdown(
            f'<div class="status-banner status-banner--info">{escape(status.provider)} is unavailable, but fallback {escape(getattr(status, "fallback_provider", None) or "--")} is ready.</div>',
            unsafe_allow_html=True,
        )


def _render_composer() -> tuple[bool, bool]:
    st.markdown('<div class="composer-dock">', unsafe_allow_html=True)
    st.markdown(
        """
        <div class="composer-heading">
          <div class="composer-heading__title">Ask anything about your data</div>
          <div class="composer-heading__meta">Assistant replies stay inline with charts, tables, trace, and result objects.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.expander("Attach files", expanded=False):
        st.file_uploader(
            "Attach files",
            accept_multiple_files=True,
            type=list(SUPPORTED_UPLOAD_EXTENSIONS),
            key="composer_uploader_value",
            label_visibility="collapsed",
        )
    st.text_area(
        "Message",
        key="composer_text",
        placeholder="Message Full Stack Data Agent...",
        height=100,
        label_visibility="collapsed",
    )
    send_col, clear_col = st.columns([0.82, 0.18], gap="small")
    send_clicked = send_col.button("Send", type="primary", use_container_width=True)
    clear_clicked = clear_col.button("Clear", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)
    return send_clicked, clear_clicked


def main() -> None:
    configure_page()
    _init_session_state()
    _sync_uploaded_contexts()
    _apply_pending_ui_resets()

    status = get_service().provider_status()
    sidebar_ratio = [0.26, 0.74] if st.session_state.sidebar_open else [0.08, 0.92]
    sidebar_col, main_col = st.columns(sidebar_ratio, gap="medium")

    with sidebar_col:
        _render_sidebar(status)

    with main_col:
        _render_status_notice(status)
        conversation_mount = st.empty()
        _render_conversation(conversation_mount)
        send_clicked, clear_clicked = _render_composer()

        if clear_clicked:
            _clear_chat()
            st.rerun()

        if send_clicked:
            _submit_prompt(st.session_state.composer_text, conversation_mount)
            st.rerun()
