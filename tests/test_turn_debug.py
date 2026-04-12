from full_stack_data_agent.context.conversation_engine import ConversationEngine


def test_turn_debug_details_can_be_attached() -> None:
    engine = ConversationEngine(turn_window=3)
    state = engine.create_state()
    engine.add_user_message(state, "hello")
    engine.add_assistant_message(state, "hi")

    state.turns[-1].debug_detailed = {"debug": {"step": "attached"}}

    assert state.turns[-1].debug_detailed["debug"]["step"] == "attached"
