from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class RegisteredTable:
    name: str
    source_file: str
    row_count: int
    columns: list[str]
    description: str
    normalization_report: dict[str, Any] = field(default_factory=dict)
    semantic_profile: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactSummary:
    artifact_id: str
    artifact_type: str
    artifact_purpose: str = "business_result"
    name: str | None = None
    parent_artifact_id: str | None = None
    lineage: list[str] = field(default_factory=list)
    created_by_step: str | None = None
    available_actions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    dataframe_preview: list[dict[str, Any]] | None = None
    row_count: int | None = None
    columns: list[str] = field(default_factory=list)
    scalar_value: Any | None = None
    text_value: str | None = None
    chart_plan: dict[str, Any] | None = None
    chart_spec: dict[str, Any] | None = None
    chart_data: list[dict[str, Any]] | None = None
    chart_meta: dict[str, Any] | None = None
    render_payload: dict[str, Any] = field(default_factory=dict)
    has_heavy_runtime_object: bool = False
    stats_result: dict[str, Any] | None = None
    model_result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResultWorkspaceSnapshot:
    conversation_id: str | None = None
    turn_id: str | None = None
    root_artifact_id: str | None = None
    latest_by_type: dict[str, str] = field(default_factory=dict)
    artifacts: list[ArtifactSummary] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "root_artifact_id": self.root_artifact_id,
            "latest_by_type": dict(self.latest_by_type),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }


@dataclass(frozen=True)
class GroundedResponseSnapshot:
    primary_text_artifact_id: str | None = None
    primary_table_artifact_id: str | None = None
    primary_chart_artifact_id: str | None = None
    primary_explain_artifact_id: str | None = None
    referenced_artifact_ids: list[str] = field(default_factory=list)
    followup_target_artifact_id: str | None = None
    available_actions_by_artifact: dict[str, list[str]] = field(default_factory=dict)
    render_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DatabaoTurnResult:
    text: str
    dataframe: pd.DataFrame | None = None
    dataframe_preview: list[dict[str, Any]] | None = None
    columns: list[str] | None = None
    row_count: int | None = None
    provider_used: str | None = None
    model_used: str | None = None
    fallback_triggered: bool = False
    fallback_reason: str | None = None
    plot_code: str | None = None
    plot_object: Any | None = None
    plot_plan: dict[str, Any] | None = None
    plot_spec: dict[str, Any] | None = None
    plot_data: list[dict[str, Any]] | None = None
    plot_meta: dict[str, Any] | None = None
    plot_backend: str | None = None
    plot_kind: str | None = None
    plot_image_base64: str | None = None
    plot_image_mime_type: str | None = None
    plot_error: str | None = None
    chart_debug: dict[str, Any] = field(default_factory=dict)
    completion_validation: dict[str, Any] | None = None
    thread_meta: dict[str, Any] = field(default_factory=dict)
    result_workspace: Any | None = None
    grounded_response: Any | None = None
    primary_artifact_id: str | None = None
    primary_table_artifact_id: str | None = None
    primary_chart_artifact_id: str | None = None
    followup_target_artifact_id: str | None = None
    binding_intent: dict[str, Any] | None = None
    binding_bundle: dict[str, Any] | None = None
    binding_decisions: dict[str, Any] | None = None
    decision_mode: str | None = None
    llm_used: bool = False
    turn_failure_state: str | None = None
    failure_reason: str | None = None
    business_result_present: bool = False
    used_databao: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        workspace = self.result_workspace
        grounded = self.grounded_response
        workspace_dict = workspace.to_dict() if hasattr(workspace, "to_dict") else workspace
        grounded_dict = grounded.to_dict() if hasattr(grounded, "to_dict") else grounded
        return {
            "text": self.text,
            "dataframe_preview": self.dataframe_preview,
            "columns": self.columns,
            "row_count": self.row_count,
            "provider_used": self.provider_used,
            "model_used": self.model_used,
            "fallback_triggered": self.fallback_triggered,
            "fallback_reason": self.fallback_reason,
            "plot_code": self.plot_code,
            "plot_plan": self.plot_plan,
            "plot_spec": self.plot_spec,
            "plot_data": self.plot_data,
            "plot_meta": self.plot_meta,
            "plot_backend": self.plot_backend,
            "plot_kind": self.plot_kind,
            "plot_image_base64": self.plot_image_base64,
            "plot_image_mime_type": self.plot_image_mime_type,
            "plot_error": self.plot_error,
            "chart_debug": self.chart_debug,
            "completion_validation": self.completion_validation,
            "thread_meta": self.thread_meta,
            "result_workspace": workspace_dict,
            "grounded_response": grounded_dict,
            "primary_artifact_id": self.primary_artifact_id,
            "primary_table_artifact_id": self.primary_table_artifact_id,
            "primary_chart_artifact_id": self.primary_chart_artifact_id,
            "followup_target_artifact_id": self.followup_target_artifact_id,
            "binding_intent": self.binding_intent,
            "binding_bundle": self.binding_bundle,
            "binding_decisions": self.binding_decisions,
            "decision_mode": self.decision_mode,
            "llm_used": self.llm_used,
            "turn_failure_state": self.turn_failure_state,
            "failure_reason": self.failure_reason,
            "business_result_present": self.business_result_present,
            "used_databao": self.used_databao,
            "error": self.error,
        }


@dataclass
class DatabaoSessionSnapshot:
    conversation_id: str
    llm_name: str
    executor_type: str
    registered_tables: list[RegisteredTable] = field(default_factory=list)
    normalization_reports: list[dict[str, Any]] = field(default_factory=list)
    context_build_error: str | None = None
    context_replayed: bool = False
    datasource_changed: bool = False
    thread_reset_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "llm_name": self.llm_name,
            "executor_type": self.executor_type,
            "registered_tables": [table.to_dict() for table in self.registered_tables],
            "normalization_reports": self.normalization_reports,
            "context_build_error": self.context_build_error,
            "context_replayed": self.context_replayed,
            "datasource_changed": self.datasource_changed,
            "thread_reset_reason": self.thread_reset_reason,
        }
