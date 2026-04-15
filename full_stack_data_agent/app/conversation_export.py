from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import date, datetime, time as time_cls
import base64
import io
import json
from importlib import metadata as importlib_metadata
from pathlib import Path
import platform
import subprocess
import zipfile
from typing import Any, Mapping, Sequence

import pandas as pd

try:  # optional dependency
    import numpy as np
except Exception:  # pragma: no cover
    np = None

from full_stack_data_agent.context.models import ConversationState, ConversationTurn, UploadedFileContext
from full_stack_data_agent.utils.redaction import redact_secrets


_MAX_REPR_CHARS = 240
_DEFAULT_MAX_DEPTH = 6
_DEFAULT_MAX_ITEMS = 60


@dataclass(frozen=True)
class ConversationExportBundle:
    archive_name: str
    exported_at: str
    markdown_report: str
    plain_text_report: str
    full_json: dict[str, Any]
    turn_json_map: dict[str, dict[str, Any]]
    errors_and_warnings: str
    files: dict[str, bytes]
    zip_bytes: bytes


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 22)]}... (truncated, len={len(text)})"


def _safe_repr(obj: Any, *, limit: int = _MAX_REPR_CHARS) -> str:
    try:
        text = repr(obj)
    except Exception:
        text = f"<unreprable {type(obj).__name__}>"
    return _truncate_text(text, limit)


def _json_safe_key(key: Any) -> str:
    if isinstance(key, str):
        return key
    return _safe_repr(key, limit=80)


def _is_numpy_scalar(obj: Any) -> bool:
    if np is not None and isinstance(obj, np.generic):
        return True
    return hasattr(obj, "item") and type(obj).__module__.startswith("numpy")


def _safe_datetime(obj: Any) -> str | None:
    if isinstance(obj, (datetime, date, time_cls)):
        return obj.isoformat()
    if hasattr(obj, "isoformat") and callable(obj.isoformat):
        try:
            return str(obj.isoformat())
        except Exception:
            return None
    return None


def _dataframe_summary(df: pd.DataFrame, *, max_rows: int = 20) -> dict[str, Any]:
    preview = df.head(max_rows).to_dict(orient="records")
    dtypes = {}
    for column in df.columns:
        try:
            dtypes[str(column)] = str(df[column].dtype)
        except Exception:
            dtypes[str(column)] = "<dtype unavailable>"
    return {
        "_type": "pandas.DataFrame",
        "shape": [int(df.shape[0]), int(df.shape[1])],
        "columns": [str(column) for column in df.columns],
        "dtypes": dtypes,
        "preview": make_json_safe(preview, max_depth=3, max_items=max_rows),
        "preview_row_count": int(len(preview)),
        "has_duplicate_columns": bool(not df.columns.is_unique),
    }


def _series_summary(series: pd.Series, *, max_rows: int = 20) -> dict[str, Any]:
    return {
        "_type": "pandas.Series",
        "name": str(series.name) if series.name is not None else None,
        "dtype": str(series.dtype),
        "length": int(len(series)),
        "preview": make_json_safe(series.head(max_rows).tolist(), max_depth=3, max_items=max_rows),
    }


def _bytes_summary(data: bytes | bytearray | memoryview, *, limit: int = 128) -> dict[str, Any]:
    raw = bytes(data)
    preview = base64.b64encode(raw[:limit]).decode("ascii") if raw else ""
    return {
        "_type": type(data).__name__,
        "length": len(raw),
        "preview_base64": preview,
        "truncated": len(raw) > limit,
    }


