from __future__ import annotations

from typing import Any

from databao.agent.sql.core import SQLGuardrailReport
from databao.agent.sql.execution_validation import ExecutionValidationReport
from databao.agent.sql.obligations import QueryObligations


def _format_list(items: list[str], *, default: str = "None") -> str:
    values = [str(item) for item in items if str(item).strip()]
    if not values:
        return default
    return "\n".join(f"- {value}" for value in values)


def build_sql_repair_prompt(
    *,
    original_query: str,
    obligations: QueryObligations,
    previous_sql: str,
    guardrail_report: SQLGuardrailReport,
    execution_validation_report: ExecutionValidationReport | None,
    schema_summary: str | dict[str, Any],
) -> str:
    must_include: list[str] = []
    if obligations.group_by:
        must_include.append(f"GROUP BY fields: {', '.join(obligations.group_by)}")
    if obligations.filters:
        must_include.extend([f"Filter: {item.column} {item.op} {item.value} (stage={item.stage})" for item in obligations.filters])
    if obligations.post_filters:
        must_include.extend([f"Post-aggregation filter: {item.column} {item.op} {item.value}" for item in obligations.post_filters])
    if obligations.metrics:
        must_include.extend([f"Metric: {item.name} ({item.kind})" for item in obligations.metrics])
    if obligations.derived_metrics:
        must_include.extend([f"Derived metric: {item.name} = {item.numerator}/{item.denominator}" for item in obligations.derived_metrics])
    if obligations.sort:
        must_include.append(f"Sorting: {', '.join(f'{item.column} {item.direction}' for item in obligations.sort)}")
    if obligations.limit is not None:
        must_include.append(f"LIMIT {obligations.limit}")

    must_not = [
        "Do not use non-SELECT statements.",
        "Do not fabricate business rows via VALUES/UNION literal records.",
        "Do not reference unknown tables or columns.",
        "Do not remove required grouping, metrics, or sorting obligations.",
    ]
    failure_reasons = []
    if guardrail_report.final_reason:
        failure_reasons.append(guardrail_report.final_reason)
    failure_reasons.extend(guardrail_report.missing_obligations)
    failure_reasons.extend(guardrail_report.wrong_refs)
    failure_reasons.extend(guardrail_report.shape_mismatches)
    if execution_validation_report is not None:
        failure_reasons.extend(execution_validation_report.missing_columns)
        failure_reasons.extend(execution_validation_report.missing_metrics)
        failure_reasons.extend(execution_validation_report.missing_grouping_dims)
    repair_hints = list(guardrail_report.repair_hints)
    if execution_validation_report is not None:
        repair_hints.extend(execution_validation_report.notes)
    schema_text = schema_summary if isinstance(schema_summary, str) else str(schema_summary)
    return (
        "You are repairing an SQL query. Return only one SQL query.\n\n"
        f"Original user query:\n{original_query}\n\n"
        f"Current failed SQL:\n{previous_sql}\n\n"
        f"Schema summary:\n{schema_text}\n\n"
        "Must include:\n"
        f"{_format_list(must_include)}\n\n"
        "Must not do:\n"
        f"{_format_list(must_not)}\n\n"
        "Expected output columns:\n"
        f"{_format_list(obligations.required_output_columns)}\n\n"
        "Expected grouping:\n"
        f"{_format_list(obligations.group_by)}\n\n"
        "Expected filters:\n"
        f"{_format_list([f'{item.column} {item.op} {item.value}' for item in obligations.filters])}\n\n"
        "Current failure reasons:\n"
        f"{_format_list(failure_reasons)}\n\n"
        "Repair hints:\n"
        f"{_format_list(repair_hints)}\n"
    )
