from __future__ import annotations

import pandas as pd

from databao.agent.executors.sql_guardrails import SqlGuardrail, SqlSchemaInfo


def _schema(
    tables: list[str],
    columns_by_table: dict[str, list[str]],
    types_by_table_column: dict[tuple[str, str], str] | None = None,
) -> SqlSchemaInfo:
    normalized_columns = {table.lower(): {column.lower() for column in columns} for table, columns in columns_by_table.items()}
    normalized_types = {}
    for (table, column), sql_type in (types_by_table_column or {}).items():
        normalized_types[(table.lower(), column.lower())] = sql_type
    return SqlSchemaInfo(
        tables={table.lower() for table in tables},
        columns_by_table=normalized_columns,
        types_by_table_column=normalized_types,
    )


def _has_issue(report, code: str) -> bool:
    return any(issue.code == code for issue in report.issues)


def test_sql_guard_rejects_nonexistent_or_simulated_tables() -> None:
    guard = SqlGuardrail()
    schema = _schema(
        tables=["orders", "order_payments", "customers"],
        columns_by_table={"orders": ["order_id", "customer_id"], "order_payments": ["order_id", "payment_value"], "customers": ["customer_id"]},
    )
    sql = """
    WITH simulated_payments AS (
      SELECT order_id, 100 AS payment_value FROM orders
    )
    SELECT * FROM simulated_payments JOIN invented_customer_dim icd ON simulated_payments.order_id = icd.order_id
    """
    report = guard.validate_before_execution(query="average payment per order", sql=sql, schema=schema)
    assert report.blocked is True
    assert _has_issue(report, "fabricated_table_prefix")
    assert _has_issue(report, "unknown_table_reference")


def test_sql_guard_rejects_values_based_fabricated_business_data() -> None:
    guard = SqlGuardrail()
    schema = _schema(
        tables=["orders"],
        columns_by_table={"orders": ["order_id", "customer_id"]},
    )
    sql = "WITH fake_orders AS (SELECT * FROM (VALUES ('o1','c1'),('o2','c2')) AS v(order_id, customer_id)) SELECT * FROM fake_orders"
    report = guard.validate_before_execution(query="top customers by orders", sql=sql, schema=schema)
    assert report.blocked is True
    assert _has_issue(report, "values_fabrication_blocked")
    assert _has_issue(report, "fabricated_table_prefix")


def test_sql_obligation_checker_requires_key_fact_table() -> None:
    guard = SqlGuardrail()
    schema = _schema(
        tables=["hardware_fact_sales_monthly", "hardware_dim_product"],
        columns_by_table={
            "hardware_fact_sales_monthly": ["product_code", "fiscal_year"],
            "hardware_dim_product": ["product_code", "segment"],
        },
    )
    sql = "SELECT segment, COUNT(DISTINCT product_code) FROM hardware_dim_product GROUP BY segment"
    report = guard.validate_before_execution(
        query="list hardware product segments ordered by highest increase from 2020 to 2021",
        sql=sql,
        schema=schema,
    )
    assert report.blocked is True
    assert _has_issue(report, "required_table_missing")


def test_sql_obligation_checker_requires_explicit_filter_when_requested() -> None:
    guard = SqlGuardrail()
    schema = _schema(
        tables=["olist_orders", "olist_customers"],
        columns_by_table={
            "olist_orders": ["order_id", "customer_id", "order_status"],
            "olist_customers": ["customer_id", "customer_unique_id"],
        },
    )
    sql = """
    SELECT c.customer_unique_id, COUNT(*) AS order_count
    FROM olist_orders o
    JOIN olist_customers c ON o.customer_id = c.customer_id
    GROUP BY c.customer_unique_id
    """
    report = guard.validate_before_execution(
        query="top 3 customers with highest number of delivered orders",
        sql=sql,
        schema=schema,
    )
    assert report.blocked is True
    assert _has_issue(report, "required_filter_missing")


def test_join_guard_rejects_missing_or_invalid_on_clause() -> None:
    guard = SqlGuardrail()
    schema = _schema(
        tables=["orders", "order_payments"],
        columns_by_table={"orders": ["order_id", "customer_id"], "order_payments": ["order_id", "payment_value"]},
        types_by_table_column={
            ("orders", "order_id"): "VARCHAR",
            ("orders", "customer_id"): "VARCHAR",
            ("order_payments", "order_id"): "INTEGER",
            ("order_payments", "payment_value"): "DOUBLE",
        },
    )
    sql = "SELECT * FROM orders o JOIN order_payments p ON o.non_existing = p.order_id"
    report = guard.validate_before_execution(query="join orders with payments", sql=sql, schema=schema)
    assert report.blocked is True
    assert _has_issue(report, "join_on_unknown_column")