def make_json_safe(
    obj: Any,
    *,
    max_depth: int = _DEFAULT_MAX_DEPTH,
    max_items: int = _DEFAULT_MAX_ITEMS,
    max_text_chars: int = 8000,
    _depth: int = 0,
    _seen: set[int] | None = None,
) -> Any:
    if _seen is None:
        _seen = set()

    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return _truncate_text(obj, max_text_chars)
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return _bytes_summary(obj)
    if _is_numpy_scalar(obj):
        try:
            return obj.item()
        except Exception:
            return _safe_repr(obj)
    if isinstance(obj, (datetime, date, time_cls)):
        return obj.isoformat()

    obj_id = id(obj)
    if obj_id in _seen:
        return {"_type": type(obj).__name__, "_circular_reference": True}
    if _depth >= max_depth:
        return {"_type": type(obj).__name__, "_repr": _safe_repr(obj), "_max_depth_reached": True}

    if isinstance(obj, pd.DataFrame):
        return _dataframe_summary(obj)
    if isinstance(obj, pd.Series):
        return _series_summary(obj)
    if isinstance(obj, pd.Index):
        values = obj.tolist()
        return {
            "_type": "pandas.Index",
            "length": int(len(obj)),
            "preview": make_json_safe(values[:max_items], max_depth=max_depth, max_items=max_items, max_text_chars=max_text_chars, _depth=_depth + 1, _seen=_seen),
        }
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()

    module_name = type(obj).__module__
    if (
        isinstance(obj, (Mapping, list, tuple, set, frozenset))
        or is_dataclass(obj)
    ) and not (module_name.startswith("pandas") or module_name.startswith("matplotlib")):
        obj = redact_secrets(obj)

    if is_dataclass(obj):
        _seen.add(obj_id)
        payload = {}
        for field in fields(obj):
            payload[field.name] = make_json_safe(
                getattr(obj, field.name),
                max_depth=max_depth,
                max_items=max_items,
                max_text_chars=max_text_chars,
                _depth=_depth + 1,
                _seen=_seen,
            )
        _seen.remove(obj_id)
        return payload

    if isinstance(obj, Mapping):
        _seen.add(obj_id)
        items = list(obj.items())[:max_items]
        payload = {
            _json_safe_key(key): make_json_safe(
                value,
                max_depth=max_depth,
                max_items=max_items,
                max_text_chars=max_text_chars,
                _depth=_depth + 1,
                _seen=_seen,
            )
            for key, value in items
        }
        if len(obj) > max_items:
            payload["__truncated__"] = len(obj) - max_items
        _seen.remove(obj_id)
        return payload

    if isinstance(obj, (list, tuple, set, frozenset)):
        _seen.add(obj_id)
        values = list(obj)[:max_items]
        payload = [
            make_json_safe(
                value,
                max_depth=max_depth,
                max_items=max_items,
                max_text_chars=max_text_chars,
                _depth=_depth + 1,
                _seen=_seen,
            )
            for value in values
        ]
        if len(obj) > max_items:
            payload.append({"__truncated__": len(obj) - max_items})
        _seen.remove(obj_id)
        return payload

    safe_dt = _safe_datetime(obj)
    if safe_dt is not None:
        return safe_dt

    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            converted = to_dict()
        except Exception:
            converted = None
        if converted is not None and converted is not obj:
            return make_json_safe(
                converted,
                max_depth=max_depth,
                max_items=max_items,
                max_text_chars=max_text_chars,
                _depth=_depth + 1,
                _seen=_seen,
            )

    if hasattr(obj, "__dict__"):
        _seen.add(obj_id)
        payload = {"_type": type(obj).__name__, "_repr": _safe_repr(obj)}
        for key, value in list(vars(obj).items())[:max_items]:
            payload[str(key)] = make_json_safe(
                value,
                max_depth=max_depth,
                max_items=max_items,
                max_text_chars=max_text_chars,
                _depth=_depth + 1,
                _seen=_seen,
            )
        _seen.remove(obj_id)
        return payload

    return {"_type": type(obj).__name__, "_repr": _safe_repr(obj)}


def _normalize_turns(conversation_turns: ConversationState | Sequence[ConversationTurn]) -> tuple[str | None, list[ConversationTurn], dict[str, Any]]:
    if isinstance(conversation_turns, ConversationState):
        return conversation_turns.conversation_id, list(conversation_turns.turns), dict(conversation_turns.debug_state)
    return None, list(conversation_turns), {}


def _extract_uploaded_names(session_context: Mapping[str, Any] | None) -> list[str]:
    if not session_context:
        return []
    uploaded = session_context.get("uploaded_contexts")
    names: list[str] = []
    if isinstance(uploaded, Sequence) and not isinstance(uploaded, (str, bytes, bytearray)):
        for item in uploaded:
            if isinstance(item, UploadedFileContext):
                names.append(item.file_name)
            elif isinstance(item, Mapping):
                name = item.get("file_name") or item.get("table_name") or item.get("name")
                if name:
                    names.append(str(name))
            else:
                name = getattr(item, "file_name", None) or getattr(item, "table_name", None) or getattr(item, "name", None)
                if name:
                    names.append(str(name))
    return sorted(set(names))


