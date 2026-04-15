from __future__ import annotations

import io
import json
import zipfile

import matplotlib
import pandas as pd
matplotlib.use("Agg", force=True)
from matplotlib import pyplot as plt

from full_stack_data_agent.app.conversation_export import (
    build_conversation_export_bundle,
    make_json_safe,
)
from full_stack_data_agent.context.models import ConversationMessage, ConversationState, ConversationTurn


class _OpaqueObject:
    def __repr__(self) -> str:  # pragma: no cover - repr is intentionally simple
        return "<opaque>"


def _build_state() -> ConversationState:
    figure, _axis = plt.subplots()
    state = ConversationState(conversation_id="conv-export-test")
    state.turns.append(
        ConversationTurn(
            user_message=ConversationMessage(role="user", content="Show me the trend"),
            assistant_message=ConversationMessage(role="assistant", content="Here is the chart and table."),
            metadata={
                "provider": "ollama",
                "model": "llama3",
                "result_workspace": {
                    "conversation_id": "conv-export-test",
                    "turn_id": "turn-2",
                    "root_artifact_id": "table_1",
                    "latest_by_type": {"filtered_df": "table_1", "chart": "chart_1"},
                    "artifacts": [
                        {
                            "artifact_id": "table_1",
                            "artifact_type": "filtered_df",
                            "dataframe_preview": [{"month": "Jan", "value": 12}],
                            "row_count": 1,
                            "columns": ["month", "value"],
                        },
                        {
                            "artifact_id": "chart_1",
                            "artifact_type": "chart",
                            "chart_plan": {"kind": "barplot", "x": "month", "y": "value"},
                            "chart_spec": {"x": "month", "y": "value"},
                            "chart_data": [{"month": "Jan", "value": 12}],
                            "chart_meta": {"render_kind": "barplot"},
                        },
                    ],
                },
                "grounded_response": {
                    "primary_text_artifact_id": "text_1",
                    "primary_table_artifact_id": "table_1",
                    "primary_chart_artifact_id": "chart_1",
                    "primary_explain_artifact_id": "table_1",
                    "referenced_artifact_ids": ["table_1", "chart_1"],
                    "followup_target_artifact_id": "table_1",
                    "available_actions_by_artifact": {"table_1": ["show_table", "explain"]},
                    "render_payload": {"binding_decisions": {"show_chart": {"selected_artifact_id": "chart_1"}}},
                },
                "binding_intent": {"raw_query": "show trend"},
                "binding_bundle": {"chart_target": "chart_1", "table_target": "table_1"},
                "binding_decisions": {"show_chart": {"decision_mode": "llm_assisted"}},
                "decision_mode": "llm_assisted",
                "llm_used": True,
                "completion_validation": {
                    "status": "warning",
                    "requested_deliverables": ["table", "chart"],
                    "satisfied_deliverables": ["table"],
                    "missing_deliverables": ["chart"],
                    "validation_notes": ["chart generation failed"],
                },
                "normalization_reports": [{"warnings": ["coerced month to string"]}],
                "registered_tables": [{"name": "sales", "source_file": "sales.csv", "shape": [1, 2]}],
                "plot_backend": "seaborn",
                "plot_kind": "barplot",
                "plot_plan": {"kind": "barplot", "x": "month", "y": "value"},
                "plot_spec": {"x": "month", "y": "value"},
                "plot_data": [{"month": "Jan", "value": 12}],
                "plot_meta": {"render_failed": True},
                "plot_error": "render_failed: duplicated column label",
                "chart_debug": {"planner_status": "render_failed", "render_error": "duplicated column label"},
                "query_obligations": {"group_by": ["contract"], "required_output_columns": ["contract", "churn_rate"]},
                "parsed_sql_summary": {"tables": ["customers"], "group_by_columns": ["contract"]},
                "sql_guardrail_report": {"status": "repairable", "missing_obligations": ["derived_metric:churn_rate"]},
                "execution_validation_report": {"status": "repairable", "missing_columns": ["churn_rate"]},
                "sql_retry_history": [{"attempt": 1, "report": {"status": "repairable"}}],
                "final_failure_reason": "SQL obligations unmet after retries",
                "debug_blob": {
                    "figure": figure,
                    "opaque": _OpaqueObject(),
                    "raw_df": pd.DataFrame({"value": [1, 2], "value_dup": [3, 4]}),
                    "payload": b"hello world",
                },
            },
            debug_detailed={
                "trace": {"warning": "retry happened"},
                "last_error": "render_failed: duplicated column label",
            },
        )
    )
    plt.close(figure)
    state.turns.append(
        ConversationTurn(
            user_message=ConversationMessage(role="user", content="Thanks"),
            assistant_message=ConversationMessage(role="assistant", content="Done"),
            metadata={"note": "minimal turn"},
        )
    )
    return state


