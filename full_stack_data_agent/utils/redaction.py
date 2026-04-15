from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import Any


_SECRET_MARKERS = (
    "api_key",
    "token",
    "secret",
    "password",
    "authorization",
    "bearer",
    "private_key",
    "access_key",
)


def is_secret_key(key: Any) -> bool:
    normalized = str(key or "").strip().lower()
    return any(marker in normalized for marker in _SECRET_MARKERS)


def redact_secret_value(value: Any) -> str:
    if value in (None, ""):
        return ""
    return "***REDACTED***"


def redact_mapping(mapping: Mapping[str, Any], *, _seen: set[int] | None = None) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in mapping.items():
        key_str = str(key)
        if is_secret_key(key_str):
            redacted[key_str] = redact_secret_value(value)
            continue
        redacted[key_str] = redact_secrets(value, _seen=_seen)
    return redacted


def redact_secrets(obj: Any, *, _seen: set[int] | None = None) -> Any:
    if obj is None or isinstance(obj, (bool, int, float, str, bytes, bytearray, memoryview)):
        return obj
    module_name = type(obj).__module__
    if module_name.startswith("pandas") or module_name.startswith("matplotlib"):
        return obj
    seen = _seen if _seen is not None else set()
    obj_id = id(obj)
    if obj_id in seen:
        return {"_type": type(obj).__name__, "_redacted": "circular_reference"}
    seen.add(obj_id)
    if isinstance(obj, Mapping):
        return redact_mapping(obj, _seen=seen)
    if isinstance(obj, list):
        return [redact_secrets(item, _seen=seen) for item in obj]
    if isinstance(obj, tuple):
        return tuple(redact_secrets(item, _seen=seen) for item in obj)
    if isinstance(obj, set):
        return {redact_secrets(item, _seen=seen) for item in obj}
    if is_dataclass(obj):
        return redact_secrets(asdict(obj), _seen=seen)
    return obj
