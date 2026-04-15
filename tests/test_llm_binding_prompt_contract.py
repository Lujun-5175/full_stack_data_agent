from __future__ import annotations

from full_stack_data_agent.app.llm_binding_models import LLMBindingCandidateView, LLMBindingProposal
from full_stack_data_agent.app.llm_binding_prompts import build_binding_user_prompt, serialize_candidates


def test_llm_binding_prompt_serialization_is_lightweight() -> None:
    candidate = LLMBindingCandidateView(
        artifact_id="chart:turn-1",
        artifact_type="chart",
        modality="chart",
        available_actions=["explain", "export"],
        lineage=["grouped_df:turn-1", "chart:turn-1"],
        parent_artifact_id="grouped_df:turn-1",
        is_current_turn=True,
        summary="Current chart artifact",
        renderable=True,
        explainable=True,
        exportable=True,
    )

    serialized = serialize_candidates([candidate])[0]
    prompt = build_binding_user_prompt(
        query="this chart",
        action="explain",
        candidate_views=[candidate],
        current_grounded_refs={"primary_chart_artifact_id": "chart:turn-1"},
        recent_refs=[],
    )

    assert "runtime_handles" not in serialized
    assert "dataframe" not in serialized
    assert "only one selected_artifact_id from the candidates above" in prompt
    assert "Do not invent artifact ids" not in serialized


def test_llm_binding_proposal_parser_rejects_invalid_payload() -> None:
    try:
        LLMBindingProposal.parse("[]")
    except ValueError as exc:
        assert "must be an object" in str(exc)
    else:
        raise AssertionError("Expected invalid proposal payload to raise ValueError")
