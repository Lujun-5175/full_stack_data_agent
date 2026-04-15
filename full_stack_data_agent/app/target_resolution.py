from __future__ import annotations

from typing import Any

from full_stack_data_agent.app.binding_engine import resolve_binding_for_action
from full_stack_data_agent.app.binding_intent import parse_binding_intent
from full_stack_data_agent.app.binding_models import BindingAction, BindingIntent
from full_stack_data_agent.app.llm_binding_intent import enrich_binding_intent_with_llm
from full_stack_data_agent.app.result_workspace import ResultWorkspace


def _resolve_action(
    action: str,
    message_text: str,
    workspace: ResultWorkspace,
    recent_turn_metadatas: list[dict[str, Any]] | None = None,
) -> str | None:
    intent = parse_binding_intent(message_text or "")
    if action not in intent.requested_actions:
        intent = BindingIntent(
            raw_query=intent.raw_query,
            requested_actions=[action, *intent.requested_actions],
            reference_hints=dict(intent.reference_hints),
            explicit_artifact_id=intent.explicit_artifact_id,
            preferred_modalities=list(intent.preferred_modalities),
            is_followup_like=intent.is_followup_like,
        )
    enriched_intent = enrich_binding_intent_with_llm(intent, use_llm=False)
    decision = resolve_binding_for_action(
        action,
        workspace,
        recent_turn_metadatas=recent_turn_metadatas,
        binding_intent=enriched_intent,
        use_llm=False,
    )
    return decision.selected_artifact_id


def resolve_chart_target(
    message_text: str,
    workspace: ResultWorkspace,
    recent_turn_metadatas: list[dict[str, Any]] | None = None,
) -> str | None:
    return _resolve_action(BindingAction.SHOW_CHART, message_text, workspace, recent_turn_metadatas)


def resolve_explain_target(
    message_text: str,
    workspace: ResultWorkspace,
    recent_turn_metadatas: list[dict[str, Any]] | None = None,
) -> str | None:
    selected = _resolve_action(BindingAction.EXPLAIN, message_text, workspace, recent_turn_metadatas)
    return workspace.preferred_analysis_target(workspace.resolve_artifact(selected)).artifact_id if selected and workspace.preferred_analysis_target(workspace.resolve_artifact(selected)) else selected


def resolve_followup_target(
    message_text: str,
    workspace: ResultWorkspace,
    recent_turn_metadatas: list[dict[str, Any]] | None = None,
    action: str | None = None,
) -> str | None:
    if action:
        action_name = {
            "chart": BindingAction.SHOW_CHART,
            "explain": BindingAction.EXPLAIN,
            "summarize": BindingAction.SUMMARIZE,
            "export": BindingAction.EXPORT,
        }.get(str(action or "").lower(), BindingAction.FOLLOWUP)
    else:
        parsed_intent = parse_binding_intent(message_text or "")
        action_name = next(
            (item for item in parsed_intent.requested_actions if item != BindingAction.FOLLOWUP),
            BindingAction.FOLLOWUP,
        )
    selected = _resolve_action(action_name, message_text, workspace, recent_turn_metadatas)
    object_centric_actions = {BindingAction.SHOW_CHART}
    if action_name in object_centric_actions:
        return selected
    target = workspace.preferred_analysis_target(workspace.resolve_artifact(selected))
    return target.artifact_id if target is not None else selected
