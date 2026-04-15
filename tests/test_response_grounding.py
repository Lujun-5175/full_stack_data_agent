from __future__ import annotations

import pandas as pd

from full_stack_data_agent.app.binders import bind_explain_target
from full_stack_data_agent.app.response_grounding import GroundedResponse
from full_stack_data_agent.app.result_artifacts import ResultArtifact
from full_stack_data_agent.app.result_workspace import ResultWorkspace


def test_grounded_response_primary_bindings_point_to_registered_artifacts() -> None:
    dataframe = pd.DataFrame([{"grade": "A", "score": 90}])
    workspace = ResultWorkspace(conversation_id="conv-1", turn_id="turn-1")
    table = workspace.register_artifact(
        ResultArtifact(
            artifact_id="grouped_df:turn-1",
            artifact_type="grouped_df",
            dataframe=dataframe,
            metadata={"row_count": 1, "columns": ["grade", "score"]},
        )
    )
    text = workspace.register_artifact(
        ResultArtifact(
            artifact_id="text_answer:turn-1",
            artifact_type="text_answer",
            parent_artifact_id=table.artifact_id,
            text_value="A is highest",
        )
    )
    chart = workspace.register_artifact(
        ResultArtifact(
            artifact_id="chart:turn-1",
            artifact_type="chart",
            parent_artifact_id=table.artifact_id,
            chart_spec={"mark": "bar"},
            chart_data=[{"grade": "A", "score": 90}],
        )
    )
    grounded = GroundedResponse(
        primary_text_artifact_id=text.artifact_id,
        primary_table_artifact_id=table.artifact_id,
        primary_chart_artifact_id=chart.artifact_id,
        primary_explain_artifact_id=bind_explain_target(workspace, chart.artifact_id),
        referenced_artifact_ids=[text.artifact_id, table.artifact_id, chart.artifact_id],
        followup_target_artifact_id=table.artifact_id,
        available_actions_by_artifact={artifact.artifact_id: artifact.available_actions for artifact in workspace.artifacts_by_id.values()},
        render_payload={"primary_table_artifact_id": table.artifact_id, "primary_chart_artifact_id": chart.artifact_id},
    )

    payload = grounded.to_dict()

    assert payload["primary_table_artifact_id"] == table.artifact_id
    assert payload["primary_chart_artifact_id"] == chart.artifact_id
    assert payload["primary_explain_artifact_id"] == table.artifact_id
    assert payload["followup_target_artifact_id"] == table.artifact_id


def test_grounded_response_can_carry_binding_trace() -> None:
    grounded = GroundedResponse(
        primary_text_artifact_id="text_answer:turn-1",
        primary_table_artifact_id="grouped_df:turn-1",
        primary_chart_artifact_id="chart:turn-1",
        primary_explain_artifact_id="grouped_df:turn-1",
        followup_target_artifact_id="grouped_df:turn-1",
        render_payload={
            "binding_intent": {"raw_query": "this result"},
            "binding_bundle": {"table_target": "grouped_df:turn-1"},
            "binding_decisions": {"show_table": {"decision_mode": "deterministic"}},
            "decision_mode": "deterministic",
            "llm_used": False,
        },
    )

    payload = grounded.to_dict()

    assert payload["render_payload"]["binding_bundle"]["table_target"] == "grouped_df:turn-1"
    assert payload["render_payload"]["decision_mode"] == "deterministic"