def _package_version() -> str | None:
    try:
        return importlib_metadata.version("full-stack-data-agent")
    except Exception:
        return "0.1.0"


def _git_hash() -> str | None:
    repo_root = Path(__file__).resolve().parents[2]
    try:
        output = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return None
    return output.strip() or None


def _conversation_session_summary(
    state: ConversationState,
    *,
    session_context: Mapping[str, Any] | None,
    exported_at: str,
) -> dict[str, Any]:
    provider_status = make_json_safe(session_context.get("provider_status") if session_context else None)
    raw_settings = session_context.get("settings") if session_context else None
    settings = None
    if raw_settings is not None:
        settings = make_json_safe(
            {
                "provider_name": getattr(raw_settings, "provider_name", None),
                "fallback_provider_name": getattr(raw_settings, "fallback_provider_name", None),
                "active_model": getattr(raw_settings, "active_model", None),
                "active_base_url": getattr(raw_settings, "active_base_url", None),
                "active_api_key_present": bool(getattr(raw_settings, "active_api_key", None)),
            }
        )
    last_result = session_context.get("last_result") if session_context else None
    last_runtime_snapshot = session_context.get("last_runtime_snapshot") if session_context else None
    session_debug = make_json_safe(state.debug_state)
    return {
        "exported_at": exported_at,
        "conversation_id": state.conversation_id,
        "turn_count": state.turn_count,
        "provider_status": provider_status,
        "settings": settings,
        "app_version": _package_version(),
        "git_hash": _git_hash(),
        "platform": platform.platform(),
        "uploaded_files": _extract_uploaded_names(session_context),
        "debug_state": session_debug,
        "last_result": make_json_safe(last_result) if last_result is not None else None,
        "last_runtime_snapshot": make_json_safe(last_runtime_snapshot) if last_runtime_snapshot is not None else None,
    }


