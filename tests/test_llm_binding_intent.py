from __future__ import annotations

from full_stack_data_agent.app.binding_intent import parse_binding_intent
from full_stack_data_agent.app.binding_models import BindingAction
from full_stack_data_agent.app.llm_binding_intent import enrich_binding_intent_with_llm


def test_enrich_binding_intent_with_llm_returns_original_when_disabled() -> None:
    intent = parse_binding_intent("explain this result")

    enriched = enrich_binding_intent_with_llm(intent, use_llm=False)

    assert enriched is intent


def test_enrich_binding_intent_with_llm_returns_original_when_client_unavailable(monkeypatch) -> None:
    class _FakeClient:
        def is_available(self) -> bool:
            return False

    intent = parse_binding_intent("show this result")
    monkeypatch.setattr(
        "full_stack_data_agent.app.llm_binding_intent.get_binding_llm_client",
        lambda _settings=None: _FakeClient(),
    )

    enriched = enrich_binding_intent_with_llm(intent, use_llm=True)

    assert enriched is intent


def test_enrich_binding_intent_with_llm_merges_only_whitelisted_fields(monkeypatch) -> None:
    class _FakeClient:
        def is_available(self) -> bool:
            return True

        def complete_json(self, **_kwargs):
            return {
                "requested_actions": [BindingAction.EXPLAIN, BindingAction.SHOW_CHART, "unknown"],
                "preferred_modalities": ["dataframe", "chart", "bogus"],
                "explicit_artifact_id": "artifact:123",
                "reference_hints": {"reference_kind": "chart", "llm_inferred_reference": "chart"},
                "is_followup_like": True,
                "decision_mode": "overwrite_everything",
            }

    intent = parse_binding_intent("explain this result")
    monkeypatch.setattr(
        "full_stack_data_agent.app.llm_binding_intent.get_binding_llm_client",
        lambda _settings=None: _FakeClient(),
    )

    enriched = enrich_binding_intent_with_llm(intent, use_llm=True)

    assert enriched is not intent
    assert enriched.requested_actions == [BindingAction.EXPLAIN, BindingAction.SHOW_CHART]
    assert enriched.preferred_modalities == ["dataframe", "chart"]
    assert enriched.explicit_artifact_id == "artifact:123"
    assert enriched.reference_hints["reference_kind"] == "chart"
    assert enriched.reference_hints["result"] is True
    assert enriched.is_followup_like is True


def test_enrich_binding_intent_with_llm_falls_back_on_invalid_payload(monkeypatch) -> None:
    class _FakeClient:
        def is_available(self) -> bool:
            return True

        def complete_json(self, **_kwargs):
            return {"requested_actions": "not-a-list", "reference_hints": ["bad"]}

    intent = parse_binding_intent("show this result")
    monkeypatch.setattr(
        "full_stack_data_agent.app.llm_binding_intent.get_binding_llm_client",
        lambda _settings=None: _FakeClient(),
    )

    enriched = enrich_binding_intent_with_llm(intent, use_llm=True)

    assert enriched is intent


def test_enrich_binding_intent_with_llm_falls_back_on_exception(monkeypatch) -> None:
    class _FakeClient:
        def is_available(self) -> bool:
            return True

        def complete_json(self, **_kwargs):
            raise RuntimeError("boom")

    intent = parse_binding_intent("show this result")
    monkeypatch.setattr(
        "full_stack_data_agent.app.llm_binding_intent.get_binding_llm_client",
        lambda _settings=None: _FakeClient(),
    )

    enriched = enrich_binding_intent_with_llm(intent, use_llm=True)

    assert enriched is intent
