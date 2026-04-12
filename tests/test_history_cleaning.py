from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from databao.agent.executors.history_cleaning import _truncate_block, _truncate_no_df_block, clean_tool_history


def test_clean_tool_history_requires_human_message_at_end() -> None:
    with pytest.raises(ValueError, match="Expected HumanMessage"):
        clean_tool_history([AIMessage(content="assistant")], token_limit=0)


def test_clean_tool_history_rejects_empty_history() -> None:
    with pytest.raises(ValueError, match="empty list"):
        clean_tool_history([], token_limit=0)


def test_truncate_no_df_block_rejects_non_ai_tail() -> None:
    with pytest.raises(ValueError, match="Expected AI message at end of block"):
        _truncate_no_df_block([HumanMessage(content="user")])


def test_truncate_block_rejects_missing_tool_tail() -> None:
    with pytest.raises(ValueError, match="Expected ToolMessage at end of block"):
        _truncate_block({}, [AIMessage(content="assistant"), HumanMessage(content="user")])


def test_truncate_block_rejects_missing_dataframe_metadata() -> None:
    tool_message = ToolMessage(content="tool", tool_call_id="call-1")
    ai_message = AIMessage(content="", tool_calls=[{"id": "call-1", "name": "submit_result", "args": {"query_id": "q-1"}}])

    with pytest.raises(ValueError, match="Could not find dataframe metadata"):
        _truncate_block({}, [ai_message, tool_message])
