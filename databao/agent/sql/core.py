from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from databao.agent.sql.ast_utils import ParsedSQLCandidate, parse_sql_candidate
from databao.agent.sql.execution_validation import ExecutionValidationReport
from databao.agent.sql.obligations import QueryObligations


@dataclass(frozen=True)
class SQLGuardrailReport:
    status: str
    attempt: int
    parsed: bool
    parse_error: str | None
    safety_checks: dict[str, Any] = field(default_factory=dict)
    schema_checks: dict[str, Any] = field(default_factory=dict)
    obligation_checks: dict[str, Any] = field(default_factory=dict)
    execution_checks: dict[str, Any] | None = None
    missing_obligations: list[str] = field(default_factory=list)
    wrong_refs: list[str] = field(default_factory=list)
    shape_mismatches: list[str] = field(default_factory=list)
    repair_hints: list[str] = field(default_factory=list)
    final_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _matches_required_expression(required: str, parsed_sql: ParsedSQLCandidate) -> bool:
    lowered_required = str(required or "").lower()
    haystacks = [
        parsed_sql.columns,
        parsed_sql.selected_aliases,
        parsed_sql.selected_expressions,
        parsed_sql.group_by_columns,
        parsed_sql.order_by_columns,
        parsed_sql.where_predicates,
        parsed_sql.having_predicates,
    ]
    return any(lowered_required in str(item).lower() for haystack in haystacks for item in haystack)


def _check_obligations(
    obligations: QueryObligations,
    parsed_sql: ParsedSQLCandidate,
    *,
    metadata_query: bool = False,
) -> tuple[dict[str, Any], list[str], list[str]]:
    if metadata_query:
        return (
            {
                "group_by_ok": True,
                "filters_ok": True,
                "metrics_ok": True,
                "derived_metrics_ok": True,
                "post_filters_ok": True,
                "sort_ok": True,
                "limit_ok": True,
                "required_output_ok": True,
            },
            [],
            [],
        )

    missing: list[str] = []
    hints: list[str] = []
    warnings: list[str] = []

    def _record(obligation_key: str, hint: str) -> None:
        tier = obligations.tier_for(obligation_key)
        if tier == "optional":
            warnings.append(obligation_key)
            return
        missing.append(obligation_key)
        if hint:
            hints.append(hint)
    for group in obligations.group_by:
        if not any(group in str(item).lower() for item in parsed_sql.group_by_columns):
            _record(f"group_by:{group}", f"Add GROUP BY {group}.")
    present_tables = {str(table).lower() for table in parsed_sql.tables}
    for table in obligations.base_tables or []:
        if table.lower() not in present_tables:
            _record(f"base_table:{table}", f"Use the required table '{table}'.")
    parsed_text = " ".join(
        [
            *parsed_sql.selected_expressions,
            *parsed_sql.where_predicates,
            *parsed_sql.having_predicates,
            *parsed_sql.order_by_columns,
            *parsed_sql.group_by_columns,
        ]
    ).lower()
    for literal in obligations.required_literals:
        if literal.lower() not in parsed_text and literal.lower() not in parsed_sql.sql_text.lower():
            _record(f"literal:{literal}", f"Preserve the required literal '{literal}'.")
    for filter_obligation in obligations.filters:
        where_text = " ".join(parsed_sql.where_predicates).lower()
        if filter_obligation.column.lower() not in where_text and str(filter_obligation.value).lower() not in where_text:
            _record(f"filter:{filter_obligation.column}", f"Add WHERE condition on {filter_obligation.column} {filter_obligation.op} {filter_obligation.value}.")
    for filter_obligation in obligations.post_filters:
        having_text = " ".join(parsed_sql.having_predicates).lower()
        if filter_obligation.column.lower() not in having_text and str(filter_obligation.value).lower() not in having_text:
            _record(f"post_filter:{filter_obligation.column}", f"Add HAVING condition on {filter_obligation.column} {filter_obligation.op} {filter_obligation.value}.")

    selected_text = " ".join(parsed_sql.selected_expressions).lower()
    for metric in obligations.metrics:
        if metric.kind == "conditional_count" and metric.condition and metric.condition.lower() in selected_text:
            continue
        if metric.name and not _matches_required_expression(metric.name.lower(), parsed_sql):
            _record(f"metric:{metric.name}", f"Include metric {metric.name} ({metric.kind}).")
    for derived in obligations.derived_metrics:
        if not _matches_required_expression(derived.name.lower(), parsed_sql):
            numerator_present = _matches_required_expression(derived.numerator, parsed_sql)
            denominator_present = _matches_required_expression(derived.denominator, parsed_sql)
            if not (numerator_present and denominator_present):
                _record(f"derived_metric:{derived.name}", f"Include derived metric {derived.name} as {derived.numerator}/{derived.denominator}.")

    if obligations.sort and not parsed_sql.order_by_columns:
        _record("sort", "Add ORDER BY " + ", ".join(f"{item.column} {item.direction}" for item in obligations.sort) + ".")
    if obligations.limit is not None and (parsed_sql.limit_value is None or int(parsed_sql.limit_value) != int(obligations.limit)):
        _record("limit", f"Add LIMIT {obligations.limit}.")
    for column in obligations.required_output_columns:
        if not _matches_required_expression(column.lower(), parsed_sql):
            _record(f"output_column:{column}", f"Project required output column '{column}'.")

    obligation_checks = {
        "group_by_ok": not any(item.startswith("group_by:") for item in missing),
        "base_tables_ok": not any(item.startswith("base_table:") for item in missing),
        "required_literals_ok": not any(item.startswith("literal:") for item in missing),
        "filters_ok": not any(item.startswith("filter:") for item in missing),
        "metrics_ok": not any(item.startswith("metric:") for item in missing),
        "derived_metrics_ok": not any(item.startswith("derived_metric:") for item in missing),
        "post_filters_ok": not any(item.startswith("post_filter:") for item in missing),
        "sort_ok": "sort" not in missing,
        "limit_ok": "limit" not in missing,
        "required_output_ok": not any(item.startswith("output_column:") for item in missing),
        "optional_warnings": warnings,
    }
    return obligation_checks, missing, hints


