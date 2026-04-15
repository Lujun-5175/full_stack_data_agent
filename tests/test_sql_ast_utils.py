from __future__ import annotations

import databao.agent.sql.ast_utils as sql_ast_utils
import pytest
from full_stack_data_agent.app.sql_ast_utils import parse_sql_candidate


def test_parse_sql_candidate_accepts_single_select() -> None:
    parsed = parse_sql_candidate("SELECT customer_id, COUNT(*) AS total FROM orders GROUP BY customer_id", dialect="duckdb")
    if sql_ast_utils.parse is None or sql_ast_utils.parse_one is None or sql_ast_utils.exp is None:
        assert parsed.is_select_like is False
        assert parsed.is_safe_query is False
        assert parsed.statement_kind == "select"
        assert parsed.read_only is True
        assert parsed.supported_for_inspection is True
    else:
        assert parsed.is_select_like is True
        assert parsed.is_safe_query is True
        assert "orders" in parsed.tables
        assert "customer_id" in parsed.columns
        assert "total" in parsed.selected_aliases


def test_parse_sql_candidate_accepts_with_select() -> None:
    parsed = parse_sql_candidate(
        "WITH base AS (SELECT customer_id FROM orders) SELECT customer_id FROM base",
        dialect="duckdb",
    )
    if sql_ast_utils.parse is None or sql_ast_utils.parse_one is None or sql_ast_utils.exp is None:
        assert parsed.is_select_like is False
        assert parsed.is_safe_query is False
        assert parsed.statement_kind == "select"
        assert parsed.read_only is True
    else:
        assert parsed.is_select_like is True
        assert parsed.parse_errors == []


def test_parse_sql_candidate_rejects_multi_statement() -> None:
    parsed = parse_sql_candidate("SELECT 1; SELECT 2", dialect="duckdb")
    assert parsed.is_safe_query is False
    assert any("Multiple SQL statements" in item for item in parsed.parse_errors)


def test_parse_sql_candidate_rejects_forbidden_op() -> None:
    parsed = parse_sql_candidate("DROP TABLE orders", dialect="duckdb")
    assert parsed.is_select_like is False or parsed.is_safe_query is False


def test_parse_sql_candidate_marks_forbidden_writes_with_sqlglot() -> None:
    pytest.importorskip("sqlglot")
    parsed = parse_sql_candidate("DELETE FROM orders WHERE customer_id = 1", dialect="duckdb")

    assert parsed.is_safe_query is False
    assert any(item != "ast_unavailable" for item in parsed.forbidden_operations)


def test_parse_sql_candidate_fails_closed_when_sqlglot_missing(monkeypatch) -> None:
    monkeypatch.setattr(sql_ast_utils, "parse", None)
    monkeypatch.setattr(sql_ast_utils, "parse_one", None)
    monkeypatch.setattr(sql_ast_utils, "exp", None)

    parsed = sql_ast_utils.parse_sql_candidate("SELECT 1", dialect="duckdb")

    assert parsed.is_select_like is False
    assert parsed.is_safe_query is False
    assert parsed.statement_kind == "select"
    assert parsed.read_only is True
    assert "ast_unavailable" in parsed.forbidden_operations
