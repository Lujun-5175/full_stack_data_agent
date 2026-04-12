from __future__ import annotations

from dataclasses import asdict, dataclass, field
from time import time
from typing import Any, Literal
from uuid import uuid4


@dataclass(frozen=True)
class ConversationMessage:
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: float = field(default_factory=time)


@dataclass
class ConversationTurn:
    user_message: ConversationMessage
    assistant_message: ConversationMessage | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    turn_id: str = field(default_factory=lambda: str(uuid4()))
    debug_detailed: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UploadedFileContext:
    file_name: str
    mime_type: str | None
    size_bytes: int
    summary: str
    snippets: list[str] = field(default_factory=list)
    extracted_text: str = ""
    is_tabular: bool = False
    table_name: str | None = None
    row_count: int | None = None
    columns: list[str] = field(default_factory=list)
    semantic_profile: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationState:
    conversation_id: str = field(default_factory=lambda: str(uuid4()))
    turns: list[ConversationTurn] = field(default_factory=list)
    debug_state: dict[str, Any] = field(default_factory=dict)

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    def to_dict(self) -> dict[str, Any]:
        debug_state = {
            key: [asdict(item) if hasattr(item, "__dataclass_fields__") else item for item in value]
            if isinstance(value, list)
            else (asdict(value) if hasattr(value, "__dataclass_fields__") else value)
            for key, value in self.debug_state.items()
        }
        return {
            "conversation_id": self.conversation_id,
            "turn_count": self.turn_count,
            "turns": [asdict(turn) for turn in self.turns],
            "debug_state": debug_state,
        }


@dataclass(frozen=True)
class RetrievedContextItem:
    title: str
    content: str
    score: float | None = None
    source: str = "dce"


@dataclass(frozen=True)
class ContextDebugInfo:
    recent_user_message: str | None
    recent_assistant_message: str | None
    retrieval_mode: str
    retrieval_error: str | None = None


@dataclass(frozen=True)
class ContextPacket:
    conversation_id: str
    turn_count: int
    recent_messages: list[ConversationMessage]
    context_summary: str
    retrieved_contexts: list[RetrievedContextItem]
    debug: ContextDebugInfo
    uploaded_contexts: list[UploadedFileContext] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "turn_count": self.turn_count,
            "recent_messages": [asdict(message) for message in self.recent_messages],
            "context_summary": self.context_summary,
            "retrieved_contexts": [asdict(item) for item in self.retrieved_contexts],
            "uploaded_contexts": [asdict(item) for item in self.uploaded_contexts],
            "debug": asdict(self.debug),
            "artifacts": self.artifacts,
        }
