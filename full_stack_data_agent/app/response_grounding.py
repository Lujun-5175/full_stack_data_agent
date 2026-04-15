from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GroundedResponse:
    primary_text_artifact_id: str | None = None
    primary_table_artifact_id: str | None = None
    primary_chart_artifact_id: str | None = None
    primary_explain_artifact_id: str | None = None
    referenced_artifact_ids: list[str] = field(default_factory=list)
    followup_target_artifact_id: str | None = None
    available_actions_by_artifact: dict[str, list[str]] = field(default_factory=dict)
    render_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_text_artifact_id": self.primary_text_artifact_id,
            "primary_table_artifact_id": self.primary_table_artifact_id,
            "primary_chart_artifact_id": self.primary_chart_artifact_id,
            "primary_explain_artifact_id": self.primary_explain_artifact_id,
            "referenced_artifact_ids": list(self.referenced_artifact_ids),
            "followup_target_artifact_id": self.followup_target_artifact_id,
            "available_actions_by_artifact": dict(self.available_actions_by_artifact),
            "render_payload": dict(self.render_payload),
        }
