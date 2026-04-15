from __future__ import annotations

import pandas as pd

from full_stack_data_agent.app.query_obligations import QueryObligations
from full_stack_data_agent.app.sql_execution_validation import validate_sql_result


def test_validate_sql_result_fails_empty_result() -> None:
    obligations = QueryObligations(group_by=["contract"])
    report = validate_sql_result(pd.DataFrame(columns=["contract", "churn_rate"]), obligations)
    assert report.status == "hard_failed"
    assert report.empty_result is True


def test_validate_sql_result_fails_missing_required_columns() -> None:
    obligations = QueryObligations(required_output_columns=["contract", "churn_rate"])
    df = pd.DataFrame([{"contract": "A", "total_customers": 100}])
    report = validate_sql_result(df, obligations)
    assert report.status in {"repairable", "hard_failed"}
    assert "churn_rate" in report.missing_columns


def test_validate_sql_result_flags_grouped_shape_degradation() -> None:
    obligations = QueryObligations(group_by=["contract"])
    df = pd.DataFrame([{"contract": "A", "churn_rate": 0.1}])
    report = validate_sql_result(df, obligations)
    assert report.unexpected_shape is True
