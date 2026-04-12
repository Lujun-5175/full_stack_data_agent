from full_stack_data_agent.context.conversation_engine import ConversationEngine


def test_conversation_engine_keeps_recent_messages() -> None:
    engine = ConversationEngine(turn_window=3)
    state = engine.create_state()
    engine.add_user_message(state, "My name is Lujun.")
    engine.add_assistant_message(state, "Nice to meet you, Lujun.")
    engine.add_user_message(state, "What is my name?")

    recent = engine.recent_messages(state)

    assert recent[-1].content == "What is my name?"
    assert any(message.content == "My name is Lujun." for message in recent)
