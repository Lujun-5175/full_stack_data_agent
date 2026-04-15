from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

try:
    from sqlglot import exp, parse, parse_one
except ImportError:  # pragma: no cover
    exp = None
    parse = None
    parse_one = None


FORBIDDEN_EXPRESSIONS = tuple(
    candidate
    for candidate in (
        getattr(exp, "Insert", None),
        getattr(exp, "Update", None),
        getattr(exp, "Delete", None),
        getattr(exp, "Drop", None),
        getattr(exp, "Alter", None),
        getattr(exp, "TruncateTable", None),
        getattr(exp, "Command", None),
        getattr(exp, "Copy", None),
        getattr(exp, "Call", None),
    )
    if candidate is not None
)

_SQL_KEYWORDS = {
    "select", "from", "where", "group", "by", "order", "limit", "having", "as", "join", "left", "right",
    "inner", "outer", "full", "on", "with", "union", "all", "distinct", "and", "or", "not", "desc", "asc",
    "case", "when", "then", "else", "end", "pragma",
}


def _normalize_identifier(value: str) -> str:
    return str(value or "").replace('"', "").replace("`", "").strip().lower()


@dataclass(frozen=True)
class ParsedSQLCandidate:
    sql_text: str
    dialect: str
    ast: Any | None
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    selected_expressions: list[str] = field(default_factory=list)
    selected_aliases: list[str] = field(default_factory=list)
    group_by_columns: list[str] = field(default_factory=list)
    order_by_columns: list[str] = field(default_factory=list)
    limit_value: int | None = None
    where_predicates: list[str] = field(default_factory=list)
    having_predicates: list[str] = field(default_factory=list)
    aggregate_functions: list[str] = field(default_factory=list)
    join_refs: list[str] = field(default_factory=list)
    joins: list[dict[str, Any]] = field(default_factory=list)
    forbidden_operations: list[str] = field(default_factory=list)
    cte_names: list[str] = field(default_factory=list)
    statement_kind: str = "unknown"
    read_only: bool = False
    metadata_query: bool = False
    supported_for_inspection: bool = False
    is_select_like: bool = False
    is_safe_query: bool = False
    parse_errors: list[str] = field(default_factory=list)

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "dialect": self.dialect,
            "tables": list(self.tables),
            "columns": list(self.columns),
            "selected_expressions": list(self.selected_expressions),
            "selected_aliases": list(self.selected_aliases),
            "group_by_columns": list(self.group_by_columns),
            "order_by_columns": list(self.order_by_columns),
            "limit_value": self.limit_value,
            "where_predicates": list(self.where_predicates),
            "having_predicates": list(self.having_predicates),
            "aggregate_functions": list(self.aggregate_functions),
            "join_refs": list(self.join_refs),
            "joins": list(self.joins),
            "forbidden_operations": list(self.forbidden_operations),
            "cte_names": list(self.cte_names),
            "statement_kind": self.statement_kind,
            "read_only": self.read_only,
            "metadata_query": self.metadata_query,
            "supported_for_inspection": self.supported_for_inspection,
            "is_select_like": self.is_select_like,
            "is_safe_query": self.is_safe_query,
            "parse_errors": list(self.parse_errors),
        }


def contains_forbidden_operations(ast: Any | None) -> list[str]:
    if ast is None or exp is None:
        return ["ast_unavailable"]
    found: list[str] = []
    for forbidden in FORBIDDEN_EXPRESSIONS:
        for _ in ast.find_all(forbidden):
            found.append(str(forbidden.__name__).lower())
            break
    return sorted(set(found))


def extract_table_refs(ast: Any) -> list[str]:
    if ast is None or exp is None:
        return []
    return sorted({_normalize_identifier(node.name or "") for node in ast.find_all(exp.Table) if node.name})


def extract_column_refs(ast: Any) -> list[str]:
    if ast is None or exp is None:
        return []
    return sorted({_normalize_identifier(node.name or "") for node in ast.find_all(exp.Column) if node.name})


def extract_where_predicates(ast: Any) -> list[str]:
    if not _is_select_like_ast(ast):
        return []
    where = ast.args.get("where") if ast is not None else None
    return [where.this.sql()] if where is not None and where.this is not None else []


