from __future__ import annotations

import json
from typing import Any


def extract_first_json_object_text(text: str) -> str | None:
    raw = str(text or "")
    start = raw.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(raw)):
            char = raw[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return raw[start : index + 1]
        start = raw.find("{", start + 1)
    return None


def extract_first_json_object(text: str) -> dict[str, Any] | None:
    candidate = extract_first_json_object_text(text)
    if candidate is None:
        return None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
