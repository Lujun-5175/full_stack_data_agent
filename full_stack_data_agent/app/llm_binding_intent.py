from __future__ import annotations

import json
from dataclasses import replace
import re
from typing import Any

from full_stack_data_agent.app.binding_intent import parse_binding_intent
from full_stack_data_agent.app.binding_llm_client import get_binding_llm_client
from full_stack_data_agent.app.binding_models import BindingAction, BindingIntent
from full_stack_data_agent.app.llm_binding_prompts import binding_system_prompt
from full_stack_data_agent.config.settings import Settings


_REFERENCE_PATTERN = re.compile(
    r"\b(it|this|that|these|those|the result|the table|the chart|the plot)\b|这个|那个|它|这张|那张|刚才|上面|这份",
    flags=re.IGNORECASE,
)

_MODALITIES_BY_REFERENCE = {
    "chart": ["chart"],
    "table": ["dataframe"],
    "scalar": ["scalar"],
    "text": ["text"],
}
_KNOWN_MODALITIES = {"chart", "dataframe", "scalar", "text"}


def _normalize_actions(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    known = set(BindingAction.all())
    return [value for value in dict.fromkeys(str(item).strip() for item in values if str(item).strip() in known)]


def _normalize_modalities(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [value for value in dict.fromkeys(str(item).strip() for item in values if str(item).strip() in _KNOWN_MODALITIES)]


def _normalize_reference_hints(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(key, str)}


def _normalize_explicit_artifact_id(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _normalize_followup_flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def enrich_binding_intent_with_llm(
    intent: BindingIntent,
    *,
    settings: Settings | None = None,
    use_llm: bool = True,
) -> BindingIntent:
    if not use_llm:
        return intent
    raw_query = str(intent.raw_query or "")
    if not raw_query:
        return intent
    client = get_binding_llm_client(settings)
    if not client.is_available():
        return intent
    if not _REFERENCE_PATTERN.search(raw_query):
        return intent
    structured_intent = {
        "raw_query": raw_query,
        "requested_actions": list(intent.requested_actions or []),
        "preferred_modalities": list(intent.preferred_modalities or []),
        "explicit_artifact_id": intent.explicit_artifact_id,
        "reference_hints": dict(intent.reference_hints or {}),
        "is_followup_like": bool(intent.is_followup_like),
    }
    user_prompt = (
        "Enhance a deterministic binding intent parser conservatively.\n"
        f"User query: {raw_query}\n"
        f"Current intent JSON: {json.dumps(structured_intent, ensure_ascii=False)}\n"
        "Only fill or extend these keys when clearly supported by the query: "
        "requested_actions, preferred_modalities, explicit_artifact_id, reference_hints, is_followup_like.\n"
        "Do not remove deterministic values. Do not invent unknown actions or modalities.\n"
        'Return JSON only with those keys, for example: {"requested_actions":[],"preferred_modalities":[],"explicit_artifact_id":null,"reference_hints":{},"is_followup_like":false}'
    )
    try:
        payload = client.complete_json(
            system_prompt=binding_system_prompt(),
            user_prompt=user_prompt,
        )
    except Exception:
        return intent
    if not isinstance(payload, dict):
        return intent
    llm_actions = _normalize_actions(payload.get("requested_actions"))
    llm_modalities = _normalize_modalities(payload.get("preferred_modalities"))
    llm_explicit_artifact_id = _normalize_explicit_artifact_id(payload.get("explicit_artifact_id"))
    llm_reference_hints = _normalize_reference_hints(payload.get("reference_hints"))
    llm_is_followup_like = _normalize_followup_flag(payload.get("is_followup_like"))
    if not any(
        (
            llm_actions,
            llm_modalities,
            llm_explicit_artifact_id,
            llm_reference_hints,
            llm_is_followup_like is True,
        )
    ):
        return intent

    merged_actions = list(dict.fromkeys([*list(intent.requested_actions or []), *llm_actions]))
    merged_modalities = list(dict.fromkeys([*list(intent.preferred_modalities or []), *llm_modalities]))
    merged_reference_hints = dict(intent.reference_hints or {})
    if llm_reference_hints:
        merged_reference_hints.update(llm_reference_hints)

    return replace(
        intent,
        requested_actions=merged_actions,
        preferred_modalities=merged_modalities,
        explicit_artifact_id=intent.explicit_artifact_id or llm_explicit_artifact_id,
        reference_hints=merged_reference_hints,
        is_followup_like=bool(intent.is_followup_like or llm_is_followup_like),
    )


def parse_and_enrich_binding_intent(
    query: str,
    *,
    settings: Settings | None = None,
    use_llm: bool = True,
) -> BindingIntent:
    return enrich_binding_intent_with_llm(parse_binding_intent(query), settings=settings, use_llm=use_llm)