def extract_having_predicates(ast: Any) -> list[str]:
    if not _is_select_like_ast(ast):
        return []
    having = ast.args.get("having") if ast is not None else None
    return [having.this.sql()] if having is not None and having.this is not None else []


def extract_group_by(ast: Any) -> list[str]:
    if not _is_select_like_ast(ast):
        return []
    group = ast.args.get("group") if ast is not None else None
    return [expression.sql() for expression in (group.expressions or [])] if group is not None else []


def extract_order_by(ast: Any) -> list[str]:
    if not _is_select_like_ast(ast):
        return []
    order = ast.args.get("order") if ast is not None else None
    return [expression.sql() for expression in (order.expressions or [])] if order is not None else []


def extract_limit(ast: Any) -> int | None:
    if ast is None or exp is None or not _is_select_like_ast(ast):
        return None
    limit = ast.args.get("limit")
    try:
        value = limit.expression if limit is not None else None
        return int(value.this) if isinstance(value, exp.Literal) else None
    except Exception:
        return None


def extract_selected_aliases(ast: Any) -> list[str]:
    if ast is None or not _is_select_like_ast(ast):
        return []
    aliases: list[str] = []
    for expression in ast.selects:
        alias = expression.alias_or_name
        if alias:
            aliases.append(_normalize_identifier(alias))
    return sorted(set(aliases))


def extract_joins(ast: Any) -> list[dict[str, Any]]:
    if ast is None or exp is None:
        return []
    joins: list[dict[str, Any]] = []
    for join in ast.find_all(exp.Join):
        on_expr = join.args.get("on")
        joins.append(
            {
                "kind": str(join.args.get("side") or "join").lower(),
                "table": join.this.sql() if join.this is not None else None,
                "on": on_expr.sql() if on_expr is not None else None,
                "sql": join.sql(),
            }
        )
    return joins


def _is_select_like_ast(ast: Any | None) -> bool:
    if exp is None or ast is None:
        return False
    if isinstance(ast, exp.Select):
        return True
    if isinstance(ast, exp.With) and isinstance(ast.this, exp.Select):
        return True
    if isinstance(ast, exp.Subquery) and isinstance(ast.this, exp.Select):
        return True
    return any(isinstance(ast, cls) for cls in (exp.Union, exp.Select))


def _is_metadata_query(sql: str, ast: Any | None) -> bool:
    lowered_sql = str(sql or "").lower()
    if lowered_sql.startswith("pragma table_info"):
        return True
    if "pragma_table_info(" in lowered_sql:
        return True
    if "information_schema.columns" in lowered_sql:
        return True
    if exp is None or ast is None:
        return False
    pragma_cls = getattr(exp, "Pragma", None)
    if pragma_cls is not None and isinstance(ast, pragma_cls):
        return True
    return False


def _statement_kind(sql: str, ast: Any | None, forbidden_operations: list[str], parse_errors: list[str]) -> str:
    lowered_sql = str(sql or "").strip().lower()
    structural_parse_errors = [error for error in parse_errors if "Multiple SQL statements" in error or "required for SQL safety validation" not in error]
    if structural_parse_errors:
        if lowered_sql.startswith("pragma table_info") or "pragma_table_info(" in lowered_sql or "information_schema.columns" in lowered_sql:
            return "metadata_read"
        return "unknown"
    if _is_metadata_query(sql, ast):
        return "metadata_read"
    if lowered_sql.startswith("select") or lowered_sql.startswith("with"):
        return "select"
    if _is_select_like_ast(ast):
        return "select"
    ddl_prefixes = ("drop", "alter", "create", "truncate")
    write_prefixes = ("insert", "update", "delete", "merge", "replace", "copy", "call", "attach")
    if lowered_sql.startswith(ddl_prefixes):
        return "ddl"
    if lowered_sql.startswith(write_prefixes):
        return "write"
    if any(item in {"drop", "alter", "truncatetable"} for item in forbidden_operations):
        return "ddl"
    if any(item in {"insert", "update", "delete", "copy", "call", "command"} for item in forbidden_operations):
        return "write"
    return "unknown"


