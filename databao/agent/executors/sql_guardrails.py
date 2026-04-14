from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from duckdb import DuckDBPyConnection
from langchain_core.messages import BaseMessage, HumanMessage


_AGGREGATE_FUNCTIONS = ("count(", "sum(", "avg(", "min(", "max(", "median(", "stddev(", "variance(")
_FABRICATED_TABLE_PREFIXES = ("simulated_", "fake_", "invented_", "synthetic_", "mock_")
_STATUS_KEYWORDS = ("delivered", "purchased", "cancelled", "completed", "pending")
_METRIC_KEYWORDS = ("payment", "revenue", "amount", "salary", "price", "count", "avg", "average")


def _strip_identifier(identifier: str) -> str:
    cleaned = identifier.strip().strip(";").strip()
    cleaned = cleaned.replace("`", "").replace('"', "").replace("[", "").replace("]", "")
    return cleaned


def _normalize_table_name(name: str) -> str:
    cleaned = _strip_identifier(name)
    parts = [part for part in cleaned.split(".") if part]
    if not parts:
        return ""
    return parts[-1].lower()


def _looks_like_literal_projection(select_sql: str) -> bool:
    compact = re.sub(r"\s+", " ", select_sql.strip().lower())
    if " from " in compact:
        return False
    return bool(re.search(r"\bselect\b\s+(?:'[^']*'|\"[^\"]*\"|\d+|\d+\.\d+)", compact))


def _type_group(sql_type: str) -> str:
    lowered = sql_type.lower()
    if any(token in lowered for token in ("int", "decimal", "double", "float", "numeric", "real")):
        return "numeric"
    if any(token in lowered for token in ("date", "time")):
        return "temporal"
    if any(token in lowered for token in ("char", "text", "string", "varchar")):
        return "text"
    if "bool" in lowered:
        return "boolean"
    return "other"


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocked": self.blocked,
            "issues": [
                {"code": issue.code, "message": issue.message, "details": issue.details}
                for issue in self.issues
            ],
            "obligations": self.obligations,
            "query": self.query,
            "sql": self.sql,
            "available_tables": self.available_tables,
        }


@dataclass(frozen=True)
class SqlSchemaInfo:
    tables: set[str]
    columns_by_table: dict[str, set[str]]
    types_by_table_column: dict[tuple[str, str], str]


