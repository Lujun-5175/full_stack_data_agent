from __future__ import annotations

from pathlib import Path


def test_databao_sql_core_does_not_import_app_layer() -> None:
    root = Path(__file__).resolve().parents[1]
    sql_dir = root / "databao" / "agent" / "sql"
    for path in sql_dir.glob("*.py"):
        content = path.read_text(encoding="utf-8")
        assert "full_stack_data_agent.app" not in content, f"{path} should not import app-layer SQL code"


def test_executor_sql_guardrail_compat_layer_does_not_reintroduce_logic() -> None:
    root = Path(__file__).resolve().parents[1]
    compat_file = root / "databao" / "agent" / "executors" / "sql_guardrails.py"
    content = compat_file.read_text(encoding="utf-8")

    assert "from databao.agent.sql.guardrail import" in content
    assert "full_stack_data_agent.app" not in content
    assert "keyword_to_table_hint" not in content
    assert "_STATUS_KEYWORDS" not in content
    assert "_METRIC_KEYWORDS" not in content
