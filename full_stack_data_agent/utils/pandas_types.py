from __future__ import annotations

import pandas as pd


def is_categorical_dtype(value: object) -> bool:
    dtype = getattr(value, "dtype", value)
    return isinstance(dtype, pd.CategoricalDtype)


def is_datetime64tz_dtype(value: object) -> bool:
    dtype = getattr(value, "dtype", value)
    return isinstance(dtype, pd.DatetimeTZDtype)
