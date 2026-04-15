from __future__ import annotations

import pandas as pd

from full_stack_data_agent.app.result_artifacts import ResultArtifact
from full_stack_data_agent.app.result_workspace import ResultWorkspace
from full_stack_data_agent.app.target_resolution import resolve_chart_target, resolve_explain_target, resolve_followup_target


def _workspace() -> ResultWorkspace:
    workspace = ResultWorkspace(conversation_id="conv-1", turn_id="turn-1")
    dataframe = pd.DataFrame([{"grade": "A", "score": 90}])
    table = workspace.register_artifact(
        ResultArtifact(
            artifact_id="filtered_df:turn-1",
            artifact_type="filtered_df",
            dataframe=dataframe,
            metadata={"row_count": 1, "columns": ["grade", "score"]},
        )
    )
    workspace.register_artifact(
        ResultArtifact(
            artifact_id="text_answer:turn-1",
            artifact_type="text_answer",
            parent_artifact_id=table.artifact_id,
            text_value="summary",
        )
    )
    workspace.register_artifact(
        ResultArtifact(
            artifact_id="chart:turn-1",
            artifact_type="chart",
            parent_artifact_id=table.artifact_id,
            chart_spec={"mark": "bar"},
            chart_data=[{"grade": "A", "score": 90}],
            render_payload={"render_kind": "chart_spec"},
        )
    )
    return workspace


def test_target_resolution_handles_followup_pronouns_and_staged_normalization() -> None:
    workspace = _workspace()

    assert resolve_followup_target("explain this", workspace, action="explain") == "filtered_df:turn-1"
    assert resolve_followup_target("这个图", workspace) == "filtered_df:turn-1"
    assert resolve_followup_target("这个结果", workspace) == "filtered_df:turn-1"
    assert resolve_chart_target("重新画一下", workspace) == "chart:turn-1"
    assert resolve_explain_target("explain this", workspace) == "filtered_df:turn-1"


def test_target_resolution_does_not_overmatch_plain_this() -> None:
    workspace = _workspace()

    assert resolve_followup_target("this", workspace) == "filtered_df:turn-1"
    assert resolve_followup_target("this result", workspace) == "filtered_df:turn-1"
    assert resolve_followup_target("这个表", workspace) == "filtered_df:turn-1"
    assert resolve_followup_target("它", workspace) == "filtered_df:turn-1"
