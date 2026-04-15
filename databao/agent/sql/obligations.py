from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from databao.agent.sql.obligation_binder import bind_query_frame_to_obligations
from databao.agent.sql.query_frame import QueryFrame, build_query_frame
from databao.agent.sql.semantic_schema import SemanticSchemaInventory, build_semantic_schema_inventory


@dataclass(frozen=True)
class FilterObligation:
    column: str
    op: str
    value: str | int | float | None
    stage: str = "where"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MetricObligation:
    name: str
    kind: str
    source_column: str | None = None
    condition: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DerivedMetricObligation:
    name: str
    numerator: str
    denominator: str
    expression_kind: str = "ratio"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SortObligation:
    column: str
    direction: str = "desc"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QueryObligations:
    base_tables: list[str] | None = None
    preferred_tables: list[str] = field(default_factory=list)
    candidate_tables: list[str] = field(default_factory=list)
    required_literals: list[str] = field(default_factory=list)
    filters: list[FilterObligation] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    metrics: list[MetricObligation] = field(default_factory=list)
    derived_metrics: list[DerivedMetricObligation] = field(default_factory=list)
    post_filters: list[FilterObligation] = field(default_factory=list)
    sort: list[SortObligation] = field(default_factory=list)
    limit: int | None = None
    required_output_columns: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    chart_requirements: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)
    semantic_frame: dict[str, Any] | None = None
    binding_evidence: list[dict[str, Any]] = field(default_factory=list)
    obligation_tiers: dict[str, str] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_tables": list(self.base_tables or []),
            "preferred_tables": list(self.preferred_tables),
            "candidate_tables": list(self.candidate_tables),
            "required_literals": list(self.required_literals),
            "filters": [item.to_dict() for item in self.filters],
            "group_by": list(self.group_by),
            "metrics": [item.to_dict() for item in self.metrics],
            "derived_metrics": [item.to_dict() for item in self.derived_metrics],
            "post_filters": [item.to_dict() for item in self.post_filters],
            "sort": [item.to_dict() for item in self.sort],
            "limit": self.limit,
            "required_output_columns": list(self.required_output_columns),
            "deliverables": list(self.deliverables),
            "chart_requirements": dict(self.chart_requirements or {}),
            "notes": list(self.notes),
            "semantic_frame": dict(self.semantic_frame or {}),
            "binding_evidence": [dict(item) for item in self.binding_evidence],
            "obligation_tiers": dict(self.obligation_tiers),
            "provenance": dict(self.provenance),
            "confidence": self.confidence,
        }

    def tier_for(self, obligation_key: str, default: str = "critical") -> str:
        return str(self.obligation_tiers.get(obligation_key, default))


def build_query_obligations(
    query: str,
    schema_snapshot: dict[str, Any] | None = None,
    *,
    llm_call: Callable[[str], str] | None = None,
) -> QueryObligations:
    inventory: SemanticSchemaInventory = build_semantic_schema_inventory(schema_snapshot, query=query)
    frame: QueryFrame = build_query_frame(query, inventory, llm_call=llm_call)
    obligations = bind_query_frame_to_obligations(
        frame,
        inventory,
        obligations_factory=QueryObligations,
        filter_factory=FilterObligation,
        metric_factory=MetricObligation,
        derived_metric_factory=DerivedMetricObligation,
        sort_factory=SortObligation,
    )
    return obligations
