from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChartRequest(BaseModel):
    kind: Literal["barplot", "lineplot", "scatterplot", "boxplot", "histplot", "countplot"]
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


# Backward-compatible alias.
ChartPlan = ChartRequest

