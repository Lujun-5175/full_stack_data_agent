from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


def _clean_identifier(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip()).strip("_").lower()


def _tokenize(value: str) -> list[str]:
    return [token for token in re.split(r"[_\W]+", str(value or "").lower()) if token]


def _normalize_sample_values(raw_values: Any) -> list[str]:
    if not isinstance(raw_values, (list, tuple, set)):
        return []
    values: list[str] = []
    for item in raw_values:
        cleaned = str(item).strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)
    return values[:12]


@dataclass(frozen=True)
class SemanticColumnSummary:
    column_name: str
    table_name: str
    sql_type: str = ""
    role_tags: list[str] = field(default_factory=list)
    sample_values: list[str] = field(default_factory=list)
    categorical_candidates: list[str] = field(default_factory=list)
    groupable: bool = False
    measure_like: bool = False
    id_like: bool = False
    entity_like: bool = False
    time_like: bool = False
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticTableSummary:
    table_name: str
    column_names: list[str] = field(default_factory=list)
    role_tags: list[str] = field(default_factory=list)
    candidate: bool = False
    preferred: bool = False
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticSchemaInventory:
    columns: list[SemanticColumnSummary] = field(default_factory=list)
    columns_by_name: dict[str, SemanticColumnSummary] = field(default_factory=dict)
    tables: list[SemanticTableSummary] = field(default_factory=list)
    candidate_tables: list[str] = field(default_factory=list)
    preferred_tables: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": [column.to_dict() for column in self.columns],
            "tables": [table.to_dict() for table in self.tables],
            "candidate_tables": list(self.candidate_tables),
            "preferred_tables": list(self.preferred_tables),
        }

    def compact_summary(self) -> dict[str, Any]:
        return {
            "candidate_tables": list(self.candidate_tables),
            "preferred_tables": list(self.preferred_tables),
            "columns": [
                {
                    "column_name": item.column_name,
                    "table_name": item.table_name,
                    "role_tags": list(item.role_tags),
                    "sample_values": list(item.sample_values[:5]),
                }
                for item in self.columns
            ],
        }


def _column_role_tags(column_name: str, sql_type: str, sample_values: list[str]) -> tuple[list[str], dict[str, bool]]:
    normalized = _clean_identifier(column_name)
    tokens = set(_tokenize(normalized))
    upper_type = str(sql_type or "").upper()
    numeric = any(marker in upper_type for marker in ("INT", "DECIMAL", "DOUBLE", "FLOAT", "NUMERIC", "REAL"))
    textual = any(marker in upper_type for marker in ("CHAR", "TEXT", "STRING", "VARCHAR"))
    time_like = any(marker in upper_type for marker in ("DATE", "TIME", "TIMESTAMP")) or bool(tokens & {"date", "time", "year", "month", "day"})
    id_like = normalized.endswith("_id") or normalized == "id" or ("id" in tokens and not numeric)
    measure_like = numeric and not id_like
    label_like = bool(tokens & {"name", "label", "title", "segment", "region", "category", "contract", "market", "customer"})
    status_like = bool(tokens & {"status", "state", "stage", "type", "churn", "premium", "active"})
    entity_like = bool(tokens & {"customer", "order", "product", "user", "contract", "region", "market", "segment"})
    sample_cardinality = len({value.lower() for value in sample_values if value})
    low_cardinality = sample_cardinality > 0 and sample_cardinality <= 12
    groupable = not measure_like and not time_like and (textual or label_like or status_like or entity_like or low_cardinality)

    tags: list[str] = []
    if id_like:
        tags.append("id_like")
    if entity_like:
        tags.append("entity_like")
    if groupable:
        tags.append("groupable_dimension")
    if low_cardinality or status_like:
        tags.append("categorical_filter_candidate")
    if time_like:
        tags.append("time_like")
    if measure_like:
        tags.append("numeric_measure")
    if status_like:
        tags.append("status_like")
    if label_like:
        tags.append("label_like")
    return tags, {
        "groupable": groupable,
        "measure_like": measure_like,
        "id_like": id_like,
        "entity_like": entity_like,
        "time_like": time_like,
    }


