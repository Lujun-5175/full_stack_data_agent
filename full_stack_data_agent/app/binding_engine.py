from __future__ import annotations

from typing import Any

from full_stack_data_agent.app.binding_arbiter import arbitrate_top_k_candidates
from full_stack_data_agent.app.binding_filter import is_legal_candidate, modality_from_payload
from full_stack_data_agent.app.binding_models import BindingAction, BindingBundle, BindingCandidate, BindingDecision, BindingIntent
from full_stack_data_agent.app.binding_normalizer import normalize_candidate_payload
from full_stack_data_agent.app.binding_policies import ACTION_POLICIES, BindingPolicy
from full_stack_data_agent.app.binding_ranker import classify_bucket, rank_bucketed_candidates
from full_stack_data_agent.app.binding_validator import validate_candidate_selection
from full_stack_data_agent.app.result_workspace import ResultWorkspace

_BUNDLE_ACTION_MAP = {
    BindingAction.ANSWER_TEXT: "text_target",
    BindingAction.SHOW_TABLE: "table_target",
    BindingAction.SHOW_CHART: "chart_target",
    BindingAction.EXPLAIN: "explain_target",
    BindingAction.SUMMARIZE: "summarize_target",
    BindingAction.EXPORT: "export_target",
    BindingAction.FOLLOWUP: "followup_target",
}

_DEFAULT_ACTIONS = (
    BindingAction.ANSWER_TEXT,
    BindingAction.SHOW_TABLE,
    BindingAction.SHOW_CHART,
    BindingAction.EXPLAIN,
    BindingAction.SUMMARIZE,
    BindingAction.EXPORT,
    BindingAction.FOLLOWUP,
)

_DEFAULT_REFERENCE_PRIORITIES = {
    "chart": ("primary_chart_artifact_id", "followup_target_artifact_id", "primary_table_artifact_id"),
    "table": ("primary_table_artifact_id", "followup_target_artifact_id"),
    "result": ("followup_target_artifact_id", "primary_table_artifact_id", "primary_chart_artifact_id", "primary_text_artifact_id"),
    "text": ("primary_text_artifact_id",),
}


def _should_use_llm_arbiter(candidates: list[BindingCandidate], intent: BindingIntent) -> bool:
    if len(candidates) < 2:
        return False
    top = candidates[0]
    second = candidates[1]
    if top.bucket != second.bucket:
        return False
    if abs(float(top.score) - float(second.score)) > 1.25:
        return False
    return bool(
        intent.is_followup_like
        or top.features.get("matches_hint")
        or second.features.get("matches_hint")
        or top.features.get("lineage_match")
        or second.features.get("lineage_match")
    )


