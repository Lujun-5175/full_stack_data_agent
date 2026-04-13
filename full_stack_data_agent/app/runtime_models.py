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
    used_databao: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
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
