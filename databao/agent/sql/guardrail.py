from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd
from duckdb import DuckDBPyConnection
from langchain_core.messages import BaseMessage, HumanMessage

from databao.agent.sql.core import merge_guardrail_and_execution_reports, verify_sql_candidate
from databao.agent.sql.execution_validation import validate_sql_result
from databao.agent.sql.fabrication import detect_sql_fabrication
from databao.agent.sql.obligations import QueryObligations, build_query_obligations
from databao.agent.sql.repair import build_sql_repair_prompt


def _strip_identifier(identifier: str) -> str:
    cleaned = identifier.strip().strip(";").strip()
    return cleaned.replace("`", "").replace('"', "").replace("[", "").replace("]", "")


def _normalize_table_name(name: str) -> str:
    parts = [part for part in _strip_identifier(name).split(".") if part]
    return parts[-1].lower() if parts else ""


def _schema_snapshot(schema: "SqlSchemaInfo") -> dict[str, Any]:
    return {
        "tables": sorted(schema.tables),
        "columns_by_table": {table: sorted(columns) for table, columns in schema.columns_by_table.items()},
        "types_by_table_column": {f"{table}.{column}": value for (table, column), value in schema.types_by_table_column.items()},
    }


@dataclass(frozen=True)
class SqlGuardIssue:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SqlGuardReport:
    blocked: bool
    issues: list[SqlGuardIssue]
    obligations: dict[str, Any]
    query: str
    sql: str
    available_tables: list[str]
    parsed_sql_summary: dict[str, Any] = field(default_factory=dict)
    guardrail_report: dict[str, Any] = field(default_factory=dict)
    execution_validation_report: dict[str, Any] | None = None
    repair_hints: list[str] = field(default_factory=list)
    repair_prompt: str | None = None
    final_guardrail_status: str | None = None
    repair_attempts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocked": self.blocked,
            "issues": [{"code": issue.code, "message": issue.message, "details": issue.details} for issue in self.issues],
            "obligations": self.obligations,
            "query": self.query,
            "sql": self.sql,
            "available_tables": self.available_tables,
            "parsed_sql_summary": self.parsed_sql_summary,
            "guardrail_report": self.guardrail_report,
            "execution_validation_report": self.execution_validation_report,
            "repair_hints": self.repair_hints,
            "repair_prompt": self.repair_prompt,
            "final_guardrail_status": self.final_guardrail_status,
            "repair_attempts": self.repair_attempts,
        }


@dataclass(frozen=True)
class SqlSchemaInfo:
    tables: set[str]
    columns_by_table: dict[str, set[str]]
    types_by_table_column: dict[tuple[str, str], str]


