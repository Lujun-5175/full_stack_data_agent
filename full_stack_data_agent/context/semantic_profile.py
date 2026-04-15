from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from full_stack_data_agent.utils.text_classification import (
    BOOLEAN_TOKEN_MAP,
    DATETIME_HINT_RE,
    NUMERIC_RE,
    series_fullmatch,
)


_COLUMN_ALIAS_RE = re.compile(r"[^a-z0-9]+")
_INTEGER_RE = re.compile(r"^[+-]?\d+$")


@dataclass(frozen=True)
class ColumnSemanticHint:
    semantic_type: str
    confidence: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_semantic_profile(dataframe: pd.DataFrame) -> dict[str, Any]:
    if dataframe.empty:
        return {
            "column_type_hints": {},
            "alias_map": {},
            "canonical_categorical_value_maps": {},
            "key_candidates": [],
            "time_candidates": [],
            "measure_candidates": [],
            "profiling_summary": {
                "row_count": 0,
                "column_count": int(len(dataframe.columns)),
                "empty_frame": True,
            },
        }

    column_type_hints: dict[str, dict[str, Any]] = {}
    alias_map: dict[str, list[str]] = {}
    canonical_categorical_value_maps: dict[str, dict[str, str]] = {}
    key_candidates: list[str] = []
    time_candidates: list[str] = []
    measure_candidates: list[str] = []
    column_summaries: list[dict[str, Any]] = []

    for column in dataframe.columns:
        series = dataframe[column]
        hint = _profile_column(series)
        column_type_hints[str(column)] = hint.to_dict()
        alias = _column_alias(str(column))
        alias_map.setdefault(alias, []).append(str(column))
        column_summaries.append(
            {
                "column": str(column),
                "original_dtype": str(series.dtype),
                "non_null_count": int(series.notna().sum()),
                "unique_count": int(series.dropna().astype("string").nunique(dropna=True)),
                "missing_ratio": float(series.isna().mean()),
                "hint": hint.semantic_type,
                "confidence": hint.confidence,
            }
        )

        if hint.semantic_type == "boolean":
            canonical_map = _canonical_boolean_map(series)
            if canonical_map:
                canonical_categorical_value_maps[str(column)] = canonical_map
        elif hint.semantic_type == "categorical":
            canonical_map = _canonical_categorical_map(series)
            if canonical_map:
                canonical_categorical_value_maps[str(column)] = canonical_map

        if hint.semantic_type == "key":
            key_candidates.append(str(column))
        elif hint.semantic_type == "time":
            time_candidates.append(str(column))
        elif hint.semantic_type == "measure":
            measure_candidates.append(str(column))

    profiling_summary = {
        "row_count": int(len(dataframe)),
        "column_count": int(len(dataframe.columns)),
        "column_summaries": column_summaries,
        "high_cardinality_columns": [
            item["column"]
            for item in column_summaries
            if item["non_null_count"] and item["unique_count"] / max(item["non_null_count"], 1) >= 0.9
        ],
    }

    return {
        "column_type_hints": column_type_hints,
        "alias_map": alias_map,
        "canonical_categorical_value_maps": canonical_categorical_value_maps,
        "key_candidates": key_candidates,
        "time_candidates": time_candidates,
        "measure_candidates": measure_candidates,
        "profiling_summary": profiling_summary,
    }


def _profile_column(series: pd.Series) -> ColumnSemanticHint:
    non_null = series.dropna()
    if non_null.empty:
        return ColumnSemanticHint(semantic_type="empty", confidence=1.0, reasons=["column has no non-null values"])

    text = non_null.astype("string").str.replace("\u00A0", " ", regex=False).str.strip()
    text = text[text != ""]
    if text.empty:
        return ColumnSemanticHint(semantic_type="empty", confidence=1.0, reasons=["column only contains whitespace"])

    lower = text.str.lower()
    boolean_matches = lower.isin(BOOLEAN_TOKEN_MAP.keys())
    boolean_ratio = float(boolean_matches.mean()) if len(boolean_matches) else 0.0

    datetime_like = series_fullmatch(text, DATETIME_HINT_RE)
    datetime_like_ratio = float(datetime_like.mean()) if len(datetime_like) else 0.0

    numeric_like = series_fullmatch(text, NUMERIC_RE)
    numeric_like_ratio = float(numeric_like.mean()) if len(numeric_like) else 0.0

    integer_like = series_fullmatch(text, _INTEGER_RE)
    integer_like_ratio = float(integer_like.mean()) if len(integer_like) else 0.0

    unique_count = int(text.nunique(dropna=True))
    total_count = int(len(text))
    unique_ratio = unique_count / max(total_count, 1)
    average_length = float(text.str.len().mean()) if total_count else 0.0

    reasons: list[str] = []
    if boolean_ratio >= 0.95 and unique_count <= 2:
        reasons.append("high boolean token match rate")
        return ColumnSemanticHint(semantic_type="boolean", confidence=round(boolean_ratio, 3), reasons=reasons)

    if datetime_like_ratio >= 0.8:
        reasons.append("datetime-shaped strings")
        return ColumnSemanticHint(semantic_type="time", confidence=round(datetime_like_ratio, 3), reasons=reasons)

    if numeric_like_ratio >= 0.9:
        if unique_ratio >= 0.9 and integer_like_ratio >= 0.95 and average_length >= 5:
            reasons.append("high-uniqueness integer-shaped column is likely an identifier")
            return ColumnSemanticHint(semantic_type="key", confidence=round(unique_ratio, 3), reasons=reasons)

        reasons.append("high numeric token match rate")
        return ColumnSemanticHint(semantic_type="measure", confidence=round(numeric_like_ratio, 3), reasons=reasons)

    if unique_ratio >= 0.9:
        reasons.append("high uniqueness and non-numeric shape")
        return ColumnSemanticHint(semantic_type="key", confidence=round(unique_ratio, 3), reasons=reasons)

    reasons.append("remaining strings treated as categorical")
    return ColumnSemanticHint(semantic_type="categorical", confidence=max(round(1.0 - unique_ratio, 3), 0.1), reasons=reasons)


def _column_alias(column_name: str) -> str:
    normalized = column_name.strip().lower().replace("_", " ")
    normalized = re.sub(r"([a-z])([A-Z])", r"\1 \2", normalized)
    normalized = _COLUMN_ALIAS_RE.sub(" ", normalized)
    normalized = " ".join(normalized.split())
    return normalized.replace(" ", "_")


def _canonical_boolean_map(series: pd.Series) -> dict[str, str]:
    text = series.dropna().astype("string").str.replace("\u00A0", " ", regex=False).str.strip().str.lower()
    values = [item for item in text.tolist() if item]
    mapping: dict[str, str] = {}
    for item in sorted(set(values)):
        canonical = BOOLEAN_TOKEN_MAP.get(item)
        if canonical is None:
            continue
        mapping[item] = "true" if canonical else "false"
    return mapping


def _canonical_categorical_map(series: pd.Series) -> dict[str, str]:
    text = series.dropna().astype("string").str.replace("\u00A0", " ", regex=False).str.strip()
    text = text[text != ""]
    if text.empty:
        return {}

    normalized_groups: dict[str, Counter[str]] = {}
    for item in text.tolist():
        canonical_key = " ".join(str(item).split()).casefold()
        normalized_groups.setdefault(canonical_key, Counter())[str(item)] += 1

    mapping: dict[str, str] = {}
    for canonical_key, observed in normalized_groups.items():
        if len(observed) <= 1:
            continue
        canonical_value = observed.most_common(1)[0][0]
        for observed_value in observed:
            mapping[observed_value] = canonical_value
    return mapping
