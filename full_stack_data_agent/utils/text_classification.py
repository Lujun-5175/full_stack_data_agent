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

EXPLICIT_VISUALIZATION_MARKERS = (
    "plot",
    "chart",
    "graph",
    "visual",
    "visualize",
    "visualization",
    "visualisation",
    "histogram",
    "scatter",
    "bar chart",
    "line chart",
    "heatmap",
    "box plot",
    "distribution",
    "逕ｻ蝗ｾ",
    "扈伜崟",
    "蝗ｾ陦ｨ",
    "蛻・ｸ・崟",
    "逶ｴ譁ｹ蝗ｾ",
    "謨｣轤ｹ蝗ｾ",
    "辜ｭ蜉帛崟",
    "譟ｱ迥ｶ蝗ｾ",
    "謚倡ｺｿ蝗ｾ",
    "邂ｱ郤ｿ蝗ｾ",
)

CHART_INTENT_MARKERS = EXPLICIT_VISUALIZATION_MARKERS


def series_fullmatch(series: pd.Series, pattern: re.Pattern[str]) -> pd.Series:
    values = series.astype("string")
    return values.map(lambda value: bool(pattern.fullmatch(str(value))) if not pd.isna(value) else False)
