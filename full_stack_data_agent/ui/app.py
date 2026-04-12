from __future__ import annotations

import json

import streamlit as st

from full_stack_data_agent.bootstrap import bootstrap
from full_stack_data_agent.config.settings import get_settings


st.set_page_config(page_title="Full Stack Data Agent", layout="wide")

settings = get_settings()
service = bootstrap(settings)

if "conversation_state" not in st.session_state:
    st.session_state.conversation_state = service.create_state()
if "last_result" not in st.session_state:
    st.session_state.last_result = None


def send() -> None:
    user_input = st.session_state.get("chat_input_value", "").strip()
    if not user_input:
        return
    state, result = service.send_message(st.session_state.conversation_state, user_input)
    st.session_state.conversation_state = state
    st.session_state.last_result = result
    st.session_state.chat_input_value = ""


status = service.provider_status()
st.title("Full Stack Data Agent")

status_cols = st.columns(4)
status_cols[0].metric("Provider", status.provider)
status_cols[1].metric("Model", status.model)
status_cols[2].metric("Ollama", "Connected" if status.connected else "Disconnected")
status_cols[3].metric("Source", "local Gemma 4 via Ollama")

left, right = st.columns([2, 1])

with left:
    st.subheader("Chat")
    if st.session_state.last_result:
        for message in st.session_state.last_result.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
    st.chat_input("Send a message", key="chat_input_value", on_submit=send)
    button_cols = st.columns(2)
    if button_cols[0].button("Send", use_container_width=True):
        send()
    if button_cols[1].button("Clear conversation", use_container_width=True):
        st.session_state.conversation_state = service.create_state()
        st.session_state.last_result = None
        st.rerun()

with right:
    st.subheader("Model Status")
    st.json(status.to_dict())
    st.subheader("Conversation")
    st.json(st.session_state.conversation_state.to_dict())
    if st.session_state.last_result:
        st.subheader("Context Packet")
        st.json(st.session_state.last_result.last_context_packet)
        st.subheader("Agent Request")
        st.json(st.session_state.last_result.last_agent_request)
        st.subheader("Agent Response")
        st.json(st.session_state.last_result.last_agent_response)
        if st.session_state.last_result.last_error:
            st.subheader("Error")
            st.error(st.session_state.last_result.last_error)

st.divider()
st.subheader("Debug")
debug_payload = {
    "provider_connected": status.connected,
    "available_models": status.available_models,
    "settings": {
        "ollama_base_url": settings.ollama_base_url,
        "ollama_model": settings.ollama_model,
        "domain_dir": str(settings.domain_dir),
    },
}
if st.session_state.last_result:
    debug_payload["last_result_summary"] = {
        "message_count": len(st.session_state.last_result.messages),
        "last_error": st.session_state.last_result.last_error,
    }
st.code(json.dumps(debug_payload, indent=2), language="json")
