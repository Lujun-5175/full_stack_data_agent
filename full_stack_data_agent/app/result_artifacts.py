from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

DATAFRAME_ARTIFACT_TYPES = {"raw_df", "filtered_df", "grouped_df"}
SUMMARY_ARTIFACT_TYPES = {"text_answer", "scalar"}
STAT_ARTIFACT_TYPES = {"stats_result"}
MODEL_ARTIFACT_TYPES = {"model_result"}


def _preview_from_dataframe(dataframe: pd.DataFrame | None, *, limit: int = 10) -> list[dict[str, Any]] | None:
    if dataframe is None:
        return None
    return dataframe.head(limit).to_dict(orient="records")


@dataclass
class ResultArtifact:
    artifact_id: str
    artifact_type: str
    name: str | None = None
    parent_artifact_id: str | None = None
    lineage: list[str] = field(default_factory=list)
    created_by_step: str | None = None
    available_actions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    artifact_purpose: str = "business_result"
    dataframe: pd.DataFrame | None = None
    dataframe_preview: list[dict[str, Any]] | None = None
    scalar_value: Any | None = None
    text_value: str | None = None
    chart_plan: dict[str, Any] | None = None
    chart_spec: dict[str, Any] | None = None
    chart_data: list[dict[str, Any]] | None = None
    chart_meta: dict[str, Any] | None = None
    stats_result: dict[str, Any] | None = None
    model_result: dict[str, Any] | None = None
    render_payload: dict[str, Any] = field(default_factory=dict)
    runtime_handles: dict[str, Any] = field(default_factory=dict)

    @property
    def has_heavy_runtime_object(self) -> bool:
        return bool(self.runtime_handles)

    def is_dataframe_like(self) -> bool:
        return self.artifact_type in DATAFRAME_ARTIFACT_TYPES

    def primary_modality(self) -> str:
        if self.is_dataframe_like():
            return "dataframe"
        if self.artifact_type == "chart":
            return "chart"
        if self.artifact_type == "scalar":
            return "scalar"
        if self.artifact_type == "text_answer":
            return "text"
        if self.artifact_type in STAT_ARTIFACT_TYPES:
            return "stats"
        if self.artifact_type in MODEL_ARTIFACT_TYPES:
            return "model"
        return "unknown"

    def is_chart_like(self) -> bool:
        return self.artifact_type == "chart"

    def is_summary_like(self) -> bool:
        return self.artifact_type in SUMMARY_ARTIFACT_TYPES

    def is_stat_like(self) -> bool:
        return self.artifact_type in STAT_ARTIFACT_TYPES

    def is_model_like(self) -> bool:
        return self.artifact_type in MODEL_ARTIFACT_TYPES

    def is_primary_dataframe_like(self) -> bool:
        return self.is_dataframe_like()

    def has_lineage_to(self, target_artifact_id: str) -> bool:
        return target_artifact_id in self.lineage

    def lineage_depth(self) -> int:
        return max(len(self.lineage) - 1, 0)

    def has_parent_dataframe(self, workspace: Any | None = None) -> bool:
        if workspace is None or not self.parent_artifact_id:
            return False
        parent = getattr(workspace, "resolve_artifact", lambda _artifact_id: None)(self.parent_artifact_id)
        return bool(parent is not None and getattr(parent, "is_dataframe_like", lambda: False)())

    def supports_action(self, action: str) -> bool:
        resolved_action = str(action or "").strip()
        if not resolved_action:
            return False
        if self.artifact_purpose == "diagnostic":
            if resolved_action in {"show_chart", "chart"}:
                return False
            if resolved_action in {"explain_business_result", "show_table", "export"}:
                return False
            if resolved_action in {"explain", "summarize"} and self.artifact_type in DATAFRAME_ARTIFACT_TYPES | {"chart", "scalar"}:
                return False
            if self.artifact_type == "chart":
                return False
        return resolved_action in (self.available_actions or self.default_available_actions())

    def ensure_preview(self) -> None:
        if self.dataframe_preview is None and self.dataframe is not None:
            self.dataframe_preview = _preview_from_dataframe(self.dataframe)

    def default_available_actions(self) -> list[str]:
        if self.artifact_purpose == "diagnostic":
            actions: set[str] = {"inspect", "summarize"}
            if self.artifact_type == "text_answer":
                actions.add("explain")
            return sorted(actions)
        actions: set[str] = {"inspect"}
        if self.is_dataframe_like():
            actions.update({"chart", "explain", "summarize", "export"})
        if self.artifact_type == "chart":
            actions.update({"explain", "export"})
        if self.artifact_type == "scalar":
            actions.update({"explain", "summarize"})
        if self.artifact_type == "text_answer":
            actions.update({"explain", "summarize"})
        if self.artifact_type in {"stats_result", "model_result"}:
            actions.update({"explain", "export"})
        return sorted(actions)

    def summary_dict(self) -> dict[str, Any]:
        self.ensure_preview()
        row_count = int(len(self.dataframe)) if self.dataframe is not None else self.metadata.get("row_count")
        columns = (
            [str(column) for column in self.dataframe.columns]
            if self.dataframe is not None
            else list(self.metadata.get("columns") or [])
        )
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "artifact_purpose": self.artifact_purpose,
            "name": self.name,
            "parent_artifact_id": self.parent_artifact_id,
            "lineage": list(self.lineage),
            "created_by_step": self.created_by_step,
            "available_actions": list(self.available_actions or self.default_available_actions()),
            "metadata": dict(self.metadata),
            "source_artifact_id": self.metadata.get("source_artifact_id") or self.parent_artifact_id,
            "dataframe_preview": self.dataframe_preview,
            "row_count": row_count,
            "columns": columns,
            "scalar_value": self.scalar_value,
            "text_value": self.text_value,
            "chart_plan": self.chart_plan,
            "chart_spec": self.chart_spec,
            "chart_data": self.chart_data,
            "chart_meta": self.chart_meta,
            "render_payload": dict(self.render_payload),
            "has_heavy_runtime_object": self.has_heavy_runtime_object,
            "stats_result": self.stats_result,
            "model_result": self.model_result,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.summary_dict()

    def runtime_handle(self, name: str) -> Any | None:
        return self.runtime_handles.get(name)
