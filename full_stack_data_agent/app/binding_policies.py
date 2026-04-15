from __future__ import annotations

from dataclasses import dataclass, field

from full_stack_data_agent.app.binding_models import BindingAction


@dataclass(frozen=True)
class BindingPolicy:
    action: str
    source_first: bool = False
    preserve_chart_object: bool = False
    bucket_order: tuple[str, ...] = (
        "explicit_referenced",
        "hinted_source",
        "current_source",
        "recent_source",
        "hinted_chart",
        "current_chart",
        "recent_chart",
        "lineage_adjacent",
        "historical",
    )
    score_weights: dict[str, float] = field(
        default_factory=lambda: {
            "matches_current": 3.0,
            "matches_recent": 2.0,
            "lineage_match": 2.0,
            "preferred_modality": 1.5,
            "current_turn": 0.5,
            "recent_turn": 0.25,
        }
    )


ACTION_POLICIES: dict[str, BindingPolicy] = {
    BindingAction.ANSWER_TEXT: BindingPolicy(action=BindingAction.ANSWER_TEXT, source_first=False),
    BindingAction.SHOW_TABLE: BindingPolicy(action=BindingAction.SHOW_TABLE, source_first=False),
    BindingAction.SHOW_CHART: BindingPolicy(
        action=BindingAction.SHOW_CHART,
        source_first=False,
        preserve_chart_object=True,
        bucket_order=(
            "explicit_referenced",
            "hinted_chart",
            "current_chart",
            "recent_chart",
            "hinted_source",
            "current_source",
            "recent_source",
            "lineage_adjacent",
            "historical",
        ),
    ),
    BindingAction.EXPLAIN: BindingPolicy(action=BindingAction.EXPLAIN, source_first=True),
    BindingAction.SUMMARIZE: BindingPolicy(action=BindingAction.SUMMARIZE, source_first=True),
    BindingAction.EXPORT: BindingPolicy(
        action=BindingAction.EXPORT,
        source_first=False,
        preserve_chart_object=True,
        bucket_order=(
            "explicit_referenced",
            "hinted_chart",
            "current_chart",
            "recent_chart",
            "hinted_source",
            "current_source",
            "recent_source",
            "lineage_adjacent",
            "historical",
        ),
    ),
    BindingAction.FOLLOWUP: BindingPolicy(action=BindingAction.FOLLOWUP, source_first=True),
}
