from __future__ import annotations

import duckdb

from databao.agent.executors.lighthouse.graph import ExecuteSubmit


class _FakeModelConfig:
    name = "local-model"

    def new_chat_model(self):
        return object()


class _FakeAgentConfig:
    parallel_tool_calls = False


def _prepare_graph(monkeypatch, llm_call):
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE orders (
            order_id VARCHAR,
            customer_id VARCHAR,
            order_status VARCHAR
        )
        """
    )
    graph = ExecuteSubmit(connection)
    monkeypatch.setattr(ExecuteSubmit, "make_tools", lambda self, domain, extra_tools=None: [])
    monkeypatch.setattr("databao.agent.executors.lighthouse.graph.model_bind_tools", lambda *args, **kwargs: object())
    monkeypatch.setattr(ExecuteSubmit, "_make_query_frame_llm_call", lambda self, model_config: llm_call)
    graph.compile(_FakeModelConfig(), _FakeAgentConfig(), object())
    schema = graph._guardrail.schema_from_connection(connection)
    return graph, schema


def test_lighthouse_compile_wires_llm_query_frame_into_guardrail(monkeypatch) -> None:
    query = "top 3 accounts with highest number of fulfilled orders"
    sql = """
    SELECT customer_id, COUNT(order_id) AS count_orders
    FROM orders
    GROUP BY customer_id
    ORDER BY count_orders DESC
    LIMIT 3
    """
    llm_payload = """
    {
      "intent": "aggregate",
      "entities": ["customer", "orders"],
      "semantic_filters": [{"semantic_role": "status", "value": "delivered", "confidence": 0.91}],
      "semantic_groups": [{"target": "customer", "confidence": 0.95}],
      "semantic_metrics": [{"kind": "count", "target": "orders", "name": "count_orders", "confidence": 0.95}],
      "ranking_intent": {"direction": "desc", "target": "count_orders", "confidence": 0.9},
      "limit": 3,
      "deliverables": ["grouped_table"],
      "ambiguity": [],
      "confidence": 0.89
    }
    """

    graph, schema = _prepare_graph(monkeypatch, lambda _prompt: llm_payload)
    report = graph._guardrail.validate_before_execution(query=query, sql=sql, schema=schema)

    assert graph._guardrail._llm_call is not None
    assert report.blocked is True
    assert any(issue.code == "required_filter_missing" for issue in report.issues)
    assert any(item["column"] == "order_status" and str(item["value"]).lower() == "delivered" for item in report.obligations["filters"])


def test_lighthouse_query_frame_llm_fallback_keeps_baseline_when_payload_is_invalid(monkeypatch) -> None:
    query = "top 3 accounts with highest number of fulfilled orders"
    sql = """
    SELECT customer_id, COUNT(order_id) AS count_orders
    FROM orders
    GROUP BY customer_id
    ORDER BY count_orders DESC
    LIMIT 3
    """
    graph, schema = _prepare_graph(monkeypatch, lambda _prompt: "{not valid json")
    report = graph._guardrail.validate_before_execution(query=query, sql=sql, schema=schema)

    assert report.blocked is True
    assert report.obligations["filters"] == []
    assert not any(issue.code == "required_filter_missing" for issue in report.issues)
