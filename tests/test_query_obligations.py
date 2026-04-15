from __future__ import annotations

from full_stack_data_agent.app.query_obligations import build_query_obligations


def _schema() -> dict[str, object]:
    return {
        "tables": ["olist_orders", "olist_customers", "subscriptions"],
        "columns_by_table": {
            "olist_orders": {
                "order_id": [],
                "customer_id": [],
                "order_status": ["delivered", "shipped", "cancelled"],
                "payment_value": [],
                "revenue": [],
            },
            "olist_customers": {
                "customer_id": [],
                "customer_name": [],
                "customer_segment": ["premium", "standard"],
            },
            "subscriptions": {
                "customer_status": ["active", "churned"],
                "plan_tier": ["premium", "free"],
            },
        },
        "types_by_table_column": {
            ("olist_orders", "order_id"): "VARCHAR",
            ("olist_orders", "customer_id"): "VARCHAR",
            ("olist_orders", "order_status"): "VARCHAR",
            ("olist_orders", "payment_value"): "DOUBLE",
            ("olist_orders", "revenue"): "DOUBLE",
            ("olist_customers", "customer_name"): "VARCHAR",
            ("subscriptions", "customer_status"): "VARCHAR",
        },
    }


def test_build_query_obligations_extracts_implicit_group_ranking_and_grounded_filter() -> None:
    obligations = build_query_obligations("top 3 customers with highest number of delivered orders", _schema())

    assert any(group in {"customer_id", "customer_name"} for group in obligations.group_by)
    assert obligations.limit == 3
    assert obligations.sort and obligations.sort[0].direction == "desc"
    assert any(item.kind == "count" for item in obligations.metrics)
    assert any(item.column == "order_status" and str(item.value).lower() == "delivered" for item in obligations.filters)
    assert obligations.semantic_frame


def test_build_query_obligations_grounds_semantic_values_from_schema_samples() -> None:
    obligations = build_query_obligations("show churn rate for active premium customers", _schema())

    grounded = {(item.column, str(item.value).lower()) for item in obligations.filters}
    assert ("customer_status", "active") in grounded
    assert any(value == "premium" for _column, value in grounded)


def test_build_query_obligations_uses_real_metric_targets_for_ranking() -> None:
    highest_avg = build_query_obligations("highest average revenue", _schema())
    most_orders = build_query_obligations("most orders", _schema())
    lowest_churn = build_query_obligations("lowest churn rate", _schema())

    assert highest_avg.sort and highest_avg.sort[0].column == "avg_revenue"
    assert most_orders.sort and most_orders.sort[0].column == "count_orders"
    assert lowest_churn.sort and lowest_churn.sort[0].column == "churn_rate"
    assert all(item.column != "max_metric" for item in highest_avg.sort + most_orders.sort + lowest_churn.sort)


def test_build_query_obligations_keeps_candidate_tables_soft_when_only_token_overlap_exists() -> None:
    obligations = build_query_obligations("customer overview", _schema())

    assert obligations.candidate_tables
    assert obligations.base_tables in (None, [])


def test_build_query_obligations_falls_back_when_llm_is_unavailable() -> None:
    obligations = build_query_obligations(
        "top 3 customers with highest number of delivered orders",
        _schema(),
        llm_call=lambda _prompt: (_ for _ in ()).throw(RuntimeError("llm unavailable")),
    )

    assert obligations.limit == 3
    assert obligations.sort
    assert obligations.filters
