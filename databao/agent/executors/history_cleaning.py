from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolCall, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately


def _truncate_no_df_block(messages: list[BaseMessage]) -> AIMessage:
    """Returns one AIMessage with only the last message."""
    if not messages:
        raise ValueError("Expected at least one message in block, got empty list.")
    if messages[-1].type != "ai":
        raise ValueError(f"Expected AI message at end of block, got {messages[-1].type!r}.")
    text = f"""Message history was truncated. {len(messages) - 1} messages were deleted.

Here is an answer, which was shown to the user:
""" + str(messages[-1].content)
    return AIMessage(content=text)


def _truncate_block(dfs: dict[str, dict[str, str]], messages: list[BaseMessage]) -> AIMessage:
    """Returns one AIMessage with a summary of the block."""
    if len(messages) < 2:
        raise ValueError(f"Expected at least two messages in block, got {len(messages)}.")
    if not isinstance(messages[-1], ToolMessage):
        raise ValueError(f"Expected ToolMessage at end of block, got {type(messages[-1]).__name__}.")
    if not isinstance(messages[-2], AIMessage):
        raise ValueError(f"Expected AIMessage before ToolMessage, got {type(messages[-2]).__name__}.")
    if not messages[-2].tool_calls:
        raise ValueError("Expected AIMessage to include tool calls before truncating a tool block.")
    df = None
    for d in dfs.values():
        if d.get("query_id") == messages[-2].tool_calls[0]["args"].get("query_id"):
            df = d
            break
    if df is None:
        raise ValueError("Could not find dataframe metadata for the tool block being truncated.")
    tool_call: ToolCall = messages[-2].tool_calls[0]
    text = f"""Message history was truncated. {len(messages) - 1} messages were deleted.

This SQL was generated:
```sql
{df["sql"]}
```

Query ID:
{tool_call["args"]["query_id"]}

Here is an answer, which was shown to the user:
Dataframe:
{df["df"]}

Text:
{tool_call["args"]["result_description"]}
"""
    if tool_call["args"].get("visualization_prompt"):
        text += f"\n\nVisualization prompt: {tool_call['args']['visualization_prompt']}"
    return AIMessage(content=text)


def clean_tool_history(messages: list[BaseMessage], token_limit: int) -> list[BaseMessage]:
    """
    If message history exceeds token limit, truncates it.
    It removes all intermediate messages and changes a final AI message.
    The final message contains SQL, dataframe and text.
    Specific for AgentState and ExecuteSubmit graph.

    Returns: messages ready to be sent to LLM.
    """
    if count_tokens_approximately(messages) < token_limit:
        return messages.copy()

    if not messages:
        raise ValueError("Expected at least one message when cleaning tool history, got empty list.")
    if not isinstance(messages[-1], HumanMessage):
        raise ValueError(f"Expected HumanMessage at end of history, got {type(messages[-1]).__name__}.")

    dfs: dict[str, dict[str, str]] = {}
    buffer = []
    result: list[BaseMessage] = []
    for i in range(len(messages)):
        curr_message = messages[i]
        buffer.append(curr_message)
        if isinstance(curr_message, AIMessage):
            # Fill `dfs` dict
            if curr_message.tool_calls:
                for tool_call in curr_message.tool_calls:
                    if tool_call["name"] == "run_sql_query":
                        call_id = str(tool_call["id"])
                        sql = tool_call["args"]["sql"]
                        dfs[call_id] = {"sql": sql}
            else:
                if len(buffer) > 3:
                    # Long thread with no submission at the end.
                    result.append(_truncate_no_df_block(buffer))
                    buffer = []

        elif isinstance(curr_message, ToolMessage):
            call_id = curr_message.tool_call_id
            if call_id in dfs and curr_message.artifact is not None and "csv" in curr_message.artifact:
                # Enrich `dfs` dict with calculation results
                dfs[call_id]["df"] = curr_message.artifact.get("csv")
                dfs[call_id]["query_id"] = curr_message.artifact.get("query_id")
            elif messages[i - 1].tool_calls[0]["name"] == "submit_result":  # type: ignore
                result.append(_truncate_block(dfs, buffer))
                buffer = []

        else:
            # For system and human messages
            result.extend(buffer)
            buffer = []

    return result
