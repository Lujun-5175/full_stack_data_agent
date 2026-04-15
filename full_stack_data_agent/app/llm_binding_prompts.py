from __future__ import annotations

import json
from typing import Any

from full_stack_data_agent.app.llm_binding_models import LLMBindingCandidateView


def binding_system_prompt() -> str:
    return (
        "You resolve artifact references for a data analysis assistant. "
        "You must only select from the provided candidate artifact ids. "
        "Do not invent artifact ids. Do not execute data operations. "
        "Return JSON only."
    )


def serialize_candidates(candidate_views: list[LLMBindingCandidateView]) -> list[dict[str, Any]]:
    return [candidate.to_dict() for candidate in candidate_views]


def binding_response_schema_instruction() -> str:
    return (
        "Return an object with keys: "
        "action, selected_artifact_id, normalize_to_source, confidence, reason, secondary_artifact_ids, "
        "reference_interpretation, needs_fallback."
    )


def build_binding_user_prompt(
    *,
    query: str,
    action: str,
    candidate_views: list[LLMBindingCandidateView],
    current_grounded_refs: dict[str, Any] | None = None,
    recent_refs: list[dict[str, Any]] | None = None,
) -> str:
    return "\n".join(
        [
            f"Action: {action}",
            f"Query: {query}",
            f"Current grounded refs: {json.dumps(current_grounded_refs or {}, ensure_ascii=False)}",
            f"Recent refs: {json.dumps(recent_refs or [], ensure_ascii=False)}",
            f"Candidates: {json.dumps(serialize_candidates(candidate_views), ensure_ascii=False)}",
            "Choose only one selected_artifact_id from the candidates above.",
            "Explain how you interpreted references like it/this result/这个图.",
            binding_response_schema_instruction(),
        ]
    )
