from __future__ import annotations

from dataclasses import replace
from typing import Any

from full_stack_data_agent.app.binding_llm_client import get_binding_llm_client
from full_stack_data_agent.app.binding_models import BindingCandidate, BindingDecision, BindingIntent
from full_stack_data_agent.app.llm_binding_models import LLMBindingCandidateView, LLMBindingProposal
from full_stack_data_agent.app.llm_binding_prompts import (
    binding_system_prompt,
    build_binding_user_prompt,
)
from full_stack_data_agent.config.settings import Settings


def prepare_llm_binding_candidates(
    *,
    action: str,
    binding_intent: BindingIntent,
    deterministic_candidates: list[BindingCandidate],
) -> list[LLMBindingCandidateView]:
    views: list[LLMBindingCandidateView] = []
    for candidate in deterministic_candidates:
        features = candidate.features
        views.append(
            LLMBindingCandidateView(
                artifact_id=candidate.artifact_id,
                artifact_type=candidate.artifact_type,
                modality=str(features.get("modality") or "unknown"),
                available_actions=list(features.get("available_actions") or []),
                lineage=list(features.get("lineage") or []),
                parent_artifact_id=features.get("parent_artifact_id"),
                is_current_turn=bool(features.get("is_current_turn")),
                is_recent_turn=bool(features.get("is_recent_turn")),
                summary=str(features.get("summary") or ""),
                renderable=bool(features.get("renderable")),
                explainable=bool(features.get("explainable")),
                exportable=bool(features.get("exportable")),
            )
        )
    return views


def propose_binding_with_llm(
    *,
    query: str,
    action: str,
    candidate_views: list[LLMBindingCandidateView],
    current_grounded_refs: dict[str, Any] | None = None,
    recent_turn_refs: list[dict[str, Any]] | None = None,
    settings: Settings | None = None,
) -> LLMBindingProposal | None:
    client = get_binding_llm_client(settings)
    if not client.is_available() or not candidate_views:
        return None
    try:
        payload = client.complete_json(
            system_prompt=binding_system_prompt(),
            user_prompt=build_binding_user_prompt(
                query=query,
                action=action,
                candidate_views=candidate_views,
                current_grounded_refs=current_grounded_refs,
                recent_refs=recent_turn_refs,
            ),
        )
        return LLMBindingProposal.parse(payload)
    except Exception:
        return None


def validate_llm_binding_proposal(
    *,
    proposal: LLMBindingProposal | None,
    candidate_map: dict[str, BindingCandidate],
    explicit_artifact_id: str | None = None,
) -> dict[str, Any]:
    if proposal is None:
        return {"accepted_artifact_id": None, "validator_override": "no_proposal", "fallback_used": True, "final_reason": "No valid LLM proposal."}
    if explicit_artifact_id:
        return {
            "accepted_artifact_id": explicit_artifact_id,
            "validator_override": "explicit_artifact_id",
            "fallback_used": False,
            "final_reason": "Explicit artifact id overrides LLM proposal.",
        }
    if proposal.needs_fallback:
        return {"accepted_artifact_id": None, "validator_override": "llm_requested_fallback", "fallback_used": True, "final_reason": proposal.reason}
    if proposal.confidence is not None and proposal.confidence < 0.5:
        return {"accepted_artifact_id": None, "validator_override": "low_confidence", "fallback_used": True, "final_reason": proposal.reason}
    selected = proposal.selected_artifact_id
    if selected not in candidate_map:
        for candidate in candidate_map.values():
            payload = candidate.features.get("payload") or {}
            if payload.get("normalized_from_artifact_id") == selected and proposal.normalize_to_source:
                selected = candidate.artifact_id
                break
    if not selected or selected not in candidate_map:
        return {"accepted_artifact_id": None, "validator_override": "candidate_set_violation", "fallback_used": True, "final_reason": proposal.reason}
    candidate = candidate_map[selected]
    if not candidate.features.get("supports_action", False):
        parent_artifact_id = candidate.features.get("parent_artifact_id")
        if parent_artifact_id and parent_artifact_id in candidate_map and candidate_map[parent_artifact_id].features.get("supports_action", False):
            return {
                "accepted_artifact_id": parent_artifact_id,
                "validator_override": "parent_artifact",
                "fallback_used": False,
                "final_reason": proposal.reason,
            }
        return {"accepted_artifact_id": None, "validator_override": "action_unsupported", "fallback_used": True, "final_reason": proposal.reason}
    return {
        "accepted_artifact_id": selected,
        "validator_override": None,
        "fallback_used": False,
        "final_reason": proposal.reason,
    }


def resolve_binding_with_llm_assist(
    *,
    query: str,
    action: str,
    binding_intent: BindingIntent,
    deterministic_decision: BindingDecision,
    deterministic_candidates: list[BindingCandidate],
    current_grounded_refs: dict[str, Any] | None = None,
    recent_turn_refs: list[dict[str, Any]] | None = None,
    settings: Settings | None = None,
) -> BindingDecision:
    updated_decision = replace(deterministic_decision)
    candidate_map = {candidate.artifact_id: candidate for candidate in deterministic_candidates}
    candidate_views = prepare_llm_binding_candidates(
        action=action,
        binding_intent=binding_intent,
        deterministic_candidates=deterministic_candidates,
    )
    proposal = propose_binding_with_llm(
        query=query,
        action=action,
        candidate_views=candidate_views,
        current_grounded_refs=current_grounded_refs,
        recent_turn_refs=recent_turn_refs,
        settings=settings,
    )
    validation = validate_llm_binding_proposal(
        proposal=proposal,
        candidate_map=candidate_map,
        explicit_artifact_id=binding_intent.explicit_artifact_id,
    )
    if validation["accepted_artifact_id"]:
        updated_decision.selected_artifact_id = str(validation["accepted_artifact_id"])
        updated_decision.decision_mode = "llm_assisted"
        updated_decision.llm_used = True
        updated_decision.llm_confidence = proposal.confidence if proposal is not None else None
        updated_decision.validator_override = validation["validator_override"]
        updated_decision.proposal_reason = validation["final_reason"]
        updated_decision.decision_reason = validation["final_reason"] or updated_decision.decision_reason
        return updated_decision
    updated_decision.decision_mode = "deterministic_fallback"
    updated_decision.llm_used = proposal is not None
    updated_decision.llm_confidence = proposal.confidence if proposal is not None else None
    updated_decision.validator_override = validation["validator_override"]
    updated_decision.proposal_reason = validation["final_reason"]
    return updated_decision
