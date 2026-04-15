from __future__ import annotations

import pandas as pd

from full_stack_data_agent.app.binding_engine import resolve_binding_for_action
from full_stack_data_agent.app.binding_intent import parse_binding_intent
from full_stack_data_agent.app.binding_models import BindingAction
from full_stack_data_agent.app.result_artifacts import ResultArtifact
from full_stack_data_agent.app.result_workspace import ResultWorkspace


def _recent_chart_metadata() -> dict[str, object]:
    return {
        "result_workspace": {
            "latest_by_type": {"chart": "chart:recent", "grouped_df": "grouped_df:recent"},
            "artifacts": [
                {
                    "artifact_id": "grouped_df:recent",
                    "artifact_type": "grouped_df",
                    "available_actions": ["inspect", "chart", "explain", "summarize", "export"],
                    "lineage": ["grouped_df:recent"],
                },
                {
                    "artifact_id": "chart:recent",
                    "artifact_type": "chart",
                    "available_actions": ["inspect", "explain", "export"],
                    "lineage": ["grouped_df:recent", "chart:recent"],
                    "parent_artifact_id": "grouped_df:recent",
                    "source_artifact_id": "grouped_df:recent",
                    "render_payload": {"render_kind": "chart_spec"},
                },
            ],
        },
        "grounded_response": {
            "primary_chart_artifact_id": "chart:recent",
            "primary_table_artifact_id": "grouped_df:recent",
            "followup_target_artifact_id": "chart:recent",
        },
    }


def _current_workspace() -> ResultWorkspace:
    workspace = ResultWorkspace(conversation_id="conv-1", turn_id="turn-2")
    workspace.register_artifact(
        ResultArtifact(
            artifact_id="grouped_df:turn-2",
            artifact_type="grouped_df",
            dataframe=pd.DataFrame([{"region": "North America", "sales": 100}]),
            metadata={"columns": ["region", "sales"], "row_count": 1},
        )
    )
    return workspace


def test_recent_chart_reference_normalizes_to_recent_source_for_explain(monkeypatch) -> None:
    class _FakeClient:
        def is_available(self) -> bool:
            return True

        def complete_json(self, **_kwargs):
            return {
                "action": BindingAction.EXPLAIN,
                "selected_artifact_id": "chart:recent",
                "normalize_to_source": True,
                "confidence": 0.9,
                "reason": "this chart points to the recent chart source",
                "secondary_artifact_ids": [],
                "reference_interpretation": "this chart",
                "needs_fallback": False,
            }

    monkeypatch.setattr("full_stack_data_agent.app.binding_arbiter.get_binding_llm_client", lambda _settings=None: _FakeClient())
    decision = resolve_binding_for_action(
        BindingAction.EXPLAIN,
        _current_workspace(),
        recent_turn_metadatas=[_recent_chart_metadata()],
        binding_intent=parse_binding_intent("explain this chart"),
        use_llm=True,
    )

    assert decision.selected_artifact_id == "grouped_df:recent"
    assert decision.decision_mode == "llm_assisted"


def test_current_grouped_result_beats_recent_chart_for_result_summary() -> None:
    decision = resolve_binding_for_action(
        BindingAction.SUMMARIZE,
        _current_workspace(),
        recent_turn_metadatas=[_recent_chart_metadata()],
        binding_intent=parse_binding_intent("summarize this result"),
        use_llm=False,
    )

    assert decision.selected_artifact_id == "grouped_df:turn-2"
