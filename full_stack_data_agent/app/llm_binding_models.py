from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class LLMBindingCandidateView:
    artifact_id: str
    artifact_type: str
    modality: str
    available_actions: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    parent_artifact_id: str | None = None
    is_current_turn: bool = False
    is_recent_turn: bool = False
    summary: str = ""
    renderable: bool = False
    explainable: bool = False
    exportable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LLMBindingProposal:
    action: str
    selected_artifact_id: str | None
    normalize_to_source: bool = False
    confidence: float | None = None
    reason: str = ""
    secondary_artifact_ids: list[str] = field(default_factory=list)
    reference_interpretation: str = ""
    needs_fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def parse(cls, payload: Any) -> "LLMBindingProposal":
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("LLM binding proposal must be an object")
        return cls(
            action=str(payload.get("action") or ""),
            selected_artifact_id=str(payload["selected_artifact_id"]) if payload.get("selected_artifact_id") else None,
            normalize_to_source=bool(payload.get("normalize_to_source", False)),
            confidence=float(payload["confidence"]) if payload.get("confidence") is not None else None,
            reason=str(payload.get("reason") or ""),
            secondary_artifact_ids=[str(item) for item in (payload.get("secondary_artifact_ids") or [])],
            reference_interpretation=str(payload.get("reference_interpretation") or ""),
            needs_fallback=bool(payload.get("needs_fallback", False)),
        )
