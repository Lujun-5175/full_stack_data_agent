import copy
import re
import warnings
from decimal import Decimal
from typing import Any

import altair as alt
import pandas as pd

from edaplot.spec_utils import SpecType


def spec_add_data(spec: SpecType, df: pd.DataFrame) -> SpecType:
    # TODO deal with charts with multiple 'data' fields
    # Required reading for performance considerations:
    # - https://altair-viz.github.io/user_guide/data_transformers.html
    # - https://altair-viz.github.io/user_guide/large_datasets.html
    # TLDR: see alt.sample(), alt.to_json() and vegafusion data transformations.
    # We can't use alt.Chart(df).to_dict() directly because spec might not be a valid schema.
    spec_new = copy.deepcopy(spec)
    spec_new["data"] = alt.to_values(df)
    return spec_new


def spec_remove_data(spec: Any) -> None:
    """Remove all "data" and "datasets" fields from the spec."""
    if isinstance(spec, dict):
        for k in ("data", "datasets"):
            if k in spec:
                del spec[k]
        for _k, v in spec.items():
            spec_remove_data(v)
    elif isinstance(spec, list):
        for v in spec:
            spec_remove_data(v)


def df_is_datetime_column(df: pd.DataFrame, col: str) -> bool:
    # N.B. This api is public
    return pd.api.types.is_datetime64_any_dtype(df[col])


def normalize_column_name(name: str) -> str:
    name = name.strip()
    if len(name) == 0:
        return "nop"
    return re.sub(r"[^0-9a-zA-Z_]", "_", name)


def df_normalize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    # TODO Open VL bug: Unable to use quotes in encoding field definition https://github.com/vega/vega-lite/issues/7790
    # Assumption: df doesn't have duplicate column names.
    renaming = {}  # old -> new
    new_names = set()
    normalized_names = {col_name: normalize_column_name(col_name) for col_name in df.columns}

    # Keep unchanged names the same
    for col_name, new_name in normalized_names.items():
        if col_name == new_name:
            new_names.add(new_name)
            renaming[col_name] = new_name

    # Handle new names with deduplication
    for col_name, new_name in normalized_names.items():
        if col_name == new_name:
            continue

        if new_name not in new_names:
            new_names.add(new_name)
            renaming[col_name] = new_name
            continue

        cnt = 0
        dup_new_name = new_name
        while dup_new_name in new_names:
            dup_new_name = f"{new_name}_{cnt}"
            cnt += 1
        new_names.add(dup_new_name)
        renaming[col_name] = dup_new_name

    return df.rename(columns=renaming)


def df_parse_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Convert columns to datetimes if possible."""

    def convert_column(col: "pd.Series[Any]") -> "pd.Series[Any]":
        if col.dtype == object:
            try:
                # errors="ignore" is deprecated
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    return pd.to_datetime(col, errors="raise", format=None)
            except (pd.errors.ParserError, ValueError, TypeError):
                pass
        return col

    return df.apply(convert_column, axis=0)


def df_convert_types(df: pd.DataFrame) -> pd.DataFrame:
    def convert_column(col: "pd.Series[Any]") -> "pd.Series[Any]":
        if col.dtype == object:
            # Check if the column contains Decimal objects (not supported by scenegraph)
            try:
                if any(isinstance(x, Decimal) for x in col):
                    return pd.to_numeric(col, errors="raise", downcast="float")
            except (ValueError, TypeError):
                pass
        return col

    return df.apply(convert_column, axis=0)


def df_preprocess(
    df: pd.DataFrame, *, normalize_column_names: bool, parse_dates: bool, convert_types: bool = True
) -> pd.DataFrame:
    if convert_types:
        df = df_convert_types(df)
    if normalize_column_names:
        df = df_normalize_column_names(df)
    if parse_dates:
        df = df_parse_dates(df)
    return df
