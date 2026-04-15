from __future__ import annotations

from pathlib import Path

from databao.agent.sql.guardrail import SqlGuardrail, SqlSchemaInfo
from databao.agent.sql.obligations import build_query_obligations


def _schema() -> SqlSchemaInfo:
    return SqlSchemaInfo(
        tables={"customers", "contracts"},
        columns_by_table={"customers": {"contract", "customerid", "churn"}, "contracts": {"contract"}},
        types_by_table_column={},
    )


def test_wrapper_uses_unified_obligations_builder() -> None:
    guard = SqlGuardrail()
    query = "按 Contract 分组，统计 churned_customers，并计算 churn_rate，画图并解释。"

    wrapper_obligations = guard.build_obligations(query, _schema())
    direct_obligations = build_query_obligations(
        query,
        {
            "tables": sorted(_schema().tables),
            "columns_by_table": {table: sorted(columns) for table, columns in _schema().columns_by_table.items()},
        },
    )

    assert wrapper_obligations.to_dict() == direct_obligations.to_dict()


def test_app_sql_modules_are_compatibility_reexports() -> None:
    root = Path(__file__).resolve().parents[1]
    targets = [
        root / "full_stack_data_agent" / "app" / "query_obligations.py",
        root / "full_stack_data_agent" / "app" / "sql_ast_utils.py",
        root / "full_stack_data_agent" / "app" / "sql_execution_validation.py",
        root / "full_stack_data_agent" / "app" / "sql_guardrail.py",
        root / "full_stack_data_agent" / "app" / "sql_repair.py",
    ]
    for target in targets:
        content = target.read_text(encoding="utf-8")
        assert "databao.agent.sql" in content
        assert "full_stack_data_agent.app" not in content


def test_telco_query_does_not_require_legacy_city_salary_tokens() -> None:
    obligations = build_query_obligations(
        "按 Contract 分组，计算 churn_rate，并按 churn_rate 降序取前 5 名。",
        {"tables": ["telco_customers"]},
    )

    assert "contract" in obligations.group_by
    assert any(metric.name == "churn_rate" for metric in obligations.derived_metrics)
    assert obligations.limit == 5
