from full_stack_data_agent.config.settings import get_settings
from full_stack_data_agent.context.context_packet_builder import ContextPacketBuilder
from full_stack_data_agent.context.conversation_engine import ConversationEngine


def test_context_packet_contains_conversation_and_retrieval() -> None:
    settings = get_settings()
    engine = ConversationEngine(turn_window=4)
    state = engine.create_state()
    engine.add_user_message(state, "hello")

    builder = ContextPacketBuilder(settings, engine)
    packet = builder.build_packet(state, "hello")

    assert packet.conversation_id == state.conversation_id
    assert packet.turn_count == 1
    assert packet.context_summary
