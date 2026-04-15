from __future__ import annotations

import pandas as pd

from full_stack_data_agent.app.binding_engine import rank_binding_candidates, resolve_binding_for_action
from full_stack_data_agent.app.binding_intent import parse_binding_intent
from full_stack_data_agent.app.binding_models import BindingAction, BindingCandidate, BindingIntent
from full_stack_data_agent.app.result_artifacts import ResultArtifact
from full_stack_data_agent.app.result_workspace import ResultWorkspace


def _workspace() -> ResultWorkspace:
    workspace = ResultWorkspace(conversation_id="conv-1", turn_id="turn-1")
    table = workspace.register_artifact(
        ResultArtifact(
            artifact_id="grouped_df:turn-1",
            artifact_type="grouped_df",
            dataframe=pd.DataFrame([{"month": "Jan", "value": 10}]),
            metadata={"columns": ["month", "value"], "row_count": 1},
        )
    )
    workspace.register_artifact(
        ResultArtifact(
            artifact_id="text_answer:turn-1",
            artifact_type="text_answer",
            parent_artifact_id=table.artifact_id,
            text_value="January is highest.",
        )
    )
    workspace.register_artifact(
        ResultArtifact(
            artifact_id="chart:turn-1",
            artifact_type="chart",
            parent_artifact_id=table.artifact_id,
            chart_spec={"mark": "bar"},
            chart_data=[{"month": "Jan", "value": 10}],
            render_payload={"render_kind": "chart_spec", "chart_spec": {"mark": "bar"}},
        )
    )
    return workspace


def test_binding_engine_normalizes_explain_this_chart_to_source_result() -> None:
    decision = resolve_binding_for_action(
        BindingAction.EXPLAIN,
        _workspace(),
        binding_intent=parse_binding_intent("explain this chart"),
        use_llm=False,
    )

    assert decision.selected_artifact_id == "grouped_df:turn-1"
    assert any("normalized chart reference to source artifact" in evidence for evidence in decision.candidates[0].evidence)


def test_binding_engine_keeps_chart_object_for_export() -> None:
    decision = resolve_binding_for_action(
        BindingAction.EXPORT,
        _workspace(),
        binding_intent=parse_binding_intent("export this chart"),
        use_llm=False,
    )

    assert decision.selected_artifact_id == "chart:turn-1"
    assert decision.candidates[0].bucket in {"explicit_referenced", "current_chart", "hinted_chart"}


def test_binding_engine_classifies_hint_buckets_by_action_semantics() -> None:
    explain_decision = resolve_binding_for_action(
        BindingAction.EXPLAIN,
        _workspace(),
        binding_intent=parse_binding_intent("explain this chart"),
        use_llm=False,
    )
    export_decision = resolve_binding_for_action(
        BindingAction.EXPORT,
        _workspace(),
        binding_intent=parse_binding_intent("export this chart"),
        use_llm=False,
    )

    assert explain_decision.selected_artifact_id == "grouped_df:turn-1"
    assert explain_decision.candidates[0].bucket == "hinted_source"
    assert export_decision.selected_artifact_id == "chart:turn-1"
    assert export_decision.candidates[0].bucket == "hinted_chart"


def test_binding_engine_uses_candidate_score_inside_same_bucket() -> None:
    ranked = rank_binding_candidates(
        [
            BindingCandidate(
                artifact_id="grouped_df:low",
                artifact_type="grouped_df",
                action=BindingAction.FOLLOWUP,
                bucket="current_source",
                score=1.0,
                features={"is_current_turn": True},
            ),
            BindingCandidate(
                artifact_id="grouped_df:high",
                artifact_type="grouped_df",
                action=BindingAction.FOLLOWUP,
                bucket="current_source",
                score=3.0,
                features={"is_current_turn": True},
            ),
        ]
    )

    assert [candidate.artifact_id for candidate in ranked] == ["grouped_df:high", "grouped_df:low"]