def test_postcheck_rejects_missing_topn_or_required_metric() -> None:
    guard = SqlGuardrail()
    schema = _schema(
        tables=["olist_orders", "olist_order_payments"],
        columns_by_table={
            "olist_orders": ["order_id", "order_status"],
            "olist_order_payments": ["order_id", "payment_value"],
        },
    )
    sql = """
    SELECT o.order_status
    FROM olist_orders o
    JOIN olist_order_payments p ON o.order_id = p.order_id
    """
    report = guard.validate_after_execution(
        query="top 3 customers by average payment",
        sql=sql,
        dataframe=pd.DataFrame([{"order_status": "delivered"}]),
        schema=schema,
    )
    assert report.blocked is True
    assert _has_issue(report, "post_topn_limit_missing")
    assert _has_issue(report, "post_required_metric_missing")


def test_retry_receives_structured_guardrail_feedback() -> None:
    guard = SqlGuardrail(max_retries=3)
    schema = _schema(
        tables=["orders"],
        columns_by_table={"orders": ["order_id"]},
    )
    report = guard.validate_before_execution(query="top orders", sql="SELECT * FROM simulated_orders", schema=schema)
    payload = guard.to_retry_error_payload(report, attempt=1, max_attempts=3)
    text = guard.to_retry_error_text(payload)
    assert payload["error_type"] == "sql_guardrail"
    assert payload["attempt"] == 1
    assert "report" in payload
    assert text.startswith("SQL_GUARDRAIL_ERROR ")
    assert "fabricated_table_prefix" in text


def test_spider_style_local004_blocks_simulated_payments() -> None:
    guard = SqlGuardrail()
    schema = _schema(
        tables=["orders", "order_payments", "customers"],
        columns_by_table={
            "orders": ["order_id", "customer_id", "order_purchase_timestamp"],
            "order_payments": ["order_id", "payment_value"],
            "customers": ["customer_id", "customer_unique_id"],
        },
    )
    sql = """
    WITH simulated_payments AS (
      SELECT order_id, 100.0 AS payment_value FROM orders
      UNION ALL
      SELECT 'fake-order', 88.0
    )
    SELECT * FROM orders o LEFT JOIN simulated_payments sp ON o.order_id = sp.order_id
    """
    report = guard.validate_before_execution(
        query="top 3 customers by average payment and lifecycle weeks",
        sql=sql,
        schema=schema,
    )
    assert report.blocked is True
    assert _has_issue(report, "fabricated_table_prefix")
    assert _has_issue(report, "union_literal_fabrication_blocked")


def test_spider_style_local058_requires_fact_table_and_years() -> None:
    guard = SqlGuardrail()
    schema = _schema(
        tables=["hardware_fact_sales_monthly", "hardware_dim_product"],
        columns_by_table={
            "hardware_fact_sales_monthly": ["product_code", "fiscal_year"],
            "hardware_dim_product": ["product_code", "segment"],
        },
    )
    sql = "SELECT segment, COUNT(*) FROM hardware_dim_product GROUP BY segment"
    report = guard.validate_before_execution(
        query="for 2020 and 2021, order by highest percentage increase in unique fact sales products by segment",
        sql=sql,
        schema=schema,
    )
    assert report.blocked is True
    assert _has_issue(report, "required_table_missing")
    assert _has_issue(report, "required_year_missing")


def test_spider_style_local075_flags_extra_filters_and_missing_purchase_logic() -> None:
    guard = SqlGuardrail()
    schema = _schema(
        tables=["shopping_cart_page_hierarchy", "shopping_cart_events"],
        columns_by_table={
            "shopping_cart_page_hierarchy": ["page_id", "page_name", "product_id"],
            "shopping_cart_events": ["page_id", "visit_id", "event_type"],
        },
    )
    sql = """
    SELECT h.page_id, h.page_name
    FROM shopping_cart_page_hierarchy h
    LEFT JOIN shopping_cart_events e ON h.page_id = e.page_id
    WHERE h.page_name NOT IN ('Checkout', 'Confirmation')
    """
    report = guard.validate_after_execution(
        query="count viewed added to cart left in cart without purchased and actual purchases, excluding page id 1,2,12,13",
        sql=sql,
        dataframe=pd.DataFrame([{"page_id": 8, "page_name": "Abalone"}]),
        schema=schema,
    )
    assert report.blocked is True
    assert _has_issue(report, "post_required_metric_missing")


