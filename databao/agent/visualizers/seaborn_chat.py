from __future__ import annotations

import base64
import io
import json
import logging
import re
from typing import Any, Literal

import pandas as pd
import matplotlib
matplotlib.use("Agg", force=True)
from matplotlib import pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from PIL import Image
from pydantic import Field

from databao.agent.configs.llm import LLMConfig
from databao.agent.core import ExecutionResult, VisualisationResult, Visualizer
from databao.agent.core.visualizer import HistoryMode

logger = logging.getLogger(__name__)

try:  # optional dependency
    import seaborn as sns
except Exception:  # pragma: no cover
    sns = None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _is_dt(series: pd.Series) -> bool:
    return pd.api.types.is_datetime64_any_dtype(series) or pd.api.types.is_datetime64tz_dtype(series)


def _is_cat(series: pd.Series) -> bool:
    return bool(
        pd.api.types.is_object_dtype(series)
        or pd.api.types.is_string_dtype(series)
        or pd.api.types.is_categorical_dtype(series)
        or pd.api.types.is_bool_dtype(series)
    )


def _plot_like(obj: Any) -> Figure | Axes | Any | None:
    if obj is None:
        return None
    if isinstance(obj, (Figure, Axes)):
        return obj
    for attr in ("fig", "figure"):
        candidate = getattr(obj, attr, None)
        if candidate is not None and hasattr(candidate, "savefig"):
            return candidate
    if hasattr(obj, "savefig"):
        return obj
    return None


def _fig_png_bytes(fig: Any) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=144)
    return buf.getvalue()


class SeabornChatResult(VisualisationResult):
    backend: Literal["seaborn"] = "seaborn"
    kind: str | None = None
    dataframe: pd.DataFrame | None = None
    plot_config: dict[str, Any] = Field(default_factory=dict)

    def figure(self) -> Any | None:
        return _plot_like(self.plot)

    def png_bytes(self) -> bytes | None:
        plot = self.figure()
        if plot is None:
            return None
        if isinstance(plot, Axes):
            plot = plot.figure
        return _fig_png_bytes(plot)

    def png_base64(self) -> str | None:
        png = self.png_bytes()
        return None if png is None else base64.b64encode(png).decode("utf-8")

    def image(self) -> Image.Image | None:
        png = self.png_bytes()
        if png is None:
            return None
        image = Image.open(io.BytesIO(png))
        image.load()
        return image

    def _repr_mimebundle_(self, include: Any = None, exclude: Any = None) -> Any:
        png = self.png_bytes()
        if png is None:
            return None
        return {"image/png": png, "text/plain": self.text}


