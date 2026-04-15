from __future__ import annotations

from typing import Any

from full_stack_data_agent.app.binding_policies import BindingPolicy


def classify_bucket(
    *,
    artifact_id: str,
    artifact_type: str,
    action: str,
    policy: BindingPolicy,
    is_current_turn: bool,
    is_recent_turn: bool,
    explicit_artifact_id: str | None,
    hint_targets: set[str],
    reference_kind: str | None,
    matches_hint: bool,
    lineage_match: bool,
) -> str:
    if explicit_artifact_id and artifact_id == explicit_artifact_id:
        return "explicit_referenced"
    source_like = artifact_type in {"raw_df", "filtered_df", "grouped_df", "scalar", "text_answer"}
    if matches_hint:
        if policy.preserve_chart_object and artifact_type == "chart":
            return "hinted_chart"
        if source_like or policy.source_first or reference_kind in {"result", "table"}:
            return "hinted_source"
        if artifact_type == "chart":
            return "hinted_chart"
        return "hinted_source"
    if lineage_match and action in {"explain", "followup", "summarize", "show_chart"}:
        return "lineage_adjacent"
    if source_like and is_current_turn:
        return "current_source"
    if source_like and is_recent_turn:
        return "recent_source"
    if artifact_type == "chart" and is_current_turn:
        return "current_chart"
    if artifact_type == "chart" and is_recent_turn:
        return "recent_chart"
    return "historical"


def rank_bucketed_candidates(candidates: list[Any], policy: BindingPolicy) -> list[Any]:
    bucket_priority = {bucket: index for index, bucket in enumerate(policy.bucket_order)}

    def _sort_key(candidate: Any) -> tuple[Any, ...]:
        features = candidate.features
        return (
            bucket_priority.get(candidate.bucket or "historical", 99),
            -int(bool(features.get("matches_current"))),
            -int(bool(features.get("is_current_turn"))),
            -int(bool(features.get("matches_recent"))),
            -int(bool(features.get("is_recent_turn"))),
            -int(bool(features.get("preferred_modality"))),
            -int(bool(features.get("lineage_match"))),
            -float(candidate.score),
            str(candidate.artifact_id),
        )

    return sorted(candidates, key=_sort_key)