class SqlGuardrail:
    def __init__(self, *, max_retries: int = 3) -> None:
        self.max_retries = max_retries

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
            if not table_norm or not column_norm:
                continue
            tables.add(table_norm)
            columns_by_table.setdefault(table_norm, set()).add(column_norm)
            types_by_table_column[(table_norm, column_norm)] = str(data_type)
        return SqlSchemaInfo(tables=tables, columns_by_table=columns_by_table, types_by_table_column=types_by_table_column)

    def build_obligations(self, query: str, schema: SqlSchemaInfo) -> dict[str, Any]:
        lowered = query.lower()
        required_tables = sorted([table for table in schema.tables if table in lowered])
        required_fact_tables: set[str] = set()

        keyword_to_table_hint = {
            "payment": ("payment",),
            "order": ("order",),
            "customer": ("customer",),
            "product": ("product",),
            "salary": ("salary", "faculty"),
            "faculty": ("faculty",),
            "sales": ("sales", "fact"),
        }
        for keyword, hints in keyword_to_table_hint.items():
            if keyword not in lowered:
                continue
            for table in schema.tables:
                if any(hint in table for hint in hints):
                    required_fact_tables.add(table)

        normalized_years = sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", query)))

        required_status_values = sorted({status for status in _STATUS_KEYWORDS if status in lowered})
        requires_topn = bool(re.search(r"\b(top|highest|lowest|bottom)\b", lowered))
        requires_aggregation = bool(
            re.search(r"\b(count|average|avg|sum|total|increase|ratio|rate|group by|how many)\b", lowered)
        )
        requires_distinct = bool(re.search(r"\b(distinct|unique)\b", lowered))
        requires_order_desc = bool(re.search(r"\b(top|highest|most|max)\b", lowered))
        requires_join = len(required_tables) + len(required_fact_tables) >= 2 or "join" in lowered
        required_metrics = sorted({metric for metric in _METRIC_KEYWORDS if metric in lowered})

        if re.search(r"\b(increase|growth|delta|change|trend)\b", lowered) and normalized_years:
            inferred_fact_tables = [table for table in schema.tables if ("fact" in table or "sales" in table)]
            required_fact_tables.update(inferred_fact_tables)

        return {
            "required_tables": required_tables,
            "required_fact_tables": sorted(required_fact_tables),
            "required_years": normalized_years,
            "required_status_values": required_status_values,
            "required_metrics": required_metrics,
            "requires_topn": requires_topn,
            "requires_aggregation": requires_aggregation,
            "requires_distinct": requires_distinct,
            "requires_order_desc": requires_order_desc,
            "requires_join": requires_join,
        }

    @staticmethod
    def _extract_cte_names(sql: str) -> set[str]:
        cte_names: set[str] = set()
        lowered = sql.lower()
        if "with" not in lowered:
            return cte_names
        for match in re.finditer(r"\b([a-zA-Z_][\w]*)\s+as\s*\(", sql, flags=re.IGNORECASE):
            cte_names.add(match.group(1).lower())
        return cte_names

    @staticmethod
    def _extract_table_refs(sql: str) -> list[str]:
        refs: list[str] = []
        for match in re.finditer(r"\b(?:from|join)\s+([a-zA-Z_][\w\.\"]*)", sql, flags=re.IGNORECASE):
            refs.append(_normalize_table_name(match.group(1)))
        return refs

    @staticmethod
    def _extract_alias_map(sql: str) -> dict[str, str]:
        alias_map: dict[str, str] = {}
        pattern = re.compile(
            r"\b(?:from|join)\s+([a-zA-Z_][\w\.\"]*)\s+(?:as\s+)?([a-zA-Z_]\w*)",
            flags=re.IGNORECASE,
        )
        for match in pattern.finditer(sql):
            table_name = _normalize_table_name(match.group(1))
            alias = _strip_identifier(match.group(2)).lower()
            if table_name and alias:
                alias_map[alias] = table_name
        return alias_map

    def validate_before_execution(self, *, query: str, sql: str, schema: SqlSchemaInfo) -> SqlGuardReport:
        obligations = self.build_obligations(query, schema)
        issues: list[SqlGuardIssue] = []
        normalized_sql = re.sub(r"\s+", " ", sql.strip())
        lowered_sql = normalized_sql.lower()

        if any(prefix in lowered_sql for prefix in _FABRICATED_TABLE_PREFIXES):
            issues.append(
                SqlGuardIssue(
                    code="fabricated_table_prefix",
                    message="SQL uses fabricated table naming patterns (simulated/fake/invented).",
                )
            )

        if re.search(r"\bvalues\s*\(", lowered_sql):
            issues.append(
                SqlGuardIssue(
                    code="values_fabrication_blocked",
                    message="VALUES-based synthetic business data is blocked for SQL generation.",
                )
            )

        if " union " in lowered_sql and re.search(r"\bunion(?:\s+all)?\s+select\s+(?:'[^']*'|\"[^\"]*\"|\d)", lowered_sql):
            issues.append(
                SqlGuardIssue(
                    code="union_literal_fabrication_blocked",
                    message="UNION with literal row fabrication is blocked.",
                )
            )

        for cte_match in re.finditer(r"\b([a-zA-Z_]\w*)\s+as\s*\((.*?)\)", sql, flags=re.IGNORECASE | re.DOTALL):
            cte_name = cte_match.group(1)
            cte_body = cte_match.group(2)
            if _looks_like_literal_projection(cte_body):
                issues.append(
                    SqlGuardIssue(
                        code="cte_literal_fabrication_blocked",
                        message=f"CTE '{cte_name}' appears to fabricate rows without a source table.",
                    )
                )

        cte_names = self._extract_cte_names(sql)
        table_refs = [ref for ref in self._extract_table_refs(sql) if ref]
        real_refs = [ref for ref in table_refs if ref not in cte_names]
        unknown_refs = sorted({ref for ref in real_refs if ref not in schema.tables})
        if unknown_refs:
            issues.append(
                SqlGuardIssue(
                    code="unknown_table_reference",
                    message="SQL references tables that are not present in the current schema.",
                    details={"unknown_tables": unknown_refs},
                )
            )

        for required_table in obligations["required_tables"] + obligations["required_fact_tables"]:
            if required_table and required_table not in real_refs:
                issues.append(
                    SqlGuardIssue(
                        code="required_table_missing",
                        message=f"Required table '{required_table}' is missing from SQL.",
                        details={"required_table": required_table},
                    )
                )

        for year in obligations["required_years"]:
            if year not in lowered_sql:
                issues.append(
                    SqlGuardIssue(
                        code="required_year_missing",
                        message=f"Required year filter '{year}' is missing from SQL.",
                        details={"year": year},
                    )
                )

        for status in obligations["required_status_values"]:
            if status not in lowered_sql:
                issues.append(
                    SqlGuardIssue(
                        code="required_filter_missing",
                        message=f"Required filter '{status}' is missing from SQL.",
                        details={"filter": status},
                    )
                )

        if obligations["requires_aggregation"] and not any(func in lowered_sql for func in _AGGREGATE_FUNCTIONS):
            issues.append(
                SqlGuardIssue(
                    code="aggregation_missing",
                    message="Query requires aggregation but no aggregate function was detected.",
                )
            )

        if obligations["requires_distinct"] and "distinct" not in lowered_sql:
            issues.append(
                SqlGuardIssue(
                    code="distinct_missing",
                    message="Query requires DISTINCT/UNIQUE semantics but DISTINCT is missing.",
                )
            )

        if obligations["requires_topn"] and "limit" not in lowered_sql:
            issues.append(
                SqlGuardIssue(
                    code="topn_limit_missing",
                    message="Top-N intent detected but SQL is missing LIMIT.",
                )
            )
        if obligations["requires_topn"] and "order by" not in lowered_sql:
            issues.append(
                SqlGuardIssue(
                    code="topn_order_missing",
                    message="Top-N intent detected but SQL is missing ORDER BY.",
                )
            )

        if obligations["requires_order_desc"] and "order by" in lowered_sql and " desc" not in lowered_sql:
            issues.append(
                SqlGuardIssue(
                    code="order_direction_suspect",
                    message="Top/highest intent detected but ORDER BY DESC is missing.",
                )
            )

        if obligations["requires_join"] and len(set(real_refs)) >= 2 and " join " not in lowered_sql:
            issues.append(
                SqlGuardIssue(
                    code="join_missing",
                    message="Multiple required tables detected but SQL has no explicit JOIN.",
                )
            )

        missing_on = re.findall(
            r"\b(?:left|right|inner|full|outer)?\s*join\s+[a-zA-Z_][\w\.\"]*(?!\s*(?:on|using))",
            lowered_sql,
        )
        if missing_on:
            issues.append(
                SqlGuardIssue(
                    code="join_on_missing",
                    message="Detected JOIN without ON/USING clause.",
                )
            )

        if " cross join " in lowered_sql and "cartesian" not in query.lower():
            issues.append(
                SqlGuardIssue(
                    code="cartesian_join_risk",
                    message="CROSS JOIN detected; potential cartesian product.",
                )
            )

        alias_map = self._extract_alias_map(sql)
        for table in real_refs:
            alias_map.setdefault(table, table)
        for alias_left, col_left, alias_right, col_right in re.findall(
            r"\b([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)\s*=\s*([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)",
            sql,
            flags=re.IGNORECASE,
        ):
            left_table = alias_map.get(alias_left.lower(), alias_left.lower())
            right_table = alias_map.get(alias_right.lower(), alias_right.lower())
            left_col = col_left.lower()
            right_col = col_right.lower()

            left_exists = left_col in schema.columns_by_table.get(left_table, set())
            right_exists = right_col in schema.columns_by_table.get(right_table, set())
            if not left_exists or not right_exists:
                issues.append(
                    SqlGuardIssue(
                        code="join_on_unknown_column",
                        message="JOIN ON clause references unknown columns.",
                        details={
                            "left": f"{left_table}.{left_col}",
                            "right": f"{right_table}.{right_col}",
                        },
                    )
                )
                continue

            left_type = schema.types_by_table_column.get((left_table, left_col), "")
            right_type = schema.types_by_table_column.get((right_table, right_col), "")
            if left_type and right_type:
                left_group = _type_group(left_type)
                right_group = _type_group(right_type)
                if left_group != "other" and right_group != "other" and left_group != right_group:
                    issues.append(
                        SqlGuardIssue(
                            code="join_type_mismatch",
                            message="JOIN key types appear incompatible.",
                            details={
                                "left": f"{left_table}.{left_col}:{left_type}",
                                "right": f"{right_table}.{right_col}:{right_type}",
                            },
                        )
                    )

        return SqlGuardReport(
            blocked=len(issues) > 0,
            issues=issues,
            obligations=obligations,
            query=query,
            sql=sql,
            available_tables=sorted(schema.tables),
        )

    def validate_after_execution(
        self,
        *,
        query: str,
        sql: str,
        dataframe: pd.DataFrame | None,
        schema: SqlSchemaInfo,
    ) -> SqlGuardReport:
        obligations = self.build_obligations(query, schema)
        issues: list[SqlGuardIssue] = []
        lowered_sql = sql.lower()
        result_columns = [str(column).lower() for column in (dataframe.columns if dataframe is not None else [])]

        if obligations["requires_topn"] and "limit" not in lowered_sql:
            issues.append(
                SqlGuardIssue(
                    code="post_topn_limit_missing",
                    message="Post-check: Top-N question is missing LIMIT in final SQL.",
                )
            )

        if obligations["requires_topn"] and "order by" not in lowered_sql:
            issues.append(
                SqlGuardIssue(
                    code="post_topn_order_missing",
                    message="Post-check: Top-N question is missing ORDER BY in final SQL.",
                )
            )

        for metric in obligations["required_metrics"]:
            if metric in {"avg", "average"} and any(token in lowered_sql for token in ("avg(", "average")):
                continue
            if metric not in lowered_sql and not any(metric in column for column in result_columns):
                issues.append(
                    SqlGuardIssue(
                        code="post_required_metric_missing",
                        message=f"Post-check: required metric '{metric}' not reflected in SQL/output columns.",
                        details={"metric": metric},
                    )
                )

        if "for each" in query.lower() and "group by" not in lowered_sql:
            issues.append(
                SqlGuardIssue(
                    code="post_groupby_missing",
                    message="Post-check: 'for each' intent suggests GROUP BY but none found.",
                )
            )

        required_projection_tokens = ["city", "state", "salary", "rank", "payment"]
        for token in required_projection_tokens:
            if token in query.lower() and not any(token in column for column in result_columns):
                issues.append(
                    SqlGuardIssue(
                        code="post_projection_missing",
                        message=f"Post-check: expected output column related to '{token}' is missing.",
                        details={"token": token},
                    )
                )

        return SqlGuardReport(
            blocked=len(issues) > 0,
            issues=issues,
            obligations=obligations,
            query=query,
            sql=sql,
            available_tables=sorted(schema.tables),
        )

    @staticmethod
    def to_retry_error_payload(report: SqlGuardReport, *, attempt: int, max_attempts: int) -> dict[str, Any]:
        return {
            "error_type": "sql_guardrail",
            "attempt": attempt,
            "max_attempts": max_attempts,
            "report": report.to_dict(),
        }

    @staticmethod
    def to_retry_error_text(payload: dict[str, Any]) -> str:
        return "SQL_GUARDRAIL_ERROR " + json.dumps(payload, ensure_ascii=False, default=str)
