from __future__ import annotations

from typing import Any

from full_stack_data_agent.app.binding_policies import BindingPolicy
from full_stack_data_agent.app.result_workspace import ResultWorkspace


def normalize_candidate_payload(
    workspace: ResultWorkspace,
    payload: dict[str, Any],
    action: str,
    policy: BindingPolicy,
) -> tuple[dict[str, Any], list[str]]:
    normalized = dict(payload)
    notes: list[str] = []
    artifact_type = str(payload.get("artifact_type") or "")
    if artifact_type != "chart":
        return normalized, notes
    if policy.preserve_chart_object:
        notes.append("kept chart object for object-centric action")
        return normalized, notes
    if policy.source_first:
        source = workspace.source_payload_for(str(payload.get("artifact_id") or ""))
        if source is not None:
            normalized = dict(source)
            normalized["is_current_turn"] = payload.get("is_current_turn", False)
            normalized["is_recent_turn"] = payload.get("is_recent_turn", False)
            normalized["normalized_from_artifact_id"] = payload.get("artifact_id")
            normalized["normalized_from_artifact_type"] = payload.get("artifact_type")
            normalized["normalization_reason"] = "source_first_action"
            notes.append("normalized chart reference to source artifact")
    return normalized, notes
