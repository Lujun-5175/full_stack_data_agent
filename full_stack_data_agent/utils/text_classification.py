from __future__ import annotations

import re

import pandas as pd

BOOLEAN_TOKEN_MAP = {
    "true": True,
    "false": False,
    "yes": True,
    "no": False,
    "y": True,
    "n": False,
    "t": True,
    "f": False,
    "1": True,
    "0": False,
    "on": True,
    "off": False,
}

NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$")
DATETIME_HINT_RE = re.compile(
    r"(?ix)"
    r"(^\d{4}[-/]\d{1,2}[-/]\d{1,2}([ t]\d{1,2}:\d{2}(:\d{2})?)?$)"
    r"|(^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$)"
    r"|(^[a-z]{3,9}\s+\d{1,2},?\s+\d{4}$)"
    r"|(^\d{4}-\d{2}$)"
)

EXPLICIT_CHART_REQUEST_MARKERS = (
    "plot",
    "chart",
    "graph",
    "visualize",
    "visualization",
    "visualisation",
    "histogram",
    "scatter plot",
    "bar chart",
    "line chart",
    "heatmap",
    "box plot",
    "violin plot",
    "count plot",
    "画图",
    "绘图",
    "直方图",
    "散点图",
    "热力图",
    "柱状图",
    "条形图",
    "折线图",
    "箱线图",
    "小提琴图",
)

def series_fullmatch(series: pd.Series, pattern: re.Pattern[str]) -> pd.Series:
    values = series.astype("string")
    return values.str.fullmatch(pattern, na=False)
