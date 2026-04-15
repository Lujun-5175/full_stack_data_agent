from __future__ import annotations

import re

from full_stack_data_agent.app.binding_models import BindingAction, BindingIntent

_ACTION_CUES = {
    BindingAction.SHOW_CHART: ("chart", "plot", "graph", "visualize", "图表", "画图", "可视化", "重画", "重新画"),
    BindingAction.SHOW_TABLE: ("table", "tabular", "grid", "表", "表格"),
    BindingAction.EXPLAIN: ("explain", "why", "interpret", "解释", "说明"),
    BindingAction.SUMMARIZE: ("summarize", "summary", "总结", "概括"),
    BindingAction.EXPORT: ("export", "download", "导出"),
}

_MODALITY_HINTS = {
    "chart": ("this chart", "that chart", "chart", "plot", "这个图"),
    "table": ("this table", "that table", "table", "这个表"),
    "result": ("this result", "that result", "这个结果", "它", "it"),
}

_PREFERRED_MODALITY_BY_REFERENCE = {
    "chart": "chart",
    "table": "dataframe",
    "result": "dataframe",
}


def extract_explicit_artifact_id(query: str) -> str | None:
    match = re.search(r"\bartifact[:# ]([A-Za-z0-9:_-]+)\b", query or "")
    return match.group(1) if match else None


def parse_binding_intent(query: str) -> BindingIntent:
    normalized = (query or "").strip()
    lowered = normalized.lower()
    requested_actions: list[str] = []
    preferred_modalities: list[str] = []
    reference_hints: dict[str, bool | str] = {}

    for action, cues in _ACTION_CUES.items():
        if any(cue in lowered or cue in normalized for cue in cues):
            requested_actions.append(action)

    if not requested_actions:
        requested_actions.append(BindingAction.FOLLOWUP)

    for modality, cues in _MODALITY_HINTS.items():
        matched = any(cue in lowered or cue in normalized for cue in cues)
        if matched:
            preferred_modalities.append(_PREFERRED_MODALITY_BY_REFERENCE.get(modality, modality))
            reference_hints[modality] = True
            reference_hints["reference_kind"] = modality

    is_followup_like = bool(
        reference_hints
        or extract_explicit_artifact_id(normalized)
        or re.search(r"\b(it|this|that)\b", lowered)
    )
    return BindingIntent(
        raw_query=normalized,
        requested_actions=list(dict.fromkeys(requested_actions)),
        reference_hints=reference_hints,
        explicit_artifact_id=extract_explicit_artifact_id(normalized),
        preferred_modalities=list(dict.fromkeys(preferred_modalities)),
        is_followup_like=is_followup_like,
    )
