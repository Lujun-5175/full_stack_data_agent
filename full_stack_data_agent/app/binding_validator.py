from __future__ import annotations

from typing import Any

from full_stack_data_agent.app.binding_filter import supports_action
from full_stack_data_agent.app.result_workspace import ResultWorkspace


def validate_candidate_selection(
    workspace: ResultWorkspace,
    candidate_map: dict[str, Any],
    selected_artifact_id: str | None,
    *,
    action: str,
) -> tuple[str | None, list[str], str | None]:
    notes: list[str] = []
    if not selected_artifact_id:
        return None, ["no artifact selected"], "no_selection"
    candidate = candidate_map.get(selected_artifact_id)
    if candidate is None:
        return None, ["selected artifact not in candidate set"], "candidate_set_violation"
    payload = candidate.features.get("payload") or {}
    if workspace.resolve_artifact(selected_artifact_id) is None and not payload.get("is_recent_turn"):
        return None, ["artifact does not exist in workspace"], "artifact_missing"
    if not supports_action(payload, action):
        return None, ["selected artifact does not support action"], "action_unsupported"
    if action in {"explain", "followup", "summarize"} and str(payload.get("artifact_type") or "") == "chart":
        if not (payload.get("source_artifact_id") or payload.get("parent_artifact_id")):
            return None, ["chart selection lacks source lineage"], "lineage_illegal"
    return selected_artifact_id, notes, None
