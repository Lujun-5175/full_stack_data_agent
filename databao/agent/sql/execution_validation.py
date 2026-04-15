from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from databao.agent.sql.ast_utils import ParsedSQLCandidate
from databao.agent.sql.obligations import QueryObligations


@dataclass(frozen=True)
class ExecutionValidationReport:
    status: str
    row_count: int
    column_names: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    unexpected_shape: bool = False
    empty_result: bool = False
    suspicious_projection: bool = False
    missing_metrics: list[str] = field(default_factory=list)
    missing_grouping_dims: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_sql_result(
    result_df: pd.DataFrame | None,
    query_obligations: QueryObligations,
    parsed_sql_candidate: ParsedSQLCandidate | None = None,
) -> ExecutionValidationReport:
    if result_df is None:
        return ExecutionValidationReport(
            status="hard_failed",
            row_count=0,
            empty_result=True,
            notes=["SQL execution did not return a dataframe."],
        )

    columns = [str(column).lower() for column in result_df.columns]
    row_count = int(len(result_df))
    def _tiered_required_output_columns() -> list[str]:
        required: list[str] = []
        for column in query_obligations.required_output_columns:
            tier = query_obligations.tier_for(f"output_column:{column.lower()}", "important")
            if tier in {"critical", "important"}:
                required.append(column)
        return required

    missing_columns = [column.lower() for column in _tiered_required_output_columns() if column.lower() not in columns]
    missing_metrics = sorted(
        {
            metric.name.lower()
            for metric in [*query_obligations.metrics, *query_obligations.derived_metrics]
            if metric.name and metric.name.lower() not in columns and query_obligations.tier_for(f"{'derived_metric' if hasattr(metric, 'numerator') else 'metric'}:{metric.name.lower()}") in {"critical", "important"}
        }
    )
    missing_grouping_dims = [
        dimension.lower()
        for dimension in query_obligations.group_by
        if dimension.lower() not in columns and query_obligations.tier_for(f"group_by:{dimension.lower()}") in {"critical", "important"}
    ]
    empty_result = row_count == 0
    unexpected_shape = bool(query_obligations.group_by and row_count <= 1)
    suspicious_projection = bool(
        query_obligations.group_by
        and not missing_grouping_dims
        and row_count == 1
        and len(columns) <= max(1, len(query_obligations.group_by))
    )
    notes: list[str] = []
    if query_obligations.limit is not None and row_count > query_obligations.limit:
        notes.append("Result row count exceeds expected LIMIT.")
    if parsed_sql_candidate is not None and query_obligations.sort and not parsed_sql_candidate.order_by_columns:
        notes.append("Expected ORDER BY but parsed SQL does not contain it.")
    if suspicious_projection:
        notes.append("Grouped query collapsed to a single narrow row; projection looks suspicious.")

    if empty_result:
        status = "hard_failed"
    elif missing_columns or missing_grouping_dims or missing_metrics or unexpected_shape or suspicious_projection:
        status = "repairable"
    else:
        status = "passed"

    return ExecutionValidationReport(
        status=status,
        row_count=row_count,
        column_names=columns,
        missing_columns=missing_columns,
        unexpected_shape=unexpected_shape,
        empty_result=empty_result,
        suspicious_projection=suspicious_projection,
        missing_metrics=missing_metrics,
        missing_grouping_dims=missing_grouping_dims,
        notes=notes,
    )
