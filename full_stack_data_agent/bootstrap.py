from __future__ import annotations

from full_stack_data_agent.app.chat_service import ChatService
from full_stack_data_agent.config.settings import Settings, get_settings


def bootstrap(settings: Settings | None = None) -> ChatService:
    active_settings = settings or get_settings()
    return ChatService(active_settings)