def verify_sql_candidate(
    sql_text: str,
    query_obligations: QueryObligations,
    schema_snapshot: dict[str, Any],
    dialect: str = "duckdb",
    attempt: int = 1,
) -> tuple[SQLGuardrailReport, ParsedSQLCandidate | None]:
    parsed = parse_sql_candidate(sql_text, dialect=dialect)
    lowered_sql = str(sql_text or "").lower()
    metadata_query = bool(parsed.metadata_query)
    parse_error = "; ".join(parsed.parse_errors) if parsed.parse_errors else None
    tables = {str(table).lower() for table in schema_snapshot.get("tables", [])}
    columns_by_table = {
        str(table).lower(): {str(column).lower() for column in columns}
        for table, columns in (schema_snapshot.get("columns_by_table") or {}).items()
    }
    wrong_refs: list[str] = []
    cte_names = {str(name).lower() for name in parsed.cte_names}
    for table in parsed.tables:
        if not metadata_query and table and table not in tables and table not in cte_names and table not in {"information_schema", "information_schema.columns", "pragma_table_info"}:
            wrong_refs.append(f"unknown_table:{table}")
    schema_columns = {column for columns in columns_by_table.values() for column in columns}
    should_validate_column_refs = bool(not metadata_query and (parsed.join_refs or parsed.where_predicates or parsed.group_by_columns or parsed.order_by_columns or parsed.having_predicates))
    if should_validate_column_refs:
        for column in parsed.columns:
            if column not in schema_columns and column not in parsed.selected_aliases:
                wrong_refs.append(f"unknown_column:{column}")
    safety_checks = {
        "single_statement": not any("Multiple SQL statements" in error for error in parsed.parse_errors),
        "select_like_only": parsed.statement_kind in {"select", "metadata_read"},
        "safe_query": parsed.is_safe_query,
        "forbidden_operations": not bool(parsed.forbidden_operations),
        "statement_kind": parsed.statement_kind,
        "read_only": parsed.read_only,
        "metadata_query": parsed.metadata_query,
        "supported_for_inspection": parsed.supported_for_inspection,
    }
    obligation_checks, missing_obligations, repair_hints = _check_obligations(query_obligations, parsed, metadata_query=metadata_query)
    if parse_error or not safety_checks["single_statement"] or not parsed.read_only:
        status = "hard_failed"
        final_reason = parse_error or f"Unsupported SQL statement kind: {parsed.statement_kind}."
    elif wrong_refs:
        status = "repairable"
        final_reason = "SQL references unknown tables or columns."
    elif missing_obligations:
        status = "repairable"
        final_reason = "SQL does not satisfy required obligations."
    else:
        status = "passed"
        final_reason = None
    report = SQLGuardrailReport(
        status=status,
        attempt=attempt,
        parsed=not bool(parsed.parse_errors),
        parse_error=parse_error,
        safety_checks=safety_checks,
        schema_checks={
            "known_tables": not any(item.startswith("unknown_table:") for item in wrong_refs),
            "known_columns": not any(item.startswith("unknown_column:") for item in wrong_refs),
        },
        obligation_checks=obligation_checks,
        execution_checks=None,
        missing_obligations=missing_obligations,
        wrong_refs=wrong_refs,
        shape_mismatches=[],
        repair_hints=repair_hints,
        final_reason=final_reason,
    )
    return report, (parsed if parsed.supported_for_inspection else None)


def merge_guardrail_and_execution_reports(
    guardrail_report: SQLGuardrailReport,
    execution_report: ExecutionValidationReport | None,
) -> SQLGuardrailReport:
    if execution_report is None:
        return guardrail_report
    shape_mismatches = list(guardrail_report.shape_mismatches)
    if execution_report.unexpected_shape:
        shape_mismatches.append("unexpected_shape")
    if execution_report.empty_result:
        shape_mismatches.append("empty_result")
    if execution_report.suspicious_projection:
        shape_mismatches.append("suspicious_projection")
    shape_mismatches.extend([f"missing_column:{column}" for column in execution_report.missing_columns])
    shape_mismatches.extend([f"missing_metric:{metric}" for metric in execution_report.missing_metrics])
    shape_mismatches.extend([f"missing_group:{dimension}" for dimension in execution_report.missing_grouping_dims])
    if guardrail_report.status == "passed":
        status = execution_report.status
    elif guardrail_report.status == "repairable" and execution_report.status == "hard_failed":
        status = "hard_failed"
    else:
        status = guardrail_report.status
    final_reason = guardrail_report.final_reason if execution_report.status == "passed" else (guardrail_report.final_reason or "Execution result does not satisfy obligations.")
    return SQLGuardrailReport(
        status=status,
        attempt=guardrail_report.attempt,
        parsed=guardrail_report.parsed,
        parse_error=guardrail_report.parse_error,
        safety_checks=guardrail_report.safety_checks,
        schema_checks=guardrail_report.schema_checks,
        obligation_checks=guardrail_report.obligation_checks,
        execution_checks=execution_report.to_dict(),
        missing_obligations=guardrail_report.missing_obligations,
        wrong_refs=guardrail_report.wrong_refs,
        shape_mismatches=shape_mismatches,
        repair_hints=list(dict.fromkeys(guardrail_report.repair_hints + execution_report.notes)),
        final_reason=final_reason,
    )
