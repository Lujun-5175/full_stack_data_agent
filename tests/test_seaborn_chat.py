from __future__ import annotations

import base64

import pandas as pd
import pytest

from databao.agent import api as bao_api
from databao.agent.core.executor import ExecutionResult
from databao.agent.visualizers.seaborn_chat import SeabornChatResult, SeabornChatVisualizer


def test_seaborn_chat_result_png_exports() -> None:
    visualizer = SeabornChatVisualizer()
    result = visualizer.visualize(
        "show a bar chart by category",
        ExecutionResult(
            text="ok",
            meta={},
            df=pd.DataFrame({"category": ["A", "B", "A"], "value": [1, 2, 3]}),
        ),
    )

    assert isinstance(result, SeabornChatResult)
    png_bytes = result.png_bytes()
    assert png_bytes is not None
    assert png_bytes.startswith(b"\x89PNG")

    png_base64 = result.png_base64()
    assert png_base64 is not None
    decoded = base64.b64decode(png_base64)
    assert decoded.startswith(b"\x89PNG")
    assert result.image() is not None
    assert result.meta["plot_backend"] == "seaborn"
    assert result.meta["plot_kind"] is not None


def test_agent_defaults_to_seaborn_visualizer(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            captured["visualizer"] = kwargs["visualizer"]

    monkeypatch.setattr(bao_api, "Agent", FakeAgent)
    bao_api.agent(object(), data_executor=object())

    assert isinstance(captured["visualizer"], SeabornChatVisualizer)


@pytest.mark.parametrize(
    ("prompt", "dataframe", "expected_kind"),
    [
        (
            "show a violin plot by category",
            pd.DataFrame({"category": ["A", "A", "B", "B"], "value": [1.0, 2.0, 3.0, 4.0]}),
            "violinplot",
        ),
        (
            "show a swarm plot by category",
            pd.DataFrame({"category": ["A", "A", "B", "B"], "value": [1.0, 2.0, 3.0, 4.0]}),
            "swarmplot",
        ),
        (
            "show a strip plot by category",
            pd.DataFrame({"category": ["A", "A", "B", "B"], "value": [1.0, 2.0, 3.0, 4.0]}),
            "stripplot",
        ),
        (
            "show a heatmap of correlations",
            pd.DataFrame({"a": [1, 2, 3, 4], "b": [2, 3, 4, 5], "c": [4, 3, 2, 1]}),
            "heatmap",
        ),
        (
            "show a pairplot",
            pd.DataFrame({"a": [1, 2, 3, 4], "b": [2, 3, 4, 5], "c": [4, 3, 2, 1]}),
            "pairplot",
        ),
        (
            "show a jointplot",
            pd.DataFrame({"x": [1, 2, 3, 4], "y": [2, 4, 6, 8]}),
            "jointplot",
        ),
    ],
)
def test_seaborn_chat_supports_additional_chart_types(prompt: str, dataframe: pd.DataFrame, expected_kind: str) -> None:
    visualizer = SeabornChatVisualizer()
    result = visualizer.visualize(
        prompt,
        ExecutionResult(
            text="ok",
            meta={},
            df=dataframe,
        ),
    )

    assert isinstance(result, SeabornChatResult)
    assert result.kind == expected_kind
    png_bytes = result.png_bytes()
    assert png_bytes is not None
    assert png_bytes.startswith(b"\x89PNG")
    assert result.png_base64() is not None