def _flatten_strings(obj: Any, *, path: str = "", lines: list[str] | None = None, seen: set[int] | None = None) -> list[str]:
    if lines is None:
        lines = []
    if seen is None:
        seen = set()

    if obj is None:
        return lines
    if isinstance(obj, str):
        if obj.strip():
            lines.append(f"{path}: {obj.strip()}" if path else obj.strip())
        return lines
    if isinstance(obj, (int, float, bool)):
        return lines

    obj_id = id(obj)
    if obj_id in seen:
        return lines
    seen.add(obj_id)

    if isinstance(obj, Mapping):
        for key, value in obj.items():
            next_path = f"{path}.{key}" if path else str(key)
            _flatten_strings(value, path=next_path, lines=lines, seen=seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for index, value in enumerate(obj):
            next_path = f"{path}[{index}]" if path else f"[{index}]"
            _flatten_strings(value, path=next_path, lines=lines, seen=seen)
    elif is_dataclass(obj):
        _flatten_strings(asdict(obj), path=path, lines=lines, seen=seen)
    elif hasattr(obj, "__dict__"):
        _flatten_strings(vars(obj), path=path, lines=lines, seen=seen)
    return lines


def _collect_issues_from_payload(obj: Any, *, turn_index: int, role: str) -> list[str]:
    flattened = _flatten_strings(obj)
    issues: list[str] = []
    interesting = ("error", "warning", "warn", "failed", "fallback", "retry", "validation", "render")
    for item in flattened:
        lowered = item.casefold()
        if any(token in lowered for token in interesting):
            issues.append(f"Turn {turn_index} [{role}] {item}")
    return issues


def _errors_text_for_turn(turn_index: int, turn: ConversationTurn, metadata: Mapping[str, Any], debug: Mapping[str, Any]) -> str:
    issues = []
    role = "assistant" if turn.assistant_message else "user"
    issues.extend(_collect_issues_from_payload(metadata, turn_index=turn_index, role=role))
    issues.extend(_collect_issues_from_payload(debug, turn_index=turn_index, role=role))
    seen: set[str] = set()
    unique: list[str] = []
    for item in issues:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return "\n".join(unique)


def _extract_workspace_index(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    workspace = metadata.get("result_workspace")
    if isinstance(workspace, Mapping):
        return make_json_safe(workspace)
    to_dict = getattr(workspace, "to_dict", None)
    if callable(to_dict):
        try:
            converted = to_dict()
        except Exception:
            converted = None
        if isinstance(converted, dict):
            return make_json_safe(converted)
    return None


def _extract_binding_index(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    keys = ("binding_intent", "binding_bundle", "binding_decisions", "decision_mode", "llm_used")
    payload = {key: metadata.get(key) for key in keys if metadata.get(key) is not None}
    if not payload:
        return None
    return make_json_safe(payload)


def _extract_chart_summary(metadata: Mapping[str, Any]) -> dict[str, Any]:
    grounded = metadata.get("grounded_response") if isinstance(metadata.get("grounded_response"), Mapping) else {}
    chart_debug = metadata.get("chart_debug") if isinstance(metadata.get("chart_debug"), Mapping) else {}
    return {
        "primary_chart_artifact_id": metadata.get("primary_chart_artifact_id") or (grounded or {}).get("primary_chart_artifact_id"),
        "plot_backend": metadata.get("plot_backend"),
        "plot_kind": metadata.get("plot_kind"),
        "plot_error": metadata.get("plot_error"),
        "render_failed": bool((chart_debug or {}).get("planner_status") == "render_failed" or metadata.get("plot_error")),
        "chart_debug": make_json_safe(chart_debug),
    }


def _extract_sql_trace(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    keys = (
        "query_obligations",
        "parsed_sql_summary",
        "sql_guardrail_report",
        "execution_validation_report",
        "sql_retry_history",
        "final_guardrail_status",
        "repair_attempts",
        "final_failure_reason",
    )
    payload = {key: metadata.get(key) for key in keys if metadata.get(key) is not None}
    if not payload:
        return None
    return make_json_safe(payload)


def _turn_workspace_snapshot(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    workspace = _extract_workspace_index(metadata)
    if workspace is None:
        return None
    return workspace


def _turn_artifacts(metadata: Mapping[str, Any]) -> dict[str, Any]:
    grounded = metadata.get("grounded_response") if isinstance(metadata.get("grounded_response"), Mapping) else {}
    return {
        "primary_artifact_id": metadata.get("primary_artifact_id") or (grounded or {}).get("primary_text_artifact_id"),
        "primary_table_artifact_id": metadata.get("primary_table_artifact_id") or (grounded or {}).get("primary_table_artifact_id"),
        "primary_chart_artifact_id": metadata.get("primary_chart_artifact_id") or (grounded or {}).get("primary_chart_artifact_id"),
        "followup_target_artifact_id": metadata.get("followup_target_artifact_id") or (grounded or {}).get("followup_target_artifact_id"),
        "grounded_response": make_json_safe(grounded),
        "binding_trace": _extract_binding_index(metadata),
        "sql_trace": _extract_sql_trace(metadata),
        "chart_summary": _extract_chart_summary(metadata),
    }


def _table_preview_from_turn(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    grounded = metadata.get("grounded_response") if isinstance(metadata.get("grounded_response"), Mapping) else {}
    workspace = metadata.get("result_workspace") if isinstance(metadata.get("result_workspace"), Mapping) else {}
    artifacts = workspace.get("artifacts") if isinstance(workspace, Mapping) else []
    if not isinstance(artifacts, list):
        artifacts = []
    table_id = metadata.get("primary_table_artifact_id") or (grounded or {}).get("primary_table_artifact_id")
    if table_id:
        for artifact in artifacts:
            if isinstance(artifact, Mapping) and artifact.get("artifact_id") == table_id:
                preview = artifact.get("dataframe_preview") or artifact.get("preview")
                return {
                    "label": "Table preview",
                    "rows": preview or [],
                    "row_count": artifact.get("row_count"),
                    "columns": artifact.get("columns") or [],
                }
    preview = metadata.get("dataframe_preview")
    if preview:
        return {
            "label": "Table preview",
            "rows": preview,
            "row_count": metadata.get("row_count"),
            "columns": metadata.get("columns") or [],
        }
    return None


def _render_markdown_table(rows: Sequence[Mapping[str, Any]], *, max_rows: int) -> str:
    visible_rows = list(rows[:max_rows])
    if not visible_rows:
        return "Not available"
    headers: list[str] = []
    seen: set[str] = set()
    for row in visible_rows:
        for key in row.keys():
            key_str = str(key)
            if key_str not in seen:
                headers.append(key_str)
                seen.add(key_str)
    if not headers:
        return "Not available"

    def _cell(value: Any) -> str:
        if value is None:
            return ""
        return _truncate_text(str(value), 120).replace("\n", " ")

    table = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in visible_rows:
        table.append("| " + " | ".join(_cell(row.get(header)) for header in headers) + " |")
    if len(rows) > max_rows:
        table.append(f"\n*Preview truncated to {max_rows} rows of {len(rows)} total rows.*")
    return "\n".join(table)


def _turn_text(turn: ConversationTurn) -> dict[str, str]:
    return {
        "user": _truncate_text(turn.user_message.content, 8000),
        "assistant": _truncate_text(turn.assistant_message.content, 8000) if turn.assistant_message else "",
    }


def _turn_export(
    turn_index: int,
    turn: ConversationTurn,
    *,
    max_text_chars: int,
    include_full_metadata: bool,
    include_workspace: bool,
    include_grounding: bool,
    include_bindings: bool,
    include_completion_validation: bool,
    include_normalization: bool,
    include_registered_tables: bool,
    include_trace: bool,
    include_errors: bool,
    include_chart_summaries: bool,
    include_plot_specs: bool,
) -> dict[str, Any]:
    metadata = turn.metadata if isinstance(turn.metadata, Mapping) else {}
    debug = turn.debug_detailed if isinstance(turn.debug_detailed, Mapping) else {}
    role = "assistant" if turn.assistant_message else "user"
    payload: dict[str, Any] = {
        "turn_index": turn_index,
        "turn_id": turn.turn_id,
        "role": role,
        "user_message": make_json_safe(turn.user_message, max_text_chars=max_text_chars),
        "assistant_message": make_json_safe(turn.assistant_message, max_text_chars=max_text_chars) if turn.assistant_message else None,
        "timestamp": {
            "user": turn.user_message.created_at,
            "assistant": turn.assistant_message.created_at if turn.assistant_message else None,
        },
        "text": _turn_text(turn),
        "metadata_keys": sorted(str(key) for key in metadata.keys()),
    }
    if include_full_metadata:
        payload["metadata"] = make_json_safe(metadata, max_text_chars=max_text_chars)
    if include_trace:
        payload["debug_detailed"] = make_json_safe(debug, max_text_chars=max_text_chars)
    if include_workspace:
        payload["workspace"] = _turn_workspace_snapshot(metadata)
    if include_grounding:
        payload["grounded_response"] = make_json_safe(metadata.get("grounded_response"), max_text_chars=max_text_chars)
    if include_bindings:
        payload["binding_trace"] = _extract_binding_index(metadata)
    payload["sql_trace"] = _extract_sql_trace(metadata)
    if include_completion_validation:
        payload["completion_validation"] = make_json_safe(metadata.get("completion_validation"), max_text_chars=max_text_chars)
    if include_normalization:
        payload["normalization_reports"] = make_json_safe(metadata.get("normalization_reports"), max_text_chars=max_text_chars)
    if include_registered_tables:
        payload["registered_tables"] = make_json_safe(metadata.get("registered_tables"), max_text_chars=max_text_chars)
    if include_chart_summaries:
        payload["chart_summary"] = _extract_chart_summary(metadata)
    if include_plot_specs:
        payload["plot_plan"] = make_json_safe(metadata.get("plot_plan"), max_text_chars=max_text_chars)
        payload["plot_spec"] = make_json_safe(metadata.get("plot_spec"), max_text_chars=max_text_chars)
        payload["plot_data"] = make_json_safe(metadata.get("plot_data"), max_text_chars=max_text_chars)
        payload["plot_meta"] = make_json_safe(metadata.get("plot_meta"), max_text_chars=max_text_chars)
        payload["plot_backend"] = metadata.get("plot_backend")
        payload["plot_kind"] = metadata.get("plot_kind")
        payload["plot_error"] = metadata.get("plot_error")
        payload["plot_image_base64_present"] = bool(metadata.get("plot_image_base64"))
    if include_errors:
        payload["errors"] = _errors_text_for_turn(turn_index, turn, metadata, debug).splitlines()
    return payload


def _markdown_turn(turn_export: Mapping[str, Any], *, max_markdown_table_rows: int) -> str:
    metadata = turn_export.get("metadata") if isinstance(turn_export.get("metadata"), Mapping) else {}
    grounding = turn_export.get("grounded_response") if isinstance(turn_export.get("grounded_response"), Mapping) else {}
    workspace = turn_export.get("workspace") if isinstance(turn_export.get("workspace"), Mapping) else {}
    binding_trace = turn_export.get("binding_trace") if isinstance(turn_export.get("binding_trace"), Mapping) else {}
    completion = turn_export.get("completion_validation") if isinstance(turn_export.get("completion_validation"), Mapping) else {}
    sql_trace = turn_export.get("sql_trace") if isinstance(turn_export.get("sql_trace"), Mapping) else {}
    chart_summary = turn_export.get("chart_summary") if isinstance(turn_export.get("chart_summary"), Mapping) else {}
    table_preview = _table_preview_from_turn(metadata)
    errors = turn_export.get("errors") or []
    deliverables = {
        "table_present": bool((grounding or {}).get("primary_table_artifact_id") or metadata.get("dataframe_preview")),
        "chart_present": bool((grounding or {}).get("primary_chart_artifact_id") or metadata.get("plot_plan") or metadata.get("plot_spec") or metadata.get("plot_image_base64") or metadata.get("plot_backend")),
        "explain_present": bool((grounding or {}).get("primary_explain_artifact_id")),
        "completion_status": completion.get("status") if isinstance(completion, Mapping) else None,
        "chart_render_status": (chart_summary.get("chart_debug") or {}).get("planner_status") if isinstance(chart_summary, Mapping) else None,
    }
    return "\n\n".join(
        [
            f"## Turn {turn_export['turn_index']}",
            "### User",
            str(turn_export.get("text", {}).get("user") or "Not available"),
            "### Assistant",
            str(turn_export.get("text", {}).get("assistant") or "Not available"),
            "### Deliverables Summary",
            "\n".join(f"- {key}: {value if value is not None else 'Not available'}" for key, value in deliverables.items()),
            "### Table Preview",
            _render_markdown_table((table_preview or {}).get("rows") or [], max_rows=max_markdown_table_rows),
            "### Chart Summary",
            "```json\n" + json.dumps(chart_summary or "Not available", ensure_ascii=False, indent=2) + "\n```",
            "### Result Workspace",
            "```json\n" + json.dumps(workspace or "Not available", ensure_ascii=False, indent=2) + "\n```",
            "### Grounded Response",
            "```json\n" + json.dumps(grounding or "Not available", ensure_ascii=False, indent=2) + "\n```",
            "### Primary Bindings",
            "```json\n" + json.dumps(binding_trace or "Not available", ensure_ascii=False, indent=2) + "\n```",
            "### Completion Validation",
            "```json\n" + json.dumps(completion or "Not available", ensure_ascii=False, indent=2) + "\n```",
            "### SQL Guardrail Trace",
            "```json\n" + json.dumps(sql_trace or "Not available", ensure_ascii=False, indent=2) + "\n```",
            "### Errors / Warnings",
            "\n".join(f"- {line}" for line in errors) if errors else "Not available",
            "### Raw Metadata Keys",
            ", ".join(sorted(str(key) for key in metadata.keys())) or "Not available",
        ]
    )


def _plain_turn(turn_export: Mapping[str, Any]) -> str:
    metadata = turn_export.get("metadata") if isinstance(turn_export.get("metadata"), Mapping) else {}
    grounding = turn_export.get("grounded_response") if isinstance(turn_export.get("grounded_response"), Mapping) else {}
    completion = turn_export.get("completion_validation") if isinstance(turn_export.get("completion_validation"), Mapping) else {}
    sql_trace = turn_export.get("sql_trace") if isinstance(turn_export.get("sql_trace"), Mapping) else {}
    chart_summary = turn_export.get("chart_summary") if isinstance(turn_export.get("chart_summary"), Mapping) else {}
    errors = turn_export.get("errors") or []
    lines = [
        f"Turn {turn_export['turn_index']}",
        "User:",
        str(turn_export.get("text", {}).get("user") or "Not available"),
        "Assistant:",
        str(turn_export.get("text", {}).get("assistant") or "Not available"),
        "Deliverables Summary:",
        f"- table_present: {bool((grounding or {}).get('primary_table_artifact_id') or metadata.get('dataframe_preview'))}",
        f"- chart_present: {bool((grounding or {}).get('primary_chart_artifact_id') or metadata.get('plot_plan') or metadata.get('plot_spec') or metadata.get('plot_image_base64') or metadata.get('plot_backend'))}",
        f"- explain_present: {bool((grounding or {}).get('primary_explain_artifact_id'))}",
        f"- completion_status: {completion.get('status') if isinstance(completion, Mapping) else 'Not available'}",
        f"- chart_render_status: {(chart_summary.get('chart_debug') or {}).get('planner_status') if isinstance(chart_summary, Mapping) else 'Not available'}",
        f"- sql_guardrail_status: {(sql_trace.get('sql_guardrail_report') or {}).get('status') if isinstance(sql_trace, Mapping) else 'Not available'}",
        "Errors / Warnings:",
        "\n".join(f"- {line}" for line in errors) if errors else "Not available",
        "",
    ]
    return "\n".join(lines)


def export_conversation_markdown(
    conversation_turns: Sequence[ConversationTurn],
    *,
    conversation_id: str | None,
    exported_at: str,
    session_summary: Mapping[str, Any],
    turn_exports: Sequence[Mapping[str, Any]] | None = None,
    max_markdown_table_rows: int = 30,
    max_text_chars: int = 8000,
) -> str:
    turn_exports = list(turn_exports or [])
    if not turn_exports:
        turn_exports = [
            _turn_export(
                index,
                turn,
                max_text_chars=max_text_chars,
                include_full_metadata=True,
                include_workspace=True,
                include_grounding=True,
                include_bindings=True,
                include_completion_validation=True,
                include_normalization=True,
                include_registered_tables=True,
                include_trace=True,
                include_errors=True,
                include_chart_summaries=True,
                include_plot_specs=True,
            )
            for index, turn in enumerate(conversation_turns, start=1)
        ]

    lines = [
        "# Conversation Export Report",
        "",
        f"- Exported at: {exported_at}",
        f"- Conversation ID: {conversation_id or session_summary.get('conversation_id') or 'Not available'}",
        f"- Turn count: {len(turn_exports)}",
        f"- Dataset(s): {', '.join(session_summary.get('uploaded_files') or []) or 'Not available'}",
        f"- App version: {session_summary.get('app_version') or 'Not available'}",
        f"- Git hash: {session_summary.get('git_hash') or 'Not available'}",
        "",
        "---",
        "",
    ]
    for item in turn_exports:
        lines.append(_markdown_turn(item, max_markdown_table_rows=max_markdown_table_rows))
        lines.extend(["", "---", ""])
    return "\n".join(lines).strip() + "\n"


def export_conversation_text(
    conversation_turns: Sequence[ConversationTurn],
    *,
    conversation_id: str | None,
    exported_at: str,
    session_summary: Mapping[str, Any],
    turn_exports: Sequence[Mapping[str, Any]] | None = None,
    max_markdown_table_rows: int = 30,
    max_text_chars: int = 8000,
) -> str:
    turn_exports = list(turn_exports or [])
    if not turn_exports:
        turn_exports = [
            _turn_export(
                index,
                turn,
                max_text_chars=max_text_chars,
                include_full_metadata=True,
                include_workspace=True,
                include_grounding=True,
                include_bindings=True,
                include_completion_validation=True,
                include_normalization=True,
                include_registered_tables=True,
                include_trace=True,
                include_errors=True,
                include_chart_summaries=True,
                include_plot_specs=True,
            )
            for index, turn in enumerate(conversation_turns, start=1)
        ]

    lines = [
        "Conversation Export Report",
        f"Exported at: {exported_at}",
        f"Conversation ID: {conversation_id or session_summary.get('conversation_id') or 'Not available'}",
        f"Turn count: {len(turn_exports)}",
        f"Dataset(s): {', '.join(session_summary.get('uploaded_files') or []) or 'Not available'}",
        "",
    ]
    for item in turn_exports:
        lines.append(_plain_turn(item))
    return "\n".join(lines).strip() + "\n"


def build_export_zip(files: Mapping[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, payload in files.items():
            data = payload.encode("utf-8") if isinstance(payload, str) else payload
            archive.writestr(str(filename), data)
    return buffer.getvalue()


def build_conversation_export_bundle(
    conversation_turns: ConversationState | Sequence[ConversationTurn],
    *,
    session_context: Mapping[str, Any] | None = None,
    include_full_metadata: bool = True,
    include_workspace: bool = True,
    include_grounding: bool = True,
    include_bindings: bool = True,
    include_completion_validation: bool = True,
    include_normalization: bool = True,
    include_registered_tables: bool = True,
    include_trace: bool = True,
    include_errors: bool = True,
    include_chart_summaries: bool = True,
    include_plot_specs: bool = True,
    max_markdown_table_rows: int = 30,
    max_text_chars: int = 8000,
) -> ConversationExportBundle:
    conversation_id, turns, debug_state = _normalize_turns(conversation_turns)
    exported_at = _now_iso()
    archive_name = f"conversation_bundle_{exported_at.replace(':', '-').replace('+', '_').replace('T', '_')}.zip"
    state = conversation_turns if isinstance(conversation_turns, ConversationState) else ConversationState(conversation_id=conversation_id or "", turns=list(turns), debug_state=debug_state)
    session_summary = _conversation_session_summary(state, session_context=session_context, exported_at=exported_at)

    turn_exports = [
        _turn_export(
            index,
            turn,
            max_text_chars=max_text_chars,
            include_full_metadata=include_full_metadata,
            include_workspace=include_workspace,
            include_grounding=include_grounding,
            include_bindings=include_bindings,
            include_completion_validation=include_completion_validation,
            include_normalization=include_normalization,
            include_registered_tables=include_registered_tables,
            include_trace=include_trace,
            include_errors=include_errors,
            include_chart_summaries=include_chart_summaries,
            include_plot_specs=include_plot_specs,
        )
        for index, turn in enumerate(turns, start=1)
    ]

    workspace_index = [item.get("workspace") for item in turn_exports if item.get("workspace")]
    binding_index = [item.get("binding_trace") for item in turn_exports if item.get("binding_trace")]
    errors_lines: list[str] = []
    for item in turn_exports:
        turn_index = int(item["turn_index"])
        for line in item.get("errors") or []:
            if line:
                errors_lines.append(line)
        chart_summary = item.get("chart_summary")
        if isinstance(chart_summary, Mapping) and chart_summary.get("render_failed"):
            errors_lines.append(f"Turn {turn_index} [assistant] chart render failed")

    full_json = {
        "exported_at": exported_at,
        "conversation_id": conversation_id or session_summary.get("conversation_id"),
        "summary": session_summary,
        "turn_count": len(turns),
        "turns": turn_exports,
        "workspace_index": workspace_index,
        "binding_index": binding_index,
        "errors_and_warnings": errors_lines,
    }

    markdown_report = export_conversation_markdown(
        turns,
        conversation_id=conversation_id or session_summary.get("conversation_id"),
        exported_at=exported_at,
        session_summary=session_summary,
        turn_exports=turn_exports,
        max_markdown_table_rows=max_markdown_table_rows,
        max_text_chars=max_text_chars,
    )
    plain_text_report = export_conversation_text(
        turns,
        conversation_id=conversation_id or session_summary.get("conversation_id"),
        exported_at=exported_at,
        session_summary=session_summary,
        turn_exports=turn_exports,
        max_markdown_table_rows=max_markdown_table_rows,
        max_text_chars=max_text_chars,
    )
    errors_and_warnings = "\n".join(errors_lines) if errors_lines else "No errors or warnings found."
    per_turn_json = {f"turn_{index:03d}.json": item for index, item in enumerate(turn_exports, start=1)}

    files: dict[str, bytes] = {
        "conversation_report.md": markdown_report.encode("utf-8"),
        "conversation_report.txt": plain_text_report.encode("utf-8"),
        "conversation_full.json": json.dumps(full_json, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
        "errors_and_warnings.txt": errors_and_warnings.encode("utf-8"),
        "workspace_index.json": json.dumps(make_json_safe(workspace_index), ensure_ascii=False, indent=2).encode("utf-8"),
        "binding_index.json": json.dumps(make_json_safe(binding_index), ensure_ascii=False, indent=2).encode("utf-8"),
    }
    for filename, payload in per_turn_json.items():
        files[f"turns/{filename}"] = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")

    zip_bytes = build_export_zip(files)
    return ConversationExportBundle(
        archive_name=archive_name,
        exported_at=exported_at,
        markdown_report=markdown_report,
        plain_text_report=plain_text_report,
        full_json=full_json,
        turn_json_map=per_turn_json,
        errors_and_warnings=errors_and_warnings,
        files=files,
        zip_bytes=zip_bytes,
    )
