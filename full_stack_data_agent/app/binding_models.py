from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class BindingAction:
    ANSWER_TEXT = "answer_text"
    SHOW_TABLE = "show_table"
    SHOW_CHART = "show_chart"
    EXPLAIN = "explain"
    SUMMARIZE = "summarize"
    EXPORT = "export"
    FOLLOWUP = "followup"

    @classmethod
    def all(cls) -> list[str]:
        return [
            cls.ANSWER_TEXT,
            cls.SHOW_TABLE,
            cls.SHOW_CHART,
            cls.EXPLAIN,
            cls.SUMMARIZE,
            cls.EXPORT,
            cls.FOLLOWUP,
        ]


@dataclass
class BindingIntent:
    raw_query: str
    requested_actions: list[str] = field(default_factory=list)
    reference_hints: dict[str, Any] = field(default_factory=dict)
    explicit_artifact_id: str | None = None
    preferred_modalities: list[str] = field(default_factory=list)
    is_followup_like: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BindingCandidate:
    artifact_id: str
    artifact_type: str
    action: str
    score: float = 0.0
    bucket: str = ""
    normalized_artifact_id: str | None = None
    evidence: list[str] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BindingDecision:
    action: str
    selected_artifact_id: str | None
    candidates: list[BindingCandidate] = field(default_factory=list)
    decision_reason: str = ""
    used_recent_context: bool = False
    decision_mode: str = "deterministic"
    llm_used: bool = False
    llm_confidence: float | None = None
    validator_override: str | None = None
    proposal_reason: str | None = None
    validator_notes: list[str] = field(default_factory=list)
    arbiter_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "selected_artifact_id": self.selected_artifact_id,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "decision_reason": self.decision_reason,
            "used_recent_context": self.used_recent_context,
            "decision_mode": self.decision_mode,
            "llm_used": self.llm_used,
            "llm_confidence": self.llm_confidence,
            "validator_override": self.validator_override,
            "proposal_reason": self.proposal_reason,
            "validator_notes": list(self.validator_notes),
            "arbiter_reason": self.arbiter_reason,
        }


@dataclass
class BindingBundle:
    text_target: str | None = None
    table_target: str | None = None
    chart_target: str | None = None
    explain_target: str | None = None
    summarize_target: str | None = None
    export_target: str | None = None
    followup_target: str | None = None
    decisions_by_action: dict[str, BindingDecision] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text_target": self.text_target,
            "table_target": self.table_target,
            "chart_target": self.chart_target,
            "explain_target": self.explain_target,
            "summarize_target": self.summarize_target,
            "export_target": self.export_target,
            "followup_target": self.followup_target,
            "decisions_by_action": {
                action: decision.to_dict() for action, decision in self.decisions_by_action.items()
            },
        }