def test_conversation_export_bundle_contains_expected_files() -> None:
    bundle = build_conversation_export_bundle(_build_state())

    archive = zipfile.ZipFile(io.BytesIO(bundle.zip_bytes))
    names = set(archive.namelist())

    assert "conversation_report.md" in names
    assert "conversation_report.txt" in names
    assert "conversation_full.json" in names
    assert "errors_and_warnings.txt" in names
    assert "workspace_index.json" in names
    assert "binding_index.json" in names
    assert "turns/turn_001.json" in names
    assert "turns/turn_002.json" in names


def test_conversation_export_markdown_and_json_are_structured() -> None:
    bundle = build_conversation_export_bundle(_build_state())

    assert "# Conversation Export Report" in bundle.markdown_report
    assert "## Turn 1" in bundle.markdown_report
    assert "## Turn 2" in bundle.markdown_report
    assert "### Grounded Response" in bundle.markdown_report
    assert "### Completion Validation" in bundle.markdown_report
    assert "### SQL Guardrail Trace" in bundle.markdown_report
    assert "### Errors / Warnings" in bundle.markdown_report

    full_json = bundle.full_json
    assert full_json["turn_count"] == 2
    assert len(full_json["turns"]) == 2
    first_turn = full_json["turns"][0]
    assert first_turn["plot_plan"]["kind"] == "barplot"
    assert first_turn["metadata"]["debug_blob"]["raw_df"]["_type"] == "pandas.DataFrame"
    assert first_turn["metadata"]["debug_blob"]["figure"]["_type"] == "Figure"
    assert first_turn["metadata"]["debug_blob"]["payload"]["_type"] == "bytes"
    assert first_turn["grounded_response"]["primary_chart_artifact_id"] == "chart_1"
    assert first_turn["sql_trace"]["sql_guardrail_report"]["status"] == "repairable"


def test_conversation_export_errors_summary_extracts_render_failures() -> None:
    bundle = build_conversation_export_bundle(_build_state())

    assert "render_failed" in bundle.errors_and_warnings
    assert "retry happened" in bundle.errors_and_warnings
    assert "chart render failed" in bundle.errors_and_warnings


def test_make_json_safe_handles_heavy_objects() -> None:
    df = pd.DataFrame({"a": [1, 2], "a_dup": [3, 4]})
    payload = make_json_safe(
        {
            "dataframe": df,
            "figure": plt.figure(),
            "opaque": _OpaqueObject(),
            "payload": b"abc",
        }
    )

    assert payload["dataframe"]["_type"] == "pandas.DataFrame"
    assert payload["dataframe"]["shape"] == [2, 2]
    assert payload["figure"]["_type"] == "Figure"
    assert payload["opaque"]["_type"] == "_OpaqueObject"
    assert payload["payload"]["_type"] == "bytes"


def test_make_json_safe_redacts_secret_fields() -> None:
    payload = make_json_safe(
        {
            "api_key": "secret-value",
            "nested": {"access_token": "nested-secret", "safe": "ok"},
        }
    )

    assert payload["api_key"] == "***REDACTED***"
    assert payload["nested"]["access_token"] == "***REDACTED***"
    assert payload["nested"]["safe"] == "ok"
