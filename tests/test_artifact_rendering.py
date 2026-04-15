from __future__ import annotations

from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt

from full_stack_data_agent.app.databao_runtime import DatabaoRuntime
from full_stack_data_agent.config.settings import get_settings


class _WithPlotAttribute:
    def __init__(self, payload: object) -> None:
        self.plot = payload


class _WithPngBytes:
    def png_bytes(self) -> bytes:
        return b"fake-bytes"


def _runtime() -> DatabaoRuntime:
    return DatabaoRuntime(get_settings())


def test_chart_render_payload_prefers_runtime_handle_for_custom_object() -> None:
    runtime = _runtime()

    render_payload, runtime_handles = runtime._build_chart_render_payload(  # type: ignore[attr-defined]
        _WithPngBytes(),
        plot_plan=None,
        plot_spec=None,
        plot_data=None,
        plot_meta={},
        plot_backend=None,
        plot_kind=None,
        plot_image_base64=None,
        plot_error=None,
    )

    assert render_payload["render_kind"] == "runtime_handle"
    assert runtime_handles["plot_result"] is not None


def test_chart_render_payload_describes_plot_attribute_and_matplotlib_types() -> None:
    runtime = _runtime()

    payload_runtime, _ = runtime._build_chart_render_payload(  # type: ignore[attr-defined]
        _WithPlotAttribute(SimpleNamespace(name="plot-object")),
        plot_plan=None,
        plot_spec=None,
        plot_data=None,
        plot_meta={},
        plot_backend=None,
        plot_kind=None,
        plot_image_base64=None,
        plot_error=None,
    )
    assert payload_runtime["render_kind"] == "plot_attr"

    figure, axis = plt.subplots()
    try:
        axis.plot([1, 2], [3, 4])
        payload_figure, _ = runtime._build_chart_render_payload(  # type: ignore[attr-defined]
            _WithPlotAttribute(figure),
            plot_plan=None,
            plot_spec=None,
            plot_data=None,
            plot_meta={},
            plot_backend=None,
            plot_kind=None,
            plot_image_base64=None,
            plot_error=None,
        )
        payload_axes, _ = runtime._build_chart_render_payload(  # type: ignore[attr-defined]
            _WithPlotAttribute(axis),
            plot_plan=None,
            plot_spec=None,
            plot_data=None,
            plot_meta={},
            plot_backend=None,
            plot_kind=None,
            plot_image_base64=None,
            plot_error=None,
        )
        assert payload_figure["render_kind"] == "matplotlib_figure"
        assert payload_axes["render_kind"] == "matplotlib_axes"
    finally:
        plt.close(figure)


def test_chart_render_payload_describes_image_and_chart_plan_paths() -> None:
    runtime = _runtime()

    image_payload, _ = runtime._build_chart_render_payload(  # type: ignore[attr-defined]
        SimpleNamespace(),
        plot_plan={"kind": "histogram", "x": "value"},
        plot_spec=None,
        plot_data=None,
        plot_meta={},
        plot_backend="seaborn",
        plot_kind="histogram",
        plot_image_base64="ZmFrZQ==",
        plot_error=None,
    )
    plan_payload, _ = runtime._build_chart_render_payload(  # type: ignore[attr-defined]
        None,
        plot_plan={"kind": "bar", "x": "category"},
        plot_spec=None,
        plot_data=[{"x": "A", "y": 1}],
        plot_meta={},
        plot_backend="seaborn",
        plot_kind="bar",
        plot_image_base64=None,
        plot_error=None,
    )

    assert image_payload["render_kind"] == "image_base64"
    assert image_payload["chart_plan"]["kind"] == "histogram"
    assert plan_payload["render_kind"] == "chart_plan"
