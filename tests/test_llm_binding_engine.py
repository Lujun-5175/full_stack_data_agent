from __future__ import annotations

import pandas as pd

from full_stack_data_agent.app.binding_engine import build_binding_candidates
from full_stack_data_agent.app.binding_intent import parse_binding_intent
from full_stack_data_agent.app.binding_models import BindingAction, BindingDecision
from full_stack_data_agent.app.llm_binding_engine import prepare_llm_binding_candidates, resolve_binding_with_llm_assist, validate_llm_binding_proposal
from full_stack_data_agent.app.llm_binding_models import LLMBindingProposal
from full_stack_data_agent.app.result_artifacts import ResultArtifact
from full_stack_data_agent.app.result_workspace import ResultWorkspace


def _workspace() -> ResultWorkspace:
    workspace = ResultWorkspace(conversation_id="conv-1", turn_id="turn-1")
    table = workspace.register_artifact(
        ResultArtifact(
            artifact_id="filtered_df:turn-1",
            artifact_type="filtered_df",
            dataframe=pd.DataFrame([{"region": "NA", "value": 10}]),
            metadata={"columns": ["region", "value"], "row_count": 1},
        )
    )
    workspace.register_artifact(
        ResultArtifact(
            artifact_id="chart:turn-1",
            artifact_type="chart",
            parent_artifact_id=table.artifact_id,
            chart_spec={"mark": "bar"},
            chart_data=[{"region": "NA", "value": 10}],
            render_payload={"render_kind": "chart_spec", "chart_spec": {"mark": "bar"}},
        )
    )
    return workspace


def test_llm_binding_validator_rejects_non_candidate_id() -> None:
    candidates = build_binding_candidates(BindingAction.FOLLOWUP, _workspace(), binding_intent=parse_binding_intent("it"))
    candidate_map = {candidate.artifact_id: candidate for candidate in candidates}

    validation = validate_llm_binding_proposal(
        proposal=LLMBindingProposal(action=BindingAction.FOLLOWUP, selected_artifact_id="artifact:missing", confidence=0.9, reason="invalid"),
        candidate_map=candidate_map,
    )

    assert validation["accepted_artifact_id"] is None
    assert validation["validator_override"] == "candidate_set_violation"
    assert validation["fallback_used"] is True


def test_llm_binding_validator_can_accept_normalized_source_selection() -> None:
    candidates = build_binding_candidates(BindingAction.EXPLAIN, _workspace(), binding_intent=parse_binding_intent("this chart"))
    candidate_map = {candidate.artifact_id: candidate for candidate in candidates}

    validation = validate_llm_binding_proposal(
        proposal=LLMBindingProposal(
            action=BindingAction.EXPLAIN,
            selected_artifact_id="chart:turn-1",
            normalize_to_source=True,
            confidence=0.9,
            reason="explain should use the chart source",
        ),
        candidate_map=candidate_map,
    )

    assert validation["accepted_artifact_id"] == "filtered_df:turn-1"
    assert validation["fallback_used"] is False


def test_llm_binding_candidates_stay_lightweight() -> None:
    candidates = build_binding_candidates(BindingAction.FOLLOWUP, _workspace(), binding_intent=parse_binding_intent("it"))
    views = prepare_llm_binding_candidates(action=BindingAction.FOLLOWUP, binding_intent=parse_binding_intent("it"), deterministic_candidates=candidates)

    assert views
    payload = views[0].to_dict()
    assert "dataframe" not in payload
    assert "runtime_handles" not in payload


def test_llm_binding_engine_falls_back_on_low_confidence(monkeypatch) -> None:
    class _FakeClient:
        def is_available(self) -> bool:
            return True

        def complete_json(self, **_kwargs):
            return {
                "action": BindingAction.FOLLOWUP,
                "selected_artifact_id": "filtered_df:turn-1",
                "normalize_to_source": False,
                "confidence": 0.2,
                "reason": "uncertain",
                "secondary_artifact_ids": [],
                "reference_interpretation": "it",
                "needs_fallback": False,
            }

    candidates = build_binding_candidates(BindingAction.FOLLOWUP, _workspace(), binding_intent=parse_binding_intent("it"))
    deterministic = BindingDecision(action=BindingAction.FOLLOWUP, selected_artifact_id=candidates[0].artifact_id, candidates=candidates, decision_reason="baseline")
    monkeypatch.setattr("full_stack_data_agent.app.llm_binding_engine.get_binding_llm_client", lambda _settings=None: _FakeClient())

    decision = resolve_binding_with_llm_assist(
        query="it",
        action=BindingAction.FOLLOWUP,
        binding_intent=parse_binding_intent("it"),
        deterministic_decision=deterministic,
        deterministic_candidates=candidates,
    )

    assert decision.decision_mode == "deterministic_fallback"
    assert decision.validator_override == "low_confidence"