def _metadata_grounded(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    grounded = metadata.get("grounded_response")
    return grounded if isinstance(grounded, dict) else {}


def _metadata_workspace(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    workspace = metadata.get("result_workspace")
    return workspace if isinstance(workspace, dict) else {}


def _current_primary_refs(current_grounded: dict[str, Any] | None) -> set[str]:
    grounded = current_grounded or {}
    return {str(value) for key, value in grounded.items() if key.endswith("_artifact_id") and value}


def _recent_primary_refs(recent_turn_metadatas: list[dict[str, Any]] | None) -> set[str]:
    refs: set[str] = set()
    for metadata in recent_turn_metadatas or []:
        refs.update(_current_primary_refs(_metadata_grounded(metadata) | metadata))
    return refs


def _reference_hint_targets(
    intent: BindingIntent,
    current_grounded: dict[str, Any] | None,
    recent_turn_metadatas: list[dict[str, Any]] | None,
) -> set[str]:
    if intent.explicit_artifact_id:
        return {intent.explicit_artifact_id}
    targets: set[str] = set()
    reference_kind = str((intent.reference_hints or {}).get("reference_kind") or "")
    priorities = _DEFAULT_REFERENCE_PRIORITIES.get(reference_kind, _DEFAULT_REFERENCE_PRIORITIES["result"])
    grounded = current_grounded or {}
    for key in priorities:
        value = grounded.get(key)
        if value:
            targets.add(str(value))
    for metadata in reversed(recent_turn_metadatas or []):
        recent_grounded = _metadata_grounded(metadata)
        for key in priorities:
            value = recent_grounded.get(key) or metadata.get(key)
            if value:
                targets.add(str(value))
    return targets


def _all_candidate_payloads(workspace: ResultWorkspace, recent_turn_metadatas: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    seen: set[str] = set()
    payloads: list[dict[str, Any]] = []
    for artifact in workspace.artifacts_by_id.values():
        seen.add(artifact.artifact_id)
        summary = artifact.summary_dict()
        summary["is_current_turn"] = True
        summary["is_recent_turn"] = False
        payloads.append(summary)
    for metadata in reversed(recent_turn_metadatas or []):
        for artifact in _metadata_workspace(metadata).get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            artifact_id = str(artifact.get("artifact_id") or "")
            if not artifact_id or artifact_id in seen:
                continue
            seen.add(artifact_id)
            summary = dict(artifact)
            summary["is_current_turn"] = False
            summary["is_recent_turn"] = True
            payloads.append(summary)
    return payloads


def _payload_map(payloads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(payload.get("artifact_id") or ""): payload for payload in payloads if str(payload.get("artifact_id") or "")}


def build_binding_candidates(
    action: str,
    workspace: ResultWorkspace,
    recent_turn_metadatas: list[dict[str, Any]] | None = None,
    current_grounded: dict[str, Any] | None = None,
    binding_intent: BindingIntent | None = None,
) -> list[BindingCandidate]:
    intent = binding_intent or BindingIntent(raw_query="", requested_actions=[action])
    policy: BindingPolicy = ACTION_POLICIES.get(action, ACTION_POLICIES[BindingAction.FOLLOWUP])
    hint_targets = _reference_hint_targets(intent, current_grounded, recent_turn_metadatas)
    reference_kind = str((intent.reference_hints or {}).get("reference_kind") or "")
    if not current_grounded and reference_kind in {"result", "table"}:
        for artifact_type in ("grouped_df", "filtered_df", "raw_df"):
            artifact = workspace.latest(artifact_type)
            if artifact is not None:
                hint_targets.add(artifact.artifact_id)
                break
    if not current_grounded and reference_kind == "chart":
        artifact = workspace.latest("chart")
        if artifact is not None:
            hint_targets.add(artifact.artifact_id)
    current_refs = _current_primary_refs(current_grounded)
    recent_refs = _recent_primary_refs(recent_turn_metadatas)
    candidates: list[BindingCandidate] = []
    payloads = _all_candidate_payloads(workspace, recent_turn_metadatas)
    payload_by_id = _payload_map(payloads)

    for payload in payloads:
        artifact_id = str(payload.get("artifact_id") or "")
        if not artifact_id:
            continue
        if intent.explicit_artifact_id and artifact_id != intent.explicit_artifact_id:
            continue
        legal, legal_notes = is_legal_candidate(action, payload)
        if not legal:
            continue
        normalized_payload, normalization_notes = normalize_candidate_payload(workspace, payload, action, policy)
        if normalized_payload.get("artifact_type") == "chart" and action in {"explain", "followup", "summarize"}:
            source_id = str(normalized_payload.get("source_artifact_id") or normalized_payload.get("parent_artifact_id") or "")
            source_payload = payload_by_id.get(source_id)
            if source_payload is not None:
                normalized_payload = dict(source_payload)
                normalized_payload["is_current_turn"] = payload.get("is_current_turn", False)
                normalized_payload["is_recent_turn"] = payload.get("is_recent_turn", False)
                normalized_payload["normalized_from_artifact_id"] = payload.get("artifact_id")
                normalized_payload["normalized_from_artifact_type"] = payload.get("artifact_type")
                normalized_payload["normalization_reason"] = "recent_source_fallback"
                normalization_notes = list(normalization_notes) + ["normalized chart reference to source artifact"]
        normalized_id = str(normalized_payload.get("artifact_id") or artifact_id)
        artifact_type = str(normalized_payload.get("artifact_type") or payload.get("artifact_type") or "")
        matches_hint = normalized_id in hint_targets or artifact_id in hint_targets
        lineage_match = bool(({str(item) for item in (normalized_payload.get("lineage") or [])} & (hint_targets | current_refs | recent_refs)))
        bucket = classify_bucket(
            artifact_id=normalized_id,
            artifact_type=artifact_type,
            action=action,
            policy=policy,
            is_current_turn=bool(normalized_payload.get("is_current_turn")),
            is_recent_turn=bool(normalized_payload.get("is_recent_turn")),
            explicit_artifact_id=intent.explicit_artifact_id,
            hint_targets=hint_targets,
            reference_kind=reference_kind,
            matches_hint=matches_hint,
            lineage_match=lineage_match,
        )
        features = {
            "payload": normalized_payload,
            "is_current_turn": bool(normalized_payload.get("is_current_turn")),
            "is_recent_turn": bool(normalized_payload.get("is_recent_turn")),
            "modality": modality_from_payload(normalized_payload),
            "artifact_type": artifact_type,
            "available_actions": list(normalized_payload.get("available_actions") or []),
            "lineage": list(normalized_payload.get("lineage") or []),
            "parent_artifact_id": normalized_payload.get("parent_artifact_id"),
            "summary": str(normalized_payload.get("name") or normalized_payload.get("text_value") or artifact_type),
            "renderable": bool((normalized_payload.get("render_payload") or {}).get("render_kind") or normalized_payload.get("chart_spec") or normalized_payload.get("chart_data")),
            "explainable": "explain" in {str(item) for item in (normalized_payload.get("available_actions") or [])},
            "exportable": "export" in {str(item) for item in (normalized_payload.get("available_actions") or [])},
            "supports_action": True,
            "matches_current": normalized_id in current_refs,
            "matches_recent": normalized_id in recent_refs,
            "matches_hint": matches_hint,
            "lineage_match": lineage_match,
            "preferred_modality": bool(intent.preferred_modalities and modality_from_payload(normalized_payload) in intent.preferred_modalities),
        }
        evidence = list(legal_notes) + list(normalization_notes) + [f"bucket={bucket}"]
        score = 0.0
        if features["matches_current"]:
            score += policy.score_weights["matches_current"]
        if features["matches_recent"]:
            score += policy.score_weights["matches_recent"]
        if features["lineage_match"]:
            score += policy.score_weights["lineage_match"]
        if features["preferred_modality"]:
            score += policy.score_weights["preferred_modality"]
        if features["is_current_turn"]:
            score += policy.score_weights["current_turn"]
        if features["is_recent_turn"]:
            score += policy.score_weights["recent_turn"]
        candidates.append(
            BindingCandidate(
                artifact_id=normalized_id,
                artifact_type=artifact_type,
                action=action,
                score=score,
                bucket=bucket,
                normalized_artifact_id=normalized_id if normalized_id != artifact_id else None,
                evidence=evidence,
                features=features,
            )
        )
    return candidates


def rank_binding_candidates(candidates: list[BindingCandidate]) -> list[BindingCandidate]:
    action = candidates[0].action if candidates else BindingAction.FOLLOWUP
    policy = ACTION_POLICIES.get(action, ACTION_POLICIES[BindingAction.FOLLOWUP])
    return rank_bucketed_candidates(candidates, policy)


def resolve_binding_for_action(
    action: str,
    workspace: ResultWorkspace,
    *,
    recent_turn_metadatas: list[dict[str, Any]] | None = None,
    current_grounded: dict[str, Any] | None = None,
    binding_intent: BindingIntent | None = None,
    use_llm: bool = True,
    llm_settings: Any | None = None,
) -> BindingDecision:
    intent = binding_intent or BindingIntent(raw_query="", requested_actions=[action])
    ranked = rank_binding_candidates(
        build_binding_candidates(
            action,
            workspace,
            recent_turn_metadatas=recent_turn_metadatas,
            current_grounded=current_grounded,
            binding_intent=intent,
        )
    )
    if not ranked:
        return BindingDecision(action=action, selected_artifact_id=None, candidates=[], decision_reason="No legal binding candidates.", used_recent_context=False, decision_mode="deterministic", llm_used=False)

    top = ranked[0]
    deterministic = BindingDecision(
        action=action,
        selected_artifact_id=top.artifact_id,
        candidates=ranked,
        decision_reason="Deterministic staged resolver selected top bucket candidate.",
        used_recent_context=bool(top.features.get("is_recent_turn")),
        decision_mode="deterministic",
        llm_used=False,
    )
    if use_llm and _should_use_llm_arbiter(ranked, intent):
        deterministic, _proposal = arbitrate_top_k_candidates(
            action=action,
            query=intent.raw_query,
            binding_intent=intent,
            deterministic_decision=deterministic,
            candidates=ranked,
            current_grounded_refs=current_grounded,
            recent_turn_refs=recent_turn_metadatas,
            settings=llm_settings,
        )

    candidate_map = {candidate.artifact_id: candidate for candidate in ranked}
    accepted, validator_notes, validator_override = validate_candidate_selection(
        workspace,
        candidate_map,
        deterministic.selected_artifact_id,
        action=action,
    )
    if accepted is None:
        for fallback in ranked:
            accepted, validator_notes, validator_override = validate_candidate_selection(
                workspace,
                candidate_map,
                fallback.artifact_id,
                action=action,
            )
            if accepted is not None:
                deterministic.selected_artifact_id = accepted
                deterministic.decision_mode = "deterministic_fallback" if deterministic.llm_used else "deterministic"
                break
    else:
        deterministic.selected_artifact_id = accepted
    deterministic.validator_override = validator_override
    deterministic.validator_notes = validator_notes
    if deterministic.arbiter_reason and not deterministic.proposal_reason:
        deterministic.proposal_reason = deterministic.arbiter_reason
    return deterministic


def resolve_binding_bundle(
    workspace: ResultWorkspace,
    *,
    query: str,
    recent_turn_metadatas: list[dict[str, Any]] | None = None,
    current_grounded: dict[str, Any] | None = None,
    binding_intent: BindingIntent | None = None,
    use_llm: bool = True,
    llm_settings: Any | None = None,
) -> BindingBundle:
    intent = binding_intent or BindingIntent(raw_query=query, requested_actions=[], is_followup_like=bool(query.strip()))
    requested = list(intent.requested_actions or [])
    ordered_actions = list(dict.fromkeys(requested + list(_DEFAULT_ACTIONS)))
    decisions_by_action: dict[str, BindingDecision] = {}
    for action in ordered_actions:
        decisions_by_action[action] = resolve_binding_for_action(
            action,
            workspace,
            recent_turn_metadatas=recent_turn_metadatas,
            current_grounded=current_grounded,
            binding_intent=intent,
            use_llm=use_llm,
            llm_settings=llm_settings,
        )

    bundle = BindingBundle(decisions_by_action=decisions_by_action)
    for action, field_name in _BUNDLE_ACTION_MAP.items():
        setattr(bundle, field_name, decisions_by_action[action].selected_artifact_id)
    return bundle