def _table_summary(
    table_name: str,
    columns: list[SemanticColumnSummary],
    query_tokens: set[str],
    query_text: str,
) -> SemanticTableSummary:
    normalized = _clean_identifier(table_name)
    tokens = set(_tokenize(normalized))
    singular_tokens = {token[:-1] for token in tokens if token.endswith("s")}
    role_tags: list[str] = []
    if "fact" in tokens or any(column.measure_like for column in columns):
        role_tags.append("fact_like")
    if any(column.time_like for column in columns):
        role_tags.append("time_scoped")
    if any(column.entity_like for column in columns):
        role_tags.append("entity_or_dimension")
    candidate = bool((tokens | singular_tokens) & query_tokens)
    preferred = candidate and ("fact_like" in role_tags or "time_scoped" in role_tags)
    if any(word in query_text for word in ("revenue", "sales", "orders", "payment", "count", "average", "highest", "lowest")) and "fact_like" in role_tags:
        preferred = True
    confidence = 0.35
    if candidate:
        confidence += 0.2
    if preferred:
        confidence += 0.25
    return SemanticTableSummary(
        table_name=table_name,
        column_names=[column.column_name for column in columns],
        role_tags=role_tags,
        candidate=candidate,
        preferred=preferred,
        confidence=min(confidence, 0.95),
    )


def build_semantic_schema_inventory(
    schema_snapshot: dict[str, Any] | None,
    *,
    query: str = "",
) -> SemanticSchemaInventory:
    snapshot = schema_snapshot or {}
    columns_by_table = snapshot.get("columns_by_table") or {}
    types_by_table_column = snapshot.get("types_by_table_column") or {}
    query_tokens = {token for token in _tokenize(query) if len(token) > 2}
    columns: list[SemanticColumnSummary] = []
    columns_by_name: dict[str, SemanticColumnSummary] = {}
    tables: list[SemanticTableSummary] = []
    per_table_columns: dict[str, list[SemanticColumnSummary]] = {}

    for raw_table, raw_columns in columns_by_table.items():
        table_name = str(raw_table).strip().lower()
        items = raw_columns.items() if isinstance(raw_columns, dict) else ((column, None) for column in (raw_columns or []))
        for raw_column, raw_values in items:
            column_name = str(raw_column).strip()
            if not table_name or not column_name:
                continue
            sql_type = str(
                types_by_table_column.get(f"{raw_table}.{raw_column}")
                or types_by_table_column.get(f"{table_name}.{column_name.lower()}")
                or types_by_table_column.get((raw_table, raw_column))
                or types_by_table_column.get((table_name, column_name.lower()))
                or ""
            )
            sample_values = _normalize_sample_values(raw_values)
            role_tags, flags = _column_role_tags(column_name, sql_type, sample_values)
            summary = SemanticColumnSummary(
                column_name=column_name,
                table_name=table_name,
                sql_type=sql_type,
                role_tags=role_tags,
                sample_values=sample_values,
                categorical_candidates=list(sample_values[:8]),
                groupable=flags["groupable"],
                measure_like=flags["measure_like"],
                id_like=flags["id_like"],
                entity_like=flags["entity_like"],
                time_like=flags["time_like"],
                confidence=0.8 if role_tags else 0.5,
            )
            columns.append(summary)
            columns_by_name[column_name.lower()] = summary
            per_table_columns.setdefault(table_name, []).append(summary)

    for table_name, table_columns in per_table_columns.items():
        tables.append(_table_summary(table_name, table_columns, query_tokens, query.lower()))

    candidate_tables = [table.table_name for table in tables if table.candidate or table.preferred]
    preferred_tables = [table.table_name for table in tables if table.preferred]
    return SemanticSchemaInventory(
        columns=columns,
        columns_by_name=columns_by_name,
        tables=tables,
        candidate_tables=list(dict.fromkeys(candidate_tables)),
        preferred_tables=list(dict.fromkeys(preferred_tables)),
    )
