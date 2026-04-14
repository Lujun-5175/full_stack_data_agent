from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from databao.agent.caches.in_mem_cache import InMemCache
from databao.agent.configs.agent import AgentConfig
from databao.agent.configs.llm import LLMConfig
from databao.agent.core.domain import Domain
from databao.agent.executors.base import GraphExecutor


class _DummyGraphExecutor(GraphExecutor):
    def _compile_graph(
        self,
        llm_config: LLMConfig,
        agent_config: AgentConfig,
        domain: Domain,
        extra_tools: list[Any] | None,
    ) -> CompiledStateGraph[Any]:
        raise NotImplementedError

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


def test_update_message_history_persists_query_ids_mapping() -> None:
    executor = _DummyGraphExecutor()
    cache = InMemCache()
    messages = [
        HumanMessage(content="show grouped counts"),
        ToolMessage(
            content="query_id='7-0'",
            tool_call_id="call-7",
            artifact={"query_id": "7-0", "sql": "select 1", "df": {"a": [1]}},
        ),
    ]

    executor._update_message_history(cache, messages)
    state = cache.get("state", {})

    assert "messages" in state
    assert "query_ids" in state
    assert "7-0" in state["query_ids"]
    assert isinstance(state["query_ids"]["7-0"], ToolMessage)


def test_detects_result_reuse_follow_up_markers() -> None:
    assert _DummyGraphExecutor._looks_like_result_reuse_request("不要重新筛选，基于刚才同一个分组结果改图") is True
    assert _DummyGraphExecutor._looks_like_result_reuse_request("please make a bar chart by contract") is False