def test_binding_engine_only_invokes_arbiter_for_ambiguous_top_k(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_arbiter(**kwargs):
        calls.append(kwargs["action"])
        return kwargs["deterministic_decision"], None

    monkeypatch.setattr("full_stack_data_agent.app.binding_engine.arbitrate_top_k_candidates", _fake_arbiter)

    resolve_binding_for_action(
        BindingAction.EXPORT,
        _workspace(),
        binding_intent=parse_binding_intent("export this chart"),
        use_llm=True,
    )

    ambiguous_workspace = ResultWorkspace(conversation_id="conv-amb", turn_id="turn-amb")
    ambiguous_workspace.register_artifact(
        ResultArtifact(
            artifact_id="grouped_df:a",
            artifact_type="grouped_df",
            dataframe=pd.DataFrame([{"region": "NA", "value": 10}]),
            metadata={"columns": ["region", "value"], "row_count": 1},
        )
    )
    ambiguous_workspace.register_artifact(
        ResultArtifact(
            artifact_id="grouped_df:b",
            artifact_type="grouped_df",
            dataframe=pd.DataFrame([{"region": "EU", "value": 11}]),
            metadata={"columns": ["region", "value"], "row_count": 1},
        )
    )

    resolve_binding_for_action(
        BindingAction.FOLLOWUP,
        ambiguous_workspace,
        binding_intent=BindingIntent(raw_query="continue", requested_actions=[BindingAction.FOLLOWUP], is_followup_like=True),
        use_llm=True,
    )

    assert calls == [BindingAction.FOLLOWUP]


def test_binding_engine_falls_back_when_llm_picks_illegal_artifact(monkeypatch) -> None:
    class _FakeClient:
        def is_available(self) -> bool:
            return True

        def complete_json(self, **_kwargs):
            return {
                "action": BindingAction.EXPLAIN,
                "selected_artifact_id": "chart:missing",
                "normalize_to_source": False,
                "confidence": 0.93,
                "reason": "picked a fake artifact",
                "secondary_artifact_ids": [],
                "reference_interpretation": "that result",
                "needs_fallback": False,
            }

    monkeypatch.setattr(
        "full_stack_data_agent.app.binding_arbiter.get_binding_llm_client",
        lambda _settings=None: _FakeClient(),
    )
    monkeypatch.setattr("full_stack_data_agent.app.binding_engine._should_use_llm_arbiter", lambda _candidates, _intent: True)

    decision = resolve_binding_for_action(
        BindingAction.EXPLAIN,
        _workspace(),
        recent_turn_metadatas=[{}],
        binding_intent=parse_binding_intent("刚刚那个结果"),
        use_llm=True,
    )

    assert decision.selected_artifact_id == "grouped_df:turn-1"
    assert decision.decision_mode == "deterministic_fallback"


def test_binding_engine_filters_diagnostic_artifacts_for_chart_and_business_explain() -> None:
    workspace = ResultWorkspace(conversation_id="conv-2", turn_id="turn-2")
    workspace.register_artifact(
        ResultArtifact(
            artifact_id="filtered_df:diag",
            artifact_type="filtered_df",
            artifact_purpose="diagnostic",
            dataframe=pd.DataFrame([{"category": "分析状态", "value": "缺少客户数据"}]),
            metadata={"columns": ["category", "value"], "row_count": 1},
        )
    )
    workspace.register_artifact(
        ResultArtifact(
            artifact_id="text_answer:diag",
            artifact_type="text_answer",
            artifact_purpose="diagnostic",
            text_value="No data available.",
        )
    )

    chart_decision = resolve_binding_for_action(BindingAction.SHOW_CHART, workspace, binding_intent=parse_binding_intent("plot this result"), use_llm=False)
    explain_decision = resolve_binding_for_action(BindingAction.EXPLAIN, workspace, binding_intent=parse_binding_intent("explain this result"), use_llm=False)

    assert chart_decision.selected_artifact_id is None
    assert explain_decision.selected_artifact_id == "text_answer:diag"
