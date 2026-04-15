from __future__ import annotations

from full_stack_data_agent.app.binding_engine import resolve_binding_for_action
from full_stack_data_agent.app.binding_models import BindingAction, BindingIntent
from full_stack_data_agent.app.result_workspace import ResultWorkspace


def bind_chart_target(workspace: ResultWorkspace, preferred_artifact_id: str | None = None) -> str | None:
    decision = resolve_binding_for_action(
        BindingAction.SHOW_CHART,
        workspace,
        binding_intent=BindingIntent(
            raw_query=f"artifact:{preferred_artifact_id}" if preferred_artifact_id else "",
            requested_actions=[BindingAction.SHOW_CHART],
            explicit_artifact_id=preferred_artifact_id,
            preferred_modalities=["dataframe"],
        ),
        use_llm=False,
    )
    return decision.selected_artifact_id


def bind_explain_target(workspace: ResultWorkspace, preferred_artifact_id: str | None = None) -> str | None:
    explicit_artifact_id = preferred_artifact_id
    preferred = workspace.resolve_artifact(preferred_artifact_id)
    if preferred is not None and preferred.is_chart_like():
        parent = workspace.resolve_artifact(preferred.parent_artifact_id)
        if parent is not None:
            explicit_artifact_id = parent.artifact_id
    decision = resolve_binding_for_action(
        BindingAction.EXPLAIN,
        workspace,
        binding_intent=BindingIntent(
            raw_query=f"artifact:{explicit_artifact_id}" if explicit_artifact_id else "",
            requested_actions=[BindingAction.EXPLAIN],
            explicit_artifact_id=explicit_artifact_id,
        ),
        use_llm=False,
    )
    return decision.selected_artifact_id
