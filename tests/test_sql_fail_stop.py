from __future__ import annotations

import pandas as pd

from full_stack_data_agent.app.databao_runtime import DatabaoRuntime
from full_stack_data_agent.config.settings import get_settings
from tests.test_databao_runtime import FakeAgent, FakeDomain, FakeThread


class _GuardrailBlockedThread(FakeThread):
    def __init__(self) -> None:
        super().__init__(dataframe=pd.DataFrame([{"category": "分析状态", "value": "SQL guardrail blocked query generation after 3 attempts"}]))

    def text(self) -> str:
        return "SQL guardrail blocked query generation after 3 attempts"

    def meta(self):
        return {
            "messages": [],
            "guardrail_stop_reason": "SQL guardrail blocked query generation after 3 attempts",
            "query_obligations": {"group_by": ["contract"]},
            "sql_retry_history": [{"attempt": 1}, {"attempt": 2}, {"attempt": 3}],
        }


def test_sql_guardrail_blocked_propagates_fail_stop(monkeypatch) -> None:
    runtime = DatabaoRuntime(get_settings())
    thread = _GuardrailBlockedThread()
    monkeypatch.setattr(runtime, "_create_domain", lambda: FakeDomain())
    monkeypatch.setattr(runtime, "_create_agent", lambda domain, llm_config: FakeAgent(thread))

    result, _ = runtime.ask("session-fail-stop", "plot churn rate by contract", uploaded_contexts=[])

    assert result.turn_failure_state == "sql_guardrail_blocked"
    assert result.primary_chart_artifact_id is None
    assert result.completion_validation["status"] == "failed"
    assert result.thread_meta.get("query_obligations") is not None
    assert result.thread_meta.get("sql_retry_history")