def parse_sql_candidate(sql_text: str, dialect: str = "duckdb") -> ParsedSQLCandidate:
    sql = str(sql_text or "").strip()
    parse_errors: list[str] = []
    ast = None
    lowered_sql = sql.lower()
    statement_parts = [part.strip() for part in sql.split(";") if part.strip()]
    if len(statement_parts) > 1:
        parse_errors.append("Multiple SQL statements are not allowed.")
    sqlglot_available = parse is not None and parse_one is not None and exp is not None
    if sqlglot_available and parse is not None:
        try:
            statements = parse(sql, read=dialect)
            if len([statement for statement in statements if statement is not None]) > 1:
                parse_errors.append("Multiple SQL statements are not allowed.")
        except Exception as exc:
            parse_errors.append(str(exc))
    if sqlglot_available and not parse_errors and parse_one is not None:
        try:
            ast = parse_one(sql, read=dialect)
        except Exception as exc:
            parse_errors.append(str(exc))
    is_select_like = _is_select_like_ast(ast) if sqlglot_available else False
    forbidden_operations = contains_forbidden_operations(ast if sqlglot_available else None)
    statement_kind = _statement_kind(sql, ast if sqlglot_available else None, forbidden_operations, parse_errors)
    metadata_query = statement_kind == "metadata_read"
    read_only = statement_kind in {"select", "metadata_read"}
    supported_for_inspection = statement_kind in {"select", "metadata_read"}
    is_safe_query = bool(read_only and not forbidden_operations and not parse_errors)

    if ast is not None and exp is not None:
        joins = extract_joins(ast)
        selected_expressions = [expression.sql() for expression in ast.selects] if _is_select_like_ast(ast) else []
        aggregate_functions = (
            sorted({_normalize_identifier(function.key) for function in ast.find_all(exp.AggFunc)})
            if _is_select_like_ast(ast)
            else []
        )
        return ParsedSQLCandidate(
            sql_text=sql,
            dialect=dialect,
            ast=ast,
            tables=extract_table_refs(ast),
            columns=extract_column_refs(ast),
            selected_expressions=selected_expressions,
            selected_aliases=extract_selected_aliases(ast),
            group_by_columns=extract_group_by(ast),
            order_by_columns=extract_order_by(ast),
            limit_value=extract_limit(ast),
            where_predicates=extract_where_predicates(ast),
            having_predicates=extract_having_predicates(ast),
            aggregate_functions=aggregate_functions,
            join_refs=[join["sql"] for join in joins if join.get("sql")],
            joins=joins,
            forbidden_operations=forbidden_operations,
            cte_names=sorted({_normalize_identifier(cte.alias_or_name or "") for cte in ast.find_all(exp.CTE) if cte.alias_or_name}),
            statement_kind=statement_kind,
            read_only=read_only,
            metadata_query=metadata_query,
            supported_for_inspection=supported_for_inspection,
            is_select_like=is_select_like,
            is_safe_query=is_safe_query,
            parse_errors=parse_errors,
        )

    fallback_tables = re.findall(r"\b(?:from|join)\s+([a-zA-Z_][\w\.]*)", sql, flags=re.IGNORECASE) if read_only else []
    if statement_kind == "metadata_read" and lowered_sql.startswith("pragma table_info"):
        fallback_tables.append("pragma_table_info")
    if statement_kind == "metadata_read" and "information_schema.columns" in lowered_sql:
        fallback_tables.append("information_schema.columns")
    select_match = re.search(r"\bselect\s+(.+?)(?:\bfrom\b|$)", sql, flags=re.IGNORECASE | re.DOTALL) if statement_kind == "select" else None
    select_segment = select_match.group(1) if select_match else ""
    normalized_fallback_tables = sorted({_normalize_identifier(item.split(".")[-1]) for item in fallback_tables})
    aggregate_tokens = {"count", "sum", "avg", "min", "max", "distinct"}
    column_segments = " ".join(
        segment
        for segment in (
            select_segment,
            " ".join(re.findall(r"\bwhere\s+(.+?)(?:\bgroup\b|\border\b|\blimit\b|$)", sql, flags=re.IGNORECASE)),
            " ".join(re.findall(r"\bhaving\s+(.+?)(?:\border\b|\blimit\b|$)", sql, flags=re.IGNORECASE)),
            " ".join(re.findall(r"\bgroup\s+by\s+([^\n;]+?)(?:\border\b|\blimit\b|$)", sql, flags=re.IGNORECASE)),
            " ".join(re.findall(r"\border\s+by\s+([^\n;]+?)(?:\blimit\b|$)", sql, flags=re.IGNORECASE)),
            " ".join(re.findall(r"\bon\s+([^\n;]+?)(?:\bjoin\b|\bwhere\b|\bgroup\b|\border\b|\blimit\b|$)", sql, flags=re.IGNORECASE)),
        )
        if segment
    )
    fallback_columns = (
        sorted(
            {
                _normalize_identifier(token)
                for token in re.findall(r"[a-zA-Z_][\w]*", column_segments)
                if token.lower() not in _SQL_KEYWORDS
                and token.lower() not in aggregate_tokens
                and _normalize_identifier(token) not in normalized_fallback_tables
                and not token.isdigit()
            }
        )
        if supported_for_inspection
        else []
    )
    return ParsedSQLCandidate(
        sql_text=sql,
        dialect=dialect,
        ast=None,
        tables=normalized_fallback_tables,
        columns=fallback_columns,
        selected_expressions=[item.strip() for item in re.split(r",(?![^(]*\))", select_segment) if item.strip()] or ([sql] if statement_kind == "select" else []),
        selected_aliases=sorted({_normalize_identifier(alias) for alias in re.findall(r"\bas\s+([a-zA-Z_]\w*)", select_segment, flags=re.IGNORECASE)}) if statement_kind == "select" else [],
        group_by_columns=[segment.strip() for block in re.findall(r"\bgroup\s+by\s+([a-zA-Z_][\w,\s]*)", sql, flags=re.IGNORECASE) for segment in block.split(",") if segment.strip()] if statement_kind == "select" else [],
        order_by_columns=[segment.strip() for block in re.findall(r"\border\s+by\s+([a-zA-Z_][\w,\s]*)", sql, flags=re.IGNORECASE) for segment in block.split(",") if segment.strip()] if statement_kind == "select" else [],
        limit_value=int(re.search(r"\blimit\s+(\d+)", sql, flags=re.IGNORECASE).group(1)) if statement_kind == "select" and re.search(r"\blimit\s+(\d+)", sql, flags=re.IGNORECASE) else None,
        where_predicates=[item.strip() for item in re.findall(r"\bwhere\s+(.+?)(?:\bgroup\b|\border\b|\blimit\b|$)", sql, flags=re.IGNORECASE)] if statement_kind == "select" else [],
        having_predicates=[item.strip() for item in re.findall(r"\bhaving\s+(.+?)(?:\border\b|\blimit\b|$)", sql, flags=re.IGNORECASE)] if statement_kind == "select" else [],
        aggregate_functions=sorted({token for token in ("count", "sum", "avg", "min", "max") if f"{token}(" in lowered_sql}) if statement_kind == "select" else [],
        join_refs=re.findall(r"\bjoin\b[^;]*", sql, flags=re.IGNORECASE) if statement_kind == "select" and " join " in lowered_sql else [],
        joins=[{"kind": "join", "table": None, "on": None, "sql": ref} for ref in re.findall(r"\bjoin\b[^;]*", sql, flags=re.IGNORECASE)] if statement_kind == "select" and " join " in lowered_sql else [],
        forbidden_operations=forbidden_operations,
        cte_names=sorted({_normalize_identifier(item) for item in re.findall(r"\b([a-zA-Z_]\w*)\s+as\s*\(", sql, flags=re.IGNORECASE)}),
        statement_kind=statement_kind,
        read_only=read_only,
        metadata_query=metadata_query,
        supported_for_inspection=supported_for_inspection,
        is_select_like=is_select_like,
        is_safe_query=is_safe_query,
        parse_errors=parse_errors,
    )
