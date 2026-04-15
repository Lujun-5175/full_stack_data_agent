from __future__ import annotations

from full_stack_data_agent.app.query_obligations import QueryObligations, SortObligation
from full_stack_data_agent.app.sql_guardrail import verify_sql_candidate


def _schema() -> dict[str, object]:
    return {
        "tables": ["customers"],
        "columns_by_table": {
            "customers": ["contract", "customerid", "churn", "churned_customers", "total_customers", "churn_rate"],
        },
    }


def test_verify_sql_candidate_flags_missing_group_by() -> None:
    obligations = QueryObligations(group_by=["Contract"])
    report, _parsed = verify_sql_candidate("SELECT contract, COUNT(*) FROM customers", obligations, _schema(), attempt=1)
    assert report.status == "repairable"
    assert any(item.startswith("group_by:") for item in report.missing_obligations)


def test_verify_sql_candidate_flags_missing_derived_metric() -> None:
    obligations = QueryObligations(
        derived_metrics=[],
        required_output_columns=["contract", "churn_rate"],
        sort=[SortObligation(column="churn_rate", direction="desc")],
    )
    report, _parsed = verify_sql_candidate(
        "SELECT contract, COUNT(*) AS total_customers FROM customers GROUP BY contract",
        obligations,
        _schema(),
        attempt=1,
    )
    assert report.status == "repairable"
    assert any(item.startswith("output_column:") for item in report.missing_obligations)


def test_verify_sql_candidate_flags_missing_limit_sort() -> None:
    obligations = QueryObligations(limit=3, sort=[SortObligation(column="churn_rate", direction="desc")])
    report, _parsed = verify_sql_candidate("SELECT * FROM customers", obligations, _schema(), attempt=1)
    assert "sort" in report.missing_obligations
    assert "limit" in report.missing_obligations
