from __future__ import annotations

from full_stack_data_agent.app.query_obligations import QueryObligations, SortObligation
from full_stack_data_agent.app.sql_guardrail import SQLGuardrailReport
from full_stack_data_agent.app.sql_repair import build_sql_repair_prompt


def test_build_sql_repair_prompt_contains_structured_sections() -> None:
    obligations = QueryObligations(
        group_by=["Contract"],
        required_output_columns=["contract", "churn_rate"],
        sort=[SortObligation(column="churn_rate", direction="desc")],
        limit=3,
    )
    guardrail_report = SQLGuardrailReport(
        status="repairable",
        attempt=1,
        parsed=True,
        parse_error=None,
        missing_obligations=["group_by:Contract", "output_column:churn_rate"],
        wrong_refs=["unknown_column:foo"],
        repair_hints=["Add churn_rate alias."],
        final_reason="Missing obligations.",
    )

    prompt = build_sql_repair_prompt(
        original_query="Top 3 churn rate by contract",
        obligations=obligations,
        previous_sql="SELECT contract FROM customers",
        guardrail_report=guardrail_report,
        execution_validation_report=None,
        schema_summary={"tables": ["customers"]},
    )

    assert "Must include" in prompt
    assert "Must not do" in prompt
    assert "Expected output columns" in prompt
    assert "Current failure reasons" in prompt


def test_build_sql_repair_prompt_lists_output_columns_only_once() -> None:
    obligations = QueryObligations(required_output_columns=["contract", "churn_rate"])
    guardrail_report = SQLGuardrailReport(status="repairable", attempt=1, parsed=True, parse_error=None)

    prompt = build_sql_repair_prompt(
        original_query="Top churn",
        obligations=obligations,
        previous_sql="SELECT contract FROM customers",
        guardrail_report=guardrail_report,
        execution_validation_report=None,
        schema_summary={"tables": ["customers"]},
    )

    assert prompt.count("Expected output columns:") == 1
    assert "Output columns:" not in prompt