def test_sql_guard_allows_information_schema_columns_introspection() -> None:
    guard = SqlGuardrail()
    schema = _schema(
        tables=["orders"],
        columns_by_table={"orders": ["order_id", "customer_id"]},
    )
    sql = "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'orders'"
    report = guard.validate_before_execution(query="show orders schema overview", sql=sql, schema=schema)
    assert report.blocked is False
    assert not _has_issue(report, "unknown_table_reference")


def test_sql_guard_allows_pragma_table_info_statement_or_function() -> None:
    guard = SqlGuardrail()
    schema = _schema(
        tables=["orders"],
        columns_by_table={"orders": ["order_id", "customer_id"]},
    )

    report_statement = guard.validate_before_execution(
        query="describe table orders",
        sql="PRAGMA table_info('orders')",
        schema=schema,
    )
    assert report_statement.blocked is False
    assert not _has_issue(report_statement, "unknown_table_reference")

    report_function = guard.validate_before_execution(
        query="describe table orders",
        sql="SELECT name, type FROM pragma_table_info('orders')",
        schema=schema,
    )
    assert report_function.blocked is False
    assert not _has_issue(report_function, "unknown_table_reference")


def test_sql_guard_allows_derived_summary_union_from_real_table_aggregate() -> None:
    guard = SqlGuardrail()
    schema = _schema(
        tables=["yahoo_finance_global_markets_2026"],
        columns_by_table={"yahoo_finance_global_markets_2026": ["asset_class", "price"]},
    )
    sql = """
    WITH dataset_stats AS (
      SELECT
        COUNT(*) AS total_rows,
        COUNT(DISTINCT asset_class) AS distinct_asset_classes
      FROM yahoo_finance_global_markets_2026
    )
    SELECT '总行数' AS metric, total_rows AS value FROM dataset_stats
    UNION ALL
    SELECT '资产类别数' AS metric, distinct_asset_classes AS value FROM dataset_stats
    """
    report = guard.validate_before_execution(query="summary overview", sql=sql, schema=schema)
    assert report.blocked is False
    assert not _has_issue(report, "union_literal_fabrication_blocked")
    assert not _has_issue(report, "cte_literal_fabrication_blocked")


def test_sql_guard_still_blocks_union_literal_business_row_fabrication() -> None:
    guard = SqlGuardrail()
    schema = _schema(
        tables=["orders"],
        columns_by_table={"orders": ["order_id", "customer_id"]},
    )
    sql = """
    SELECT order_id, customer_id FROM orders
    UNION ALL
    SELECT 'fake-order-id', 'fake-customer'
    """
    report = guard.validate_before_execution(query="top customers by orders", sql=sql, schema=schema)
    assert report.blocked is True
    assert _has_issue(report, "union_literal_fabrication_blocked")


def test_sql_guard_parse_failure_falls_back_without_crashing() -> None:
    guard = SqlGuardrail()
    schema = _schema(
        tables=["orders"],
        columns_by_table={"orders": ["order_id", "customer_id"]},
    )
    sql = "SELECT FROM FROM ???"
    report = guard.validate_before_execution(query="show orders", sql=sql, schema=schema)
    assert isinstance(report.blocked, bool)
    assert isinstance(report.issues, list)
    assert report.query == "show orders"


def test_sql_guard_metadata_sql_skips_business_obligation_blockers() -> None:
    guard = SqlGuardrail()
    schema = _schema(
        tables=["orders", "payments"],
        columns_by_table={
            "orders": ["order_id", "customer_id", "order_status"],
            "payments": ["order_id", "payment_value"],
        },
    )
    sql = "SELECT column_name FROM information_schema.columns WHERE table_name = 'orders'"
    report = guard.validate_before_execution(
        query="top 3 customers by average payment",
        sql=sql,
        schema=schema,
    )
    assert report.blocked is False
    assert not _has_issue(report, "required_table_missing")
    assert not _has_issue(report, "aggregation_missing")
    assert not _has_issue(report, "topn_limit_missing")
