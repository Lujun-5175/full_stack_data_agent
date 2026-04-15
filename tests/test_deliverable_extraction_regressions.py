from __future__ import annotations

from full_stack_data_agent.app.databao_runtime import DatabaoRuntime
from full_stack_data_agent.config.settings import get_settings


def test_deliverable_extraction_filters_punctuation_only_chunks() -> None:
    runtime = DatabaoRuntime(get_settings())

    clauses, debug = runtime._extract_explicit_deliverables(
        "\u8bf7\u56de\u7b54\uff1a\u6a2a\u8f74\uff1a Churn\uff0c\u7eb5\u8f74\uff1a\u5ba2\u6237\u6570\u91cf\uff0c\u6bcf\u4e2a\u67f1\u5b50\u4e0a\u663e\u793a\u4eba\u6570\uff0c\u6700\u540e\u7528 2 \u53e5\u8bdd\u8bf4\u660e\u8c01\u66f4\u591a\u3002"
    )

    assert debug["source"] == "heuristic_local"
    assert all(clause.strip() not in {":", "\uff1a"} for clause in clauses)
    assert any("\u6bcf\u4e2a\u67f1\u5b50\u4e0a\u663e\u793a\u4eba\u6570" in clause for clause in clauses)


def test_deliverable_extraction_handles_chinese_colon_delimited_requirements() -> None:
    runtime = DatabaoRuntime(get_settings())

    clauses, _debug = runtime._extract_explicit_deliverables(
        "\u8981\u6c42\uff1a\u6a2a\u8f74\uff1aChurn \u7eb5\u8f74\uff1a\u5ba2\u6237\u6570\u91cf \u6bcf\u4e2a\u67f1\u5b50\u4e0a\u663e\u793a\u4eba\u6570 \u6700\u540e\u7528\u4e24\u53e5\u8bdd\u603b\u7ed3"
    )

    assert any("Churn" in clause for clause in clauses)
    assert any("\u5ba2\u6237\u6570\u91cf" in clause for clause in clauses)
    assert any("\u6bcf\u4e2a\u67f1\u5b50\u4e0a\u663e\u793a\u4eba\u6570" in clause for clause in clauses)
