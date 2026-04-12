from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

import streamlit as st

from full_stack_data_agent.bootstrap import bootstrap
from full_stack_data_agent.config.settings import get_settings
from full_stack_data_agent.context.models import UploadedFileContext
from full_stack_data_agent.context.upload_processor import process_uploaded_file
from full_stack_data_agent.ui.components import (
    render_context_rail_header,
    render_conversation_history,
    render_shell_header,
    render_upload_cards,
    render_registered_tables_panel,
    render_composer_intro,
)
from full_stack_data_agent.ui.theme import get_ui_css


st.set_page_config(page_title="Full Stack Data Agent", layout="wide", initial_sidebar_state="collapsed")
st.markdown(get_ui_css(), unsafe_allow_html=True)

settings = get_settings()
service = bootstrap(settings)


def _init_session_state() -> None:
    defaults = {
        "conversation_state": service.create_state(),
        "last_result": None,
        "uploaded_contexts": [],
        "last_prompt": "",
        "composer_text": "",
        "upload_signature": (),
        "composer_reset_pending": False,
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
    for file_obj in uploaded_files or []:
        raw = file_obj.getvalue()
        item = process_uploaded_file(file_obj.name, raw, getattr(file_obj, "type", None))
        if item is not None:
            processed.append(item)
    return processed, _file_signature(uploaded_files)


def _sync_uploaded_contexts() -> None:
    uploaded_files = st.session_state.get("file_uploader_value")
    processed, signature = _process_uploads(uploaded_files)
    if signature != st.session_state.upload_signature:
        st.session_state.upload_signature = signature
        st.session_state.uploaded_contexts = processed
        if processed:
            st.toast(f"Loaded {len(processed)} file(s) into session context.")
        elif signature == ():
            st.toast("Upload context cleared.")


def _apply_pending_ui_resets() -> None:
    if st.session_state.get("composer_reset_pending"):
        st.session_state["composer_text"] = ""
        st.session_state["composer_reset_pending"] = False


def _submit_prompt(prompt: str) -> None:
    message = prompt.strip()
    if not message:
        return

    st.session_state.last_prompt = message
    with st.spinner("Running the active provider..."):
        state, result = service.send_message(
            st.session_state.conversation_state,
            message,
            uploaded_contexts=st.session_state.uploaded_contexts,
        )
    st.session_state.conversation_state = state
    st.session_state.last_result = result
    st.session_state.composer_reset_pending = True
    st.toast("Run completed.")


def _clear_chat() -> None:
    st.session_state.conversation_state = service.create_state()
    st.session_state.last_result = None
    st.session_state.last_prompt = ""
    st.session_state.composer_reset_pending = True
    st.toast("Session cleared.")


def _render_left_rail(status: Any) -> None:
    st.markdown('<div class="rail-shell">', unsafe_allow_html=True)
    st.markdown('<div class="section">', unsafe_allow_html=True)
    render_context_rail_header(status, st.session_state.conversation_state, len(st.session_state.uploaded_contexts))
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="section">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title">Uploads</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="panel-subtitle">Add files to ground the agent. Supported: text, markdown, CSV, JSON, and code files.</div>',
        unsafe_allow_html=True,
    )
    st.file_uploader(
        "Add files",
        accept_multiple_files=True,
        key="file_uploader_value",
        label_visibility="collapsed",
    )
    render_upload_cards(st.session_state.uploaded_contexts)
    if st.button("Clear uploads", use_container_width=True):
        st.session_state.uploaded_contexts = []
        st.session_state.upload_signature = ()
        if "file_uploader_value" in st.session_state:
            del st.session_state.file_uploader_value
        st.toast("Upload context cleared.")
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="section">', unsafe_allow_html=True)
    render_registered_tables_panel(st.session_state.last_result)
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def _render_right_workspace(status: Any) -> None:
    st.markdown('<div class="main-shell">', unsafe_allow_html=True)

    st.markdown('<div class="section conversation-section">', unsafe_allow_html=True)
    render_conversation_history(st.session_state.conversation_state, st.session_state.last_result)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="section composer-shell">', unsafe_allow_html=True)
    if st.session_state.last_result and st.session_state.last_result.last_error:
        st.error(st.session_state.last_result.last_error)
    elif not status.connected and not status.fallback_connected:
        st.warning(f"{status.provider} is disconnected. The workspace is ready, but runs will fail until the runtime is available.")
    elif not status.connected and status.fallback_connected:
        st.info(f"{status.provider} is disconnected, but fallback {getattr(status, 'fallback_provider', None) or '--'} is available.")
    render_composer_intro()
    st.text_area(
        "Message",
        key="composer_text",
        placeholder="Ask the agent...",
        height=132,
        label_visibility="collapsed",
    )
    action_cols = st.columns([1.0, 0.72])
    if action_cols[0].button("Run", type="primary", use_container_width=True):
        _submit_prompt(st.session_state.composer_text)
        st.rerun()
    if action_cols[1].button("Clear", use_container_width=True):
        _clear_chat()
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)


def main() -> None:
    _init_session_state()
    _sync_uploaded_contexts()
    _apply_pending_ui_resets()

    status = service.provider_status()
    if status.connected:
        pass
    elif status.fallback_connected:
        st.warning(f"Active provider {status.provider} is unavailable; fallback {getattr(status, 'fallback_provider', None) or '--'} is ready.")
    elif status.error:
        st.error(f"Runtime issue: {status.error}")
    render_shell_header(status, st.session_state.conversation_state, len(st.session_state.uploaded_contexts))

    left_col, right_col = st.columns([0.72, 2.8], gap="large")
    with left_col:
        _render_left_rail(status)
    with right_col:
        _render_right_workspace(status)

    st.caption(
        f"Active provider: {status.provider} | model: {status.model} | fallback: {getattr(status, 'fallback_provider', None) or '--'} | uploaded tables stay session-scoped | structured text, table, and plot outputs remain inspectable"
    )


main()
