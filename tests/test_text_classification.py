from __future__ import annotations

import re

import pandas as pd

from full_stack_data_agent.utils.text_classification import series_fullmatch


def test_series_fullmatch_handles_nulls_and_non_strings() -> None:
    series = pd.Series(["12", 12, None, "12.5", "abc"])

    result = series_fullmatch(series, re.compile(r"^[0-9]+(?:\.[0-9]+)?$"))

    assert result.tolist() == [True, True, False, True, False]


def test_series_fullmatch_accepts_precompiled_pattern() -> None:
    series = pd.Series(["2024-01", "2024-1", "bad"])

    result = series_fullmatch(series, re.compile(r"^\d{4}-\d{2}$"))

    assert result.tolist() == [True, False, False]