class SeabornChatVisualizer(Visualizer):
    def __init__(self, llm_config: LLMConfig | None = None, *, history_mode: HistoryMode = HistoryMode.LAST_QUESTION):
        super().__init__(history_mode=history_mode)
        self._llm_config = llm_config

    def _profile(self, df: pd.DataFrame) -> dict[str, list[str]]:
        cols = {
            "numeric": [str(c) for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])],
            "datetime": [str(c) for c in df.columns if _is_dt(df[c])],
            "boolean": [str(c) for c in df.columns if pd.api.types.is_bool_dtype(df[c])],
        }
        cols["categorical"] = [str(c) for c in df.columns if c not in cols["numeric"] + cols["datetime"] + cols["boolean"] and _is_cat(df[c])]
        return cols

    def _intent(self, request: str) -> str | None:
        lowered = _clean(request)
        for kind, keywords in (
            ("heatmap", ("heatmap", "correlation matrix", "corr matrix", "corr", "matrix")),
            ("pairplot", ("pairplot", "pair plot", "pairwise", "scatter matrix")),
            ("jointplot", ("jointplot", "joint plot", "joint", "bivariate")),
            ("violinplot", ("violinplot", "violin", "violin plot")),
            ("swarmplot", ("swarmplot", "swarm plot", "swarm")),
            ("stripplot", ("stripplot", "strip plot", "strip", "jitter")),
            ("boxplot", ("box", "outlier", "quartile")),
            ("scatterplot", ("scatter", "relationship", "correlation")),
            ("lineplot", ("line", "trend", "time series", "over time")),
            ("barplot", ("bar", "compare", "comparison", "aggregate")),
            ("countplot", ("countplot", "count plot", "count", "frequency")),
            ("histogram", ("hist", "histogram", "distribution")),
        ):
            if any(keyword in lowered for keyword in keywords):
                return kind
        return None

    def _choose_kind(self, request: str, profile: dict[str, list[str]]) -> str | None:
        intent = self._intent(request)
        if intent:
            return intent
        if profile["datetime"] and profile["numeric"]:
            return "lineplot"
        if len(profile["numeric"]) == 2 and not profile["categorical"]:
            return "jointplot"
        if len(profile["numeric"]) >= 3 and not profile["categorical"]:
            return "heatmap"
        if len(profile["numeric"]) >= 2:
            return "scatterplot"
        if profile["categorical"] and profile["numeric"]:
            return "barplot"
        if profile["categorical"]:
            return "countplot"
        if profile["numeric"]:
            return "histogram"
        if profile["datetime"]:
            return "lineplot"
        return None

    def _pick_columns(self, kind: str | None, profile: dict[str, list[str]]) -> dict[str, Any]:
        x = y = hue = None
        variables: list[str] = []
        if kind == "histogram":
            x = next((profile[k][0] for k in ("numeric", "datetime", "categorical") if profile[k]), None)
        elif kind == "countplot":
            x = next((profile[k][0] for k in ("categorical", "boolean", "datetime") if profile[k]), None)
        elif kind == "barplot":
            x = next((profile[k][0] for k in ("categorical", "boolean", "datetime") if profile[k]), None)
            y = profile["numeric"][0] if profile["numeric"] else None
        elif kind == "lineplot":
            x = next((profile[k][0] for k in ("datetime", "numeric", "categorical") if profile[k]), None)
            y = profile["numeric"][0] if profile["numeric"] else None
        elif kind == "scatterplot":
            x = profile["numeric"][0] if profile["numeric"] else None
            y = profile["numeric"][1] if len(profile["numeric"]) > 1 else None
            hue = profile["categorical"][0] if profile["categorical"] else None
        elif kind == "boxplot":
            x = profile["categorical"][0] if profile["categorical"] else None
            y = profile["numeric"][0] if profile["numeric"] else None
        elif kind in {"violinplot", "swarmplot", "stripplot"}:
            x = next((profile[k][0] for k in ("categorical", "boolean", "datetime") if profile[k]), None)
            y = profile["numeric"][0] if profile["numeric"] else None
            hue = profile["categorical"][1] if len(profile["categorical"]) > 1 else None
        elif kind == "jointplot":
            x = profile["numeric"][0] if profile["numeric"] else None
            y = profile["numeric"][1] if len(profile["numeric"]) > 1 else None
            hue = profile["categorical"][0] if profile["categorical"] else None
        elif kind == "pairplot":
            variables = profile["numeric"][:6]
            hue = profile["categorical"][0] if profile["categorical"] else None
        elif kind == "heatmap":
            variables = profile["numeric"][:12]
        return {"x": x, "y": y, "hue": hue, "variables": variables}

    def _render(self, kind: str, df: pd.DataFrame, columns: dict[str, Any]) -> Any:
        if sns is not None:
            sns.set_theme(style="whitegrid")
        fig: Figure | None = None
        ax: Axes | None = None
        x, y, hue = columns["x"], columns["y"], columns["hue"]
        variables = [str(column) for column in columns.get("variables", []) if column in df.columns]
        if kind == "histogram":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if x is None:
                raise ValueError("No suitable column found for histogram")
            if sns is not None:
                sns.histplot(data=df, x=x, bins=min(30, max(5, len(df) // 10 or 10)), kde=False, ax=ax)
            else:
                ax.hist(df[x].dropna(), bins=min(30, max(5, len(df) // 10 or 10)), color="#4C72B0")
            ax.set_ylabel("count")
        elif kind == "countplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if x is None:
                raise ValueError("No suitable column found for countplot")
            if sns is not None:
                sns.countplot(data=df, x=x, hue=hue if hue and hue != x else None, ax=ax)
            else:
                df[x].astype("string").value_counts(dropna=False).plot(kind="bar", ax=ax, color="#4C72B0")
            ax.set_ylabel("count")
        elif kind == "barplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if x is None:
                raise ValueError("No suitable column found for barplot")
            if y is None:
                if sns is not None:
                    sns.countplot(data=df, x=x, hue=hue if hue and hue != x else None, ax=ax)
                else:
                    df[x].astype("string").value_counts(dropna=False).plot(kind="bar", ax=ax, color="#4C72B0")
            else:
                data = df[[x, y]].dropna()
                if sns is not None:
                    sns.barplot(data=data, x=x, y=y, ax=ax, errorbar=None)
                else:
                    data.groupby(x, dropna=False)[y].mean().plot(kind="bar", ax=ax, color="#55A868")
        elif kind == "lineplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if x is None:
                raise ValueError("No suitable column found for lineplot")
            if y is None:
                if _is_dt(df[x]):
                    data = pd.DataFrame({x: pd.to_datetime(df[x], errors="coerce")}).dropna()
                    data = data.groupby(x, dropna=False).size().reset_index(name="count").sort_values(x)
                    if sns is not None:
                        sns.lineplot(data=data, x=x, y="count", marker="o", ax=ax)
                    else:
                        ax.plot(data[x], data["count"], marker="o")
                else:
                    data = df.reset_index(drop=False).rename(columns={"index": "_index"})[[x, "_index"]].dropna()
                    if sns is not None:
                        sns.lineplot(data=data, x="_index", y=x, marker="o", ax=ax)
                    else:
                        ax.plot(data["_index"], data[x], marker="o")
            else:
                data = df[[x, y]].dropna()
                if _is_dt(data[x]):
                    data = data.assign(**{x: pd.to_datetime(data[x], errors="coerce")}).dropna().sort_values(x)
                else:
                    data = data.sort_values(x)
                if sns is not None:
                    sns.lineplot(data=data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, marker="o", ax=ax)
                else:
                    ax.plot(data[x], data[y], marker="o")
        elif kind == "scatterplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if x is None or y is None:
                raise ValueError("Need two numeric columns for scatterplot")
            data = df[[x, y] + ([hue] if hue and hue not in {x, y} else [])].dropna()
            if sns is not None:
                sns.scatterplot(data=data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, ax=ax)
            else:
                ax.scatter(data[x], data[y], c="#4C72B0")
        elif kind == "boxplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if y is None:
                raise ValueError("No numeric column found for boxplot")
            if x is not None and x != y:
                data = df[[x, y]].dropna()
                if sns is not None:
                    sns.boxplot(data=data, x=x, y=y, ax=ax)
                else:
                    groups = [group[y].dropna().tolist() for _, group in data.groupby(x, dropna=False)]
                    ax.boxplot(groups, labels=[str(label) for label in data[x].dropna().astype("string").unique().tolist()])
            else:
                data = df[y].dropna().tolist()
                if sns is not None:
                    sns.boxplot(y=data, ax=ax)
                else:
                    ax.boxplot(data)
        elif kind == "violinplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if x is None or y is None:
                raise ValueError("Need one categorical column and one numeric column for violinplot")
            data = df[[x, y] + ([hue] if hue and hue not in {x, y} else [])].dropna()
            if sns is not None:
                sns.violinplot(data=data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, ax=ax, inner="quartile")
            else:
                groups = [group[y].dropna().tolist() for _, group in data.groupby(x, dropna=False)]
                ax.violinplot(groups, showmeans=False, showmedians=True)
                ax.set_xticks(range(1, len(groups) + 1))
                ax.set_xticklabels([str(label) for label in data[x].dropna().astype("string").unique().tolist()])
        elif kind == "swarmplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if x is None or y is None:
                raise ValueError("Need one categorical column and one numeric column for swarmplot")
            data = df[[x, y] + ([hue] if hue and hue not in {x, y} else [])].dropna()
            if sns is not None:
                sns.swarmplot(data=data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, ax=ax, size=3)
            else:
                ax.scatter(range(len(data)), data[y], c="#4C72B0", s=20)
        elif kind == "stripplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if x is None or y is None:
                raise ValueError("Need one categorical column and one numeric column for stripplot")
            data = df[[x, y] + ([hue] if hue and hue not in {x, y} else [])].dropna()
            if sns is not None:
                sns.stripplot(data=data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, ax=ax, jitter=True, size=3, alpha=0.7)
            else:
                ax.scatter(range(len(data)), data[y], c="#4C72B0", s=16, alpha=0.7)
        elif kind == "jointplot":
            if x is None or y is None:
                raise ValueError("Need two numeric columns for jointplot")
            data = df[[x, y] + ([hue] if hue and hue not in {x, y} else [])].dropna()
            if sns is not None:
                grid = sns.jointplot(data=data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, kind="scatter")
                grid.fig.suptitle(kind.replace("plot", " plot").title())
                grid.fig.tight_layout()
                return grid
            fig, ax = plt.subplots(figsize=(6, 6))
            ax.scatter(data[x], data[y], c="#4C72B0")
            ax.set_xlabel(x)
            ax.set_ylabel(y)
        elif kind == "pairplot":
            if len(variables) < 2:
                raise ValueError("Need at least two numeric columns for pairplot")
            data = df[variables + ([hue] if hue and hue not in variables else [])].dropna()
            if sns is not None:
                grid = sns.pairplot(data=data, vars=variables, hue=hue if hue and hue in data.columns else None, corner=True, diag_kind="hist")
                grid.fig.suptitle(kind.replace("plot", " plot").title())
                grid.fig.tight_layout()
                return grid
            axes = pd.plotting.scatter_matrix(data[variables], figsize=(8, 8))
            fig = axes[0, 0].figure
            fig.suptitle(kind.replace("plot", " plot").title())
            fig.tight_layout()
            return fig
        elif kind == "heatmap":
            if len(variables) < 2:
                raise ValueError("Need at least two numeric columns for heatmap")
            fig, ax = plt.subplots(figsize=(8, 6))
            corr = df[variables].corr(numeric_only=True)
            if sns is not None:
                sns.heatmap(corr, cmap="viridis", annot=len(variables) <= 8, ax=ax)
            else:
                im = ax.imshow(corr.values, cmap="viridis")
                ax.set_xticks(range(len(corr.columns)))
                ax.set_yticks(range(len(corr.index)))
                ax.set_xticklabels(list(corr.columns), rotation=45, ha="right")
                ax.set_yticklabels(list(corr.index))
                fig.colorbar(im, ax=ax)
        else:
            raise ValueError("No suitable chart kind could be inferred")
        if ax is not None:
            ax.set_title(kind.replace("plot", " plot").title())
        fig.tight_layout()
        return fig

    def _result(self, request: str, df: pd.DataFrame | None, kind: str | None, text: str, plot: Any | None, columns: dict[str, Any]) -> SeabornChatResult:
        meta = {
            VisualisationResult.META_PLOT_MESSAGES_KEY: [] if df is None else [
                {"backend": "seaborn", "request": request, "kind": kind, "columns": columns}
            ],
            "plot_backend": "seaborn",
            "plot_kind": kind,
            "plot_config": columns,
        }
        return SeabornChatResult(
            text=text,
            meta=meta,
            plot=plot,
            code=json.dumps({"backend": "seaborn", "kind": kind, "plot_config": columns}, indent=2),
            dataframe=df,
            kind=kind,
            plot_config=columns,
            visualizer=self,
        )

    def _visualize(self, request: str, data: ExecutionResult, *, stream: bool = False) -> SeabornChatResult:
        if data.df is None or data.df.empty:
            return self._result(request, data.df, None, "Nothing to visualize", None, {"x": None, "y": None, "hue": None})
        profile = self._profile(data.df)
        kind = self._choose_kind(request, profile)
        columns = self._pick_columns(kind, profile)
        if kind is None:
            return self._result(request, data.df, None, "Failed to infer a chart kind for the provided dataframe.", None, columns)
        try:
            fig = self._render(kind, data.df, columns)
        except Exception as exc:
            logger.warning("Failed to render seaborn chart: %s", exc)
            return self._result(request, data.df, kind, f"Failed to visualize request! Output: {exc}", None, columns)
        return self._result(request, data.df, kind, f"Rendered {kind} chart.", fig, columns)

    def edit(self, request: str, visualization: VisualisationResult, *, stream: bool = False) -> SeabornChatResult:
        if not isinstance(visualization, SeabornChatResult):
            raise ValueError(f"{self.__class__.__name__} can only edit {SeabornChatResult.__name__} objects")
        if visualization.dataframe is None:
            raise ValueError("No dataframe found in the provided visualization")
        if visualization.meta.get(VisualisationResult.META_PLOT_MESSAGES_KEY) is None:
            raise ValueError("No plot message history found in the provided visualization")
        return self._visualize(request, ExecutionResult(text=visualization.text, meta={}, df=visualization.dataframe), stream=stream)
