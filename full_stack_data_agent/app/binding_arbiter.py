from __future__ import annotations

from dataclasses import replace
from typing import Any

from full_stack_data_agent.app.binding_llm_client import get_binding_llm_client
from full_stack_data_agent.app.binding_models import BindingCandidate, BindingDecision, BindingIntent
from full_stack_data_agent.app.llm_binding_engine import prepare_llm_binding_candidates
from full_stack_data_agent.app.llm_binding_models import LLMBindingProposal
from full_stack_data_agent.app.llm_binding_prompts import binding_system_prompt, build_binding_user_prompt


def arbitrate_top_k_candidates(
    *,
    action: str,
    query: str,
    binding_intent: BindingIntent,
    deterministic_decision: BindingDecision,
    candidates: list[BindingCandidate],
    current_grounded_refs: dict[str, Any] | None,
    recent_turn_refs: list[dict[str, Any]] | None,
    settings: Any | None = None,
    top_k: int = 3,
) -> tuple[BindingDecision, LLMBindingProposal | None]:
    trimmed = candidates[:top_k]
    client = get_binding_llm_client(settings)
    if not client.is_available() or len(trimmed) <= 1:
        return deterministic_decision, None
    try:
        payload = client.complete_json(
            system_prompt=binding_system_prompt(),
            user_prompt=build_binding_user_prompt(
                query=query,
                action=action,
                candidate_views=prepare_llm_binding_candidates(
                    action=action,
                    binding_intent=binding_intent,
                    deterministic_candidates=trimmed,
                ),
                current_grounded_refs=current_grounded_refs,
                recent_refs=recent_turn_refs,
            ),
        )
        proposal = LLMBindingProposal.parse(payload)
    except Exception:
        return deterministic_decision, None

    decision = replace(deterministic_decision)
    decision.llm_used = True
    decision.llm_confidence = proposal.confidence
    decision.arbiter_reason = proposal.reason
    if proposal.needs_fallback:
        decision.decision_mode = "deterministic_fallback"
        decision.proposal_reason = proposal.reason
        return decision, proposal
    if proposal.selected_artifact_id:
        selected_artifact_id = proposal.selected_artifact_id
        if selected_artifact_id not in {candidate.artifact_id for candidate in trimmed} and proposal.normalize_to_source:
            for candidate in trimmed:
                payload = candidate.features.get("payload") or {}
                if payload.get("normalized_from_artifact_id") == selected_artifact_id:
                    selected_artifact_id = candidate.artifact_id
                    break
        decision.selected_artifact_id = selected_artifact_id
        decision.decision_mode = "llm_assisted"
        decision.proposal_reason = proposal.reason
        decision.decision_reason = proposal.reason or decision.decision_reason
    return decision, proposal