class SqlGuardrail:
    def __init__(self, *, max_retries: int = 3, llm_call: Callable[[str], str] | None = None) -> None:
        self.max_retries = max_retries
        self._llm_call = llm_call

    @staticmethod
    def latest_user_query(messages: list[BaseMessage]) -> str:
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                return str(message.content or "")
        return ""

    @staticmethod
    def schema_from_connection(connection: DuckDBPyConnection) -> SqlSchemaInfo:
        rows = connection.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
            """
        ).fetchall()
        tables: set[str] = set()
        columns_by_table: dict[str, set[str]] = {}
        types_by_table_column: dict[tuple[str, str], str] = {}
        for table_name, column_name, data_type in rows:
            table_norm = _normalize_table_name(str(table_name))
            column_norm = _strip_identifier(str(column_name)).lower()
            if table_norm and column_norm:
                tables.add(table_norm)
                columns_by_table.setdefault(table_norm, set()).add(column_norm)
                types_by_table_column[(table_norm, column_norm)] = str(data_type)
        return SqlSchemaInfo(tables=tables, columns_by_table=columns_by_table, types_by_table_column=types_by_table_column)

    def build_obligations(self, query: str, schema: SqlSchemaInfo) -> QueryObligations:
        return build_query_obligations(query, _schema_snapshot(schema), llm_call=self._llm_call)

    @staticmethod
    def _issue(code: str, message: str, *, details: dict[str, Any] | None = None) -> SqlGuardIssue:
        return SqlGuardIssue(code=code, message=message, details=dict(details or {}))

    def _collect_issues(self, sql: str, report: dict[str, Any], execution_report: dict[str, Any] | None = None) -> list[SqlGuardIssue]:
        issues: list[SqlGuardIssue] = []
        for fabrication_issue in detect_sql_fabrication(sql).issues:
            issues.append(self._issue(fabrication_issue["code"], fabrication_issue["message"]))
        for wrong in report.get("wrong_refs") or []:
            if str(wrong).startswith("unknown_table:"):
                issues.append(self._issue("unknown_table_reference", "SQL references tables that are not present in the current schema.", details={"unknown_tables": [str(wrong).split(":", 1)[1]]}))
        for missing in report.get("missing_obligations") or []:
            item = str(missing)
            if item.startswith("group_by:"):
                issues.append(self._issue("post_groupby_missing", "GROUP BY obligation is missing."))
            elif item.startswith("base_table:"):
                table = item.split(":", 1)[1]
                issues.append(self._issue("required_table_missing", f"Required table '{table}' is missing from SQL.", details={"required_table": table}))
            elif item.startswith("literal:"):
                literal = item.split(":", 1)[1]
                issues.append(self._issue("required_year_missing", f"Required year filter '{literal}' is missing from SQL.", details={"year": literal}))
            elif item.startswith("filter:"):
                issues.append(self._issue("required_filter_missing", "Required filter obligation is missing."))
            elif item.startswith("post_filter:"):
                issues.append(self._issue("post_aggregation_filter_missing", "Post-aggregation filter is missing."))
            elif item.startswith("metric:") or item.startswith("derived_metric:"):
                issues.append(self._issue("post_required_metric_missing", "Required metric obligation is missing."))
            elif item == "sort":
                issues.append(self._issue("post_topn_order_missing" if execution_report else "topn_order_missing", "Top-N intent detected but SQL is missing ORDER BY."))
            elif item == "limit":
                issues.append(self._issue("post_topn_limit_missing" if execution_report else "topn_limit_missing", "Top-N intent detected but SQL is missing LIMIT."))
            elif item.startswith("output_column:"):
                column = item.split(":", 1)[1]
                issues.append(self._issue("post_projection_missing", f"Required output column '{column}' is missing.", details={"column": column}))
        if re.search(r"\b(?:left|right|inner|full|outer)?\s*join\s+[a-zA-Z_][\w\.\"]*(?!\s*(?:on|using))", sql.lower()):
            issues.append(self._issue("join_on_missing", "Detected JOIN without ON/USING clause."))
        if re.search(r"\bjoin\b", sql.lower()) and any("unknown_column:" in ref for ref in (report.get("wrong_refs") or [])):
            issues.append(self._issue("join_on_unknown_column", "JOIN ON clause references unknown columns."))
        if execution_report:
            if execution_report.get("empty_result"):
                issues.append(self._issue("post_empty_result", "Query returned an empty result set."))
            if execution_report.get("missing_metrics"):
                issues.append(self._issue("post_required_metric_missing", "Required metric is missing in output."))
            if execution_report.get("missing_grouping_dims"):
                issues.append(self._issue("post_groupby_missing", "Grouped result is missing required dimensions."))
            for column in execution_report.get("missing_columns") or []:
                issues.append(self._issue("post_projection_missing", f"Required output column '{column}' is missing.", details={"column": column}))
        return issues

    def validate_before_execution(self, *, query: str, sql: str, schema: SqlSchemaInfo, attempt: int = 1) -> SqlGuardReport:
        obligations = self.build_obligations(query, schema)
        guardrail_report, parsed_sql = verify_sql_candidate(sql_text=sql, query_obligations=obligations, schema_snapshot=_schema_snapshot(schema), dialect="duckdb", attempt=attempt)
        parsed_sql_summary = parsed_sql.to_summary_dict() if parsed_sql is not None else {}
        issues = self._collect_issues(sql, guardrail_report.to_dict())
        return SqlGuardReport(
            blocked=bool(issues or guardrail_report.status in {"repairable", "hard_failed"}),
            issues=issues,
            obligations=obligations.to_dict(),
            query=query,
            sql=sql,
            available_tables=sorted(schema.tables),
            parsed_sql_summary=parsed_sql_summary,
            guardrail_report=guardrail_report.to_dict(),
            execution_validation_report=None,
            repair_hints=list(guardrail_report.repair_hints),
            repair_prompt=build_sql_repair_prompt(original_query=query, obligations=obligations, previous_sql=sql, guardrail_report=guardrail_report, execution_validation_report=None, schema_summary=_schema_snapshot(schema)),
            final_guardrail_status=guardrail_report.status,
            repair_attempts=[{"attempt": attempt, "status": guardrail_report.status}],
        )

    def validate_after_execution(self, *, query: str, sql: str, dataframe: pd.DataFrame | None, schema: SqlSchemaInfo, attempt: int = 1) -> SqlGuardReport:
        obligations = self.build_obligations(query, schema)
        guardrail_report, parsed_sql = verify_sql_candidate(sql_text=sql, query_obligations=obligations, schema_snapshot=_schema_snapshot(schema), dialect="duckdb", attempt=attempt)
        execution_report = validate_sql_result(dataframe, obligations, parsed_sql)
        merged = merge_guardrail_and_execution_reports(guardrail_report, execution_report)
        parsed_sql_summary = parsed_sql.to_summary_dict() if parsed_sql is not None else {}
        issues = self._collect_issues(sql, merged.to_dict(), execution_report.to_dict())
        return SqlGuardReport(
            blocked=bool(issues or merged.status in {"repairable", "hard_failed"}),
            issues=issues,
            obligations=obligations.to_dict(),
            query=query,
            sql=sql,
            available_tables=sorted(schema.tables),
            parsed_sql_summary=parsed_sql_summary,
            guardrail_report=merged.to_dict(),
            execution_validation_report=execution_report.to_dict(),
            repair_hints=list(merged.repair_hints),
            repair_prompt=build_sql_repair_prompt(original_query=query, obligations=obligations, previous_sql=sql, guardrail_report=merged, execution_validation_report=execution_report, schema_summary=_schema_snapshot(schema)),
            final_guardrail_status=merged.status,
            repair_attempts=[{"attempt": attempt, "status": merged.status}],
        )

    @staticmethod
    def to_retry_error_payload(report: SqlGuardReport, *, attempt: int, max_attempts: int) -> dict[str, Any]:
        return {
            "error_type": "sql_guardrail",
            "attempt": attempt,
            "max_attempts": max_attempts,
            "report": report.to_dict() | {"repair_attempts": [{"attempt": attempt, "max_attempts": max_attempts, "status": report.final_guardrail_status}]},
        }

    @staticmethod
    def to_retry_error_text(payload: dict[str, Any]) -> str:
        return "SQL_GUARDRAIL_ERROR " + json.dumps(payload, ensure_ascii=False, default=str)
