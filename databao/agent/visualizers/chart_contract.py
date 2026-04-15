from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from databao.agent.visualizers.chart_registry import canonicalize_chart_kind, supported_chart_kinds


class ChartRequest(BaseModel):
    kind: str
    x: str | None = None
    y: str | None = None
    hue: str | None = None
    value: str | None = None
    orientation: Literal["vertical", "horizontal"] | None = None
    stack_mode: Literal["none", "stacked", "percent_stacked"] | None = None
    normalize_mode: Literal["none", "percent_of_group", "percent_of_total"] | None = None
    category_order: list[str] = Field(default_factory=list)
    show_value_labels: bool | None = None
    explicit_fields: dict[str, Any] = Field(default_factory=dict)
    planner_source: Literal["llm_json", "fallback"] | str | None = None
    confidence: Literal["high", "medium", "low"] | None = None
    source_result_id: str | None = None
    source_df_role: Literal["raw", "filtered", "grouped", "aggregated", "plot_ready"] | None = None
    # Backward-compatible fields in current call chain.
    title: str | None = None
    variables: list[str] = Field(default_factory=list)
    mode: str | None = None
    reason: str | None = None

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        canonical = canonicalize_chart_kind(value)
        if canonical not in supported_chart_kinds():
            raise ValueError(f"Unsupported chart kind: {value}")
        return str(canonical)


# Backward-compatible alias.
ChartPlan = ChartRequest
