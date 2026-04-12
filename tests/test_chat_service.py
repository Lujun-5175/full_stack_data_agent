from full_stack_data_agent.app.chat_service import ChatService
from full_stack_data_agent.config.settings import get_settings


def test_chat_service_creates_state() -> None:
    service = ChatService(get_settings())

    state = service.create_state()

    assert state.conversation_id
    assert state.turn_count == 0
