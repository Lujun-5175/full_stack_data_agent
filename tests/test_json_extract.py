from __future__ import annotations

from full_stack_data_agent.utils.json_extract import extract_first_json_object, extract_first_json_object_text


def test_extract_first_json_object_ignores_prose_and_fenced_blocks() -> None:
    text = """
    Here is the result:

    ```json
    {"kind":"bar","x":"month"}
    ```
    """

    assert extract_first_json_object_text(text) == '{"kind":"bar","x":"month"}'
    assert extract_first_json_object(text) == {"kind": "bar", "x": "month"}


def test_extract_first_json_object_returns_first_complete_blob() -> None:
    text = 'before {"a": 1, "nested": {"b": 2}} middle {"c": 3}'

    assert extract_first_json_object(text) == {"a": 1, "nested": {"b": 2}}


def test_extract_first_json_object_returns_none_for_malformed_payload() -> None:
    text = "```json\n{\"a\": 1\n```"

    assert extract_first_json_object_text(text) is None
    assert extract_first_json_object(text) is None
