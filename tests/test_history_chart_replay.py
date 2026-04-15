from __future__ import annotations

from types import SimpleNamespace

from full_stack_data_agent.ui import components


def test_history_chart_replay_uses_workspace_snapshot_image_payload(monkeypatch) -> None:
    rendered: list[object] = []
    monkeypatch.setattr(components.st, "image", lambda image, use_container_width=True: rendered.append(image))
    monkeypatch.setattr(components.st, "error", lambda message: None)

    result = SimpleNamespace(
        last_databao_result={
            "result_workspace": {
                "artifacts": [
                    {
                        "artifact_id": "chart:history",
                        "artifact_type": "chart",
                        "render_payload": {
                            "render_kind": "image_base64",
                            "plot_image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2eY1cAAAAASUVORK5CYII=",
                        },
                        "has_heavy_runtime_object": False,
                    }
                ]
            },
            "grounded_response": {"primary_chart_artifact_id": "chart:history"},
        }
    )

    success = components._render_chart_from_response(result, chart_debug={})

    assert success is True
    assert rendered


def test_history_chart_replay_rejects_retired_chart_spec_payload(monkeypatch) -> None:
    errors: list[str] = []
    monkeypatch.setattr(components.st, "error", lambda message: errors.append(str(message)))

    result = SimpleNamespace(
        last_databao_result={
            "result_workspace": {
                "artifacts": [
                    {
                        "artifact_id": "chart:history",
                        "artifact_type": "chart",
                        "render_payload": {
                            "render_kind": "chart_spec",
                            "chart_spec": {"mark": "bar"},
                            "chart_data": [{"category": "A", "value": 1}],
                        },
                        "has_heavy_runtime_object": False,
                    }
                ]
            },
            "grounded_response": {"primary_chart_artifact_id": "chart:history"},
        }
    )

    success = components._render_chart_from_response(result, chart_debug={})

    assert success is False
    assert errors == []
