from __future__ import annotations

import pandas as pd

from full_stack_data_agent.app.result_artifacts import ResultArtifact
from full_stack_data_agent.app.result_workspace import ResultWorkspace


def test_workspace_registers_artifacts_and_latest_by_type() -> None:
    dataframe = pd.DataFrame([{"grade": "A", "score": 90}, {"grade": "B", "score": 80}])
    workspace = ResultWorkspace(conversation_id="conv-1", turn_id="turn-1")

    table = workspace.register_artifact(
        ResultArtifact(
            artifact_id="filtered_df:turn-1",
            artifact_type="filtered_df",
            dataframe=dataframe,
            metadata={"row_count": 2, "columns": ["grade", "score"]},
        )
    )
    text = workspace.register_artifact(
        ResultArtifact(
            artifact_id="text_answer:turn-1",
            artifact_type="text_answer",
            parent_artifact_id=table.artifact_id,
            text_value="summary",
        )
    )
    chart = workspace.register_artifact(
        ResultArtifact(
            artifact_id="chart:abc123",
            artifact_type="chart",
            parent_artifact_id=table.artifact_id,
            chart_spec={"mark": "bar"},
            chart_data=[{"grade": "A", "score": 90}],
        )
    )
    scalar = workspace.register_artifact(
        ResultArtifact(
            artifact_id="scalar:turn-1",
            artifact_type="scalar",
            parent_artifact_id=table.artifact_id,
            scalar_value=2,
        )
    )

    assert workspace.root_artifact_id == table.artifact_id
    assert workspace.latest("text_answer") == text
    assert workspace.latest("chart") == chart
    assert workspace.latest("scalar") == scalar
    assert chart.parent_artifact_id == table.artifact_id
    assert chart.lineage == [table.artifact_id, chart.artifact_id]
    assert "chart" in table.available_actions


def test_workspace_to_dict_excludes_heavy_payload_by_default() -> None:
    dataframe = pd.DataFrame([{"value": 1}])
    workspace = ResultWorkspace(conversation_id="conv-1", turn_id="turn-1")
    workspace.register_artifact(
        ResultArtifact(
            artifact_id="filtered_df:turn-1",
            artifact_type="filtered_df",
            dataframe=dataframe,
            metadata={"row_count": 1, "columns": ["value"]},
        )
    )

    payload = workspace.to_dict()

    assert "dataframe" not in payload["artifacts"][0]
    assert "runtime_handles" not in payload["artifacts"][0]
    assert payload["artifacts"][0]["dataframe_preview"] == [{"value": 1}]
