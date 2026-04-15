from __future__ import annotations

from typing import Final


CANONICAL_CHART_KINDS: Final[tuple[str, ...]] = (
    "histogram",
    "countplot",
    "barplot",
    "lineplot",
    "scatterplot",
    "boxplot",
    "violinplot",
    "swarmplot",
    "stripplot",
    "jointplot",
    "pairplot",
    "heatmap",
)

_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "histogram": (
        "histogram",
        "histplot",
        "hist",
        "直方图",
        "直方图表",
        "histogram chart",
        "histogram plot",
        "hist plot",
    ),
    "countplot": (
        "countplot",
        "count plot",
        "count chart",
        "计数图",
        "频数图",
    ),
    "barplot": (
        "barplot",
        "bar plot",
        "bar chart",
        "grouped bar chart",
        "grouped bar plot",
        "柱状图",
        "条形图",
        "分组柱状图",
    ),
    "lineplot": (
        "lineplot",
        "line plot",
        "line chart",
        "折线图",
        "趋势图",
    ),
    "scatterplot": (
        "scatterplot",
        "scatter plot",
        "scatter chart",
        "散点图",
    ),
    "boxplot": (
        "boxplot",
        "box plot",
        "box chart",
        "箱线图",
        "盒图",
    ),
    "violinplot": (
        "violinplot",
        "violin plot",
        "violin chart",
        "小提琴图",
    ),
    "swarmplot": (
        "swarmplot",
        "swarm plot",
        "swarm chart",
        "蜂群图",
    ),
    "stripplot": (
        "stripplot",
        "strip plot",
        "strip chart",
        "条带图",
    ),
    "jointplot": (
        "jointplot",
        "joint plot",
        "joint chart",
        "联合图",
    ),
    "pairplot": (
        "pairplot",
        "pair plot",
        "scatter matrix",
        "成对图",
    ),
    "heatmap": (
        "heatmap",
        "heat map",
        "热力图",
        "相关矩阵",
    ),
}

_LOOKUP: Final[dict[str, str]] = {
    alias.casefold(): canonical
    for canonical, aliases in _ALIASES.items()
    for alias in (canonical, *aliases)
}


def supported_chart_kinds() -> tuple[str, ...]:
    return CANONICAL_CHART_KINDS


def chart_kind_aliases() -> dict[str, tuple[str, ...]]:
    return {kind: tuple(aliases) for kind, aliases in _ALIASES.items()}


def canonicalize_chart_kind(kind: str | None) -> str | None:
    if kind is None:
        return None
    cleaned = str(kind).strip()
    if not cleaned:
        return None
    return _LOOKUP.get(cleaned.casefold(), cleaned.casefold())


def is_supported_chart_kind(kind: str | None) -> bool:
    return canonicalize_chart_kind(kind) in CANONICAL_CHART_KINDS
