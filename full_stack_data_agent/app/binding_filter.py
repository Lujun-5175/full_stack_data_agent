from __future__ import annotations

from typing import Any


def _action_aliases(action: str) -> set[str]:
    mapping = {
        "answer_text": {"summarize"},
        "show_table": {"inspect"},
        "show_chart": {"chart"},
        "explain": {"explain"},
        "summarize": {"summarize"},
        "export": {"export"},
        "followup": {"explain", "inspect", "summarize"},
    }
    return mapping.get(action, set())


def supports_action(payload: dict[str, Any], action: str) -> bool:
    actions = {str(item) for item in (payload.get("available_actions") or [])}
    artifact_type = str(payload.get("artifact_type") or "")
    if action == "show_chart" and artifact_type == "chart":
        return True
    return action in actions or bool(actions & _action_aliases(action))


def modality_from_payload(payload: dict[str, Any]) -> str:
    artifact_type = str(payload.get("artifact_type") or "")
    if artifact_type in {"raw_df", "filtered_df", "grouped_df"}:
        return "dataframe"
    if artifact_type == "chart":
        return "chart"
    if artifact_type == "scalar":
        return "scalar"
    if artifact_type == "text_answer":
        return "text"
    if artifact_type == "stats_result":
        return "stats"
    if artifact_type == "model_result":
        return "model"
    return "unknown"


def is_legal_candidate(action: str, payload: dict[str, Any]) -> tuple[bool, list[str]]:
    notes: list[str] = []
    modality = modality_from_payload(payload)
    if not supports_action(payload, action):
        return False, ["action unsupported"]
    purpose = str(payload.get("artifact_purpose") or "business_result")
    if purpose == "diagnostic":
        if action in {"show_chart", "export"}:
            return False, ["diagnostic artifact disallowed for action"]
        if action in {"explain", "followup"} and modality in {"dataframe", "chart", "scalar"}:
            return False, ["diagnostic artifact not explainable as business result"]
    if action in {"show_chart", "export"}:
        renderable = bool(
            (payload.get("render_payload") or {}).get("render_kind")
            or payload.get("chart_plan")
            or payload.get("chart_spec")
            or payload.get("chart_data")
        )
        if modality == "chart" and action == "show_chart" and not renderable:
            return False, ["chart artifact missing render payload"]
    if action in {"explain", "followup"} and modality == "chart":
        if not (payload.get("source_artifact_id") or payload.get("parent_artifact_id")):
            return False, ["chart follow-up lacks source lineage"]
        notes.append("chart candidate has source lineage")
    return True, notes
