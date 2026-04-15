from __future__ import annotations

from dataclasses import replace

from full_stack_data_agent.context.models import ConversationMessage, ConversationState, ConversationTurn


class ConversationEngine:
    def __init__(self, turn_window: int):
        self._turn_window = turn_window

    def create_state(self) -> ConversationState:
        return ConversationState()

    def add_user_message(self, state: ConversationState, content: str) -> ConversationState:
        state.turns.append(ConversationTurn(user_message=ConversationMessage(role="user", content=content)))
        return state

    def add_assistant_message(
        self,
        state: ConversationState,
        content: str,
        metadata: dict | None = None,
    ) -> ConversationState:
        if not state.turns:
            raise ValueError("Cannot add an assistant message before a user message.")
        last_turn = state.turns[-1]
        state.turns[-1] = replace(
            last_turn,
            assistant_message=ConversationMessage(role="assistant", content=content),
            metadata=metadata or {},
        )
        return state

    def begin_assistant_message(
        self,
        state: ConversationState,
        *,
        content: str = "",
        metadata: dict | None = None,
    ) -> ConversationState:
        if not state.turns:
            raise ValueError("Cannot add an assistant message before a user message.")
        last_turn = state.turns[-1]
        state.turns[-1] = replace(
            last_turn,
            assistant_message=ConversationMessage(role="assistant", content=content, status="streaming"),
            metadata=metadata or {},
        )
        return state

    def update_assistant_message(
        self,
        state: ConversationState,
        content: str,
        *,
        metadata: dict | None = None,
    ) -> ConversationState:
        if not state.turns or state.turns[-1].assistant_message is None:
            raise ValueError("Cannot update an assistant message before it exists.")
        last_turn = state.turns[-1]
        assistant_message = last_turn.assistant_message
        state.turns[-1] = replace(
            last_turn,
            assistant_message=replace(assistant_message, content=content, status="streaming"),
            metadata=metadata if metadata is not None else last_turn.metadata,
        )
        return state

    def finalize_assistant_message(
        self,
        state: ConversationState,
        content: str,
        *,
        metadata: dict | None = None,
        status: str = "complete",
    ) -> ConversationState:
        if not state.turns or state.turns[-1].assistant_message is None:
            raise ValueError("Cannot finalize an assistant message before it exists.")
        last_turn = state.turns[-1]
        assistant_message = last_turn.assistant_message
        state.turns[-1] = replace(
            last_turn,
            assistant_message=replace(assistant_message, content=content, status=status),
            metadata=metadata if metadata is not None else last_turn.metadata,
        )
        return state

    def recent_messages(self, state: ConversationState) -> list[ConversationMessage]:
        recent_turns = state.turns[-self._turn_window :]
        messages: list[ConversationMessage] = []
        for turn in recent_turns:
            messages.append(turn.user_message)
            if turn.assistant_message:
                messages.append(turn.assistant_message)
        return messages

    def summarize(self, state: ConversationState) -> str:
        if not state.turns:
            return "No conversation turns yet."
        summaries: list[str] = []
        start_index = max(1, state.turn_count - self._turn_window + 1)
        for offset, turn in enumerate(state.turns[-self._turn_window :], start=start_index):
            assistant = turn.assistant_message.content if turn.assistant_message else "(pending)"
            summaries.append(f"Turn {offset}: user={turn.user_message.content!r}; assistant={assistant!r}")
        return "\n".join(summaries)
