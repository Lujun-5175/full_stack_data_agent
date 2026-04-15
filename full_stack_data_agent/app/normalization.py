from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from full_stack_data_agent.utils.text_classification import (
    BOOLEAN_TOKEN_MAP,
    DATETIME_HINT_RE,
    NUMERIC_RE,
    series_fullmatch,
)
_INTEGER_RE = re.compile(r"^[+-]?\d+$")
_COMMA_NUMERIC_RE = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


@dataclass(frozen=True)
class ColumnNormalizationResult:
    column: str
    original_dtype: str
    inferred_dtype: str
    coercion_applied: list[str] = field(default_factory=list)
    parse_success_rate: float | None = None
    warnings: list[str] = field(default_factory=list)
    skipped: bool = False
    skipped_reason: str | None = None
    null_before: int = 0
    null_after: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizationResult:
    dataframe: pd.DataFrame
    report: list[ColumnNormalizationResult]
    column_type_map: dict[str, str]
    warnings: list[str]
    columns_skipped: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "column_type_map": self.column_type_map,
            "warnings": self.warnings,
            "columns_skipped": self.columns_skipped,
            "report": [item.to_dict() for item in self.report],
        }


def normalize_dataframe(dataframe: pd.DataFrame) -> NormalizationResult:
    normalized = dataframe.copy()
    report: list[ColumnNormalizationResult] = []
    column_type_map: dict[str, str] = {}
    warnings: list[str] = []
    columns_skipped: list[dict[str, Any]] = []

    for column in normalized.columns:
        series = normalized[column]
        original_dtype = str(series.dtype)
        null_before = int(series.isna().sum())

        if pd.api.types.is_datetime64_any_dtype(series):
            inferred_dtype = "time"
            report.append(
                ColumnNormalizationResult(
                    column=str(column),
                    original_dtype=original_dtype,
                    inferred_dtype=inferred_dtype,
                    coercion_applied=[],
                    parse_success_rate=1.0,
                    warnings=[],
                    skipped=False,
                    skipped_reason=None,
                    null_before=null_before,
                    null_after=int(series.isna().sum()),
                )
            )
            column_type_map[str(column)] = inferred_dtype
            continue

        if pd.api.types.is_bool_dtype(series):
            inferred_dtype = "boolean"
            report.append(
                ColumnNormalizationResult(
                    column=str(column),
                    original_dtype=original_dtype,
                    inferred_dtype=inferred_dtype,
                    coercion_applied=[],
                    parse_success_rate=1.0,
                    warnings=[],
                    skipped=False,
                    skipped_reason=None,
                    null_before=null_before,
                    null_after=int(series.isna().sum()),
                )
            )
            column_type_map[str(column)] = inferred_dtype
            continue

        if pd.api.types.is_numeric_dtype(series):
            inferred_dtype = "numeric"
            report.append(
                ColumnNormalizationResult(
                    column=str(column),
                    original_dtype=original_dtype,
                    inferred_dtype=inferred_dtype,
                    coercion_applied=[],
                    parse_success_rate=1.0,
                    warnings=[],
                    skipped=False,
                    skipped_reason=None,
                    null_before=null_before,
                    null_after=int(series.isna().sum()),
                )
            )
            column_type_map[str(column)] = inferred_dtype
            continue

        cleaned = _clean_text_series(series)
        normalized[column] = cleaned
        null_after_cleaning = int(cleaned.isna().sum())
        non_null = cleaned.dropna()

        column_warnings: list[str] = []
        coercion_applied: list[str] = ["trim_whitespace", "empty_string_to_null"]
        skipped = False
        skipped_reason: str | None = None
        parse_success_rate: float | None = None
        best_parse_rate = 0.0

        if non_null.empty:
            inferred_dtype = "string"
            skipped = True
            skipped_reason = "column contains no non-null values after cleanup"
            parse_success_rate = 0.0
            column_warnings.append(skipped_reason)
            columns_skipped.append(
                {
                    "column": str(column),
                    "reason": skipped_reason,
                    "original_dtype": original_dtype,
                }
            )
        else:
            boolean_rate = _boolean_parse_success(non_null)
            datetime_rate, datetime_like_rate = _datetime_parse_success(non_null)
            numeric_rate, numeric_like_ratio, integer_like_ratio = _numeric_parse_success(non_null)
            key_like = _looks_like_key(non_null, numeric_like_ratio=numeric_like_ratio, integer_like_ratio=integer_like_ratio)
            unique_ratio = float(non_null.nunique(dropna=True) / max(len(non_null), 1))
            best_parse_rate = max(boolean_rate, datetime_rate, numeric_rate)

            if boolean_rate >= 0.95:
                coerced = _coerce_boolean(cleaned)
                if coerced is not None:
                    normalized[column] = coerced
                    inferred_dtype = "boolean"
                    parse_success_rate = boolean_rate
                    coercion_applied.append("boolean_canonicalization")
                    if unique_ratio > 0.5:
                        column_warnings.append("boolean-like conversion was applied to a high-cardinality column")
                else:
                    inferred_dtype = "string"
                    skipped = True
                    skipped_reason = "boolean candidate could not be canonically mapped"
                    column_warnings.append(skipped_reason)
            elif datetime_like_rate >= 0.8 and datetime_rate >= 0.9:
                coerced = pd.to_datetime(cleaned, errors="coerce")
                normalized[column] = coerced
                inferred_dtype = "time"
                parse_success_rate = datetime_rate
                coercion_applied.append("datetime_coercion")
            elif numeric_like_ratio >= 0.9 and numeric_rate >= 0.95 and not key_like:
                coerced, all_integer_like = _coerce_numeric(cleaned)
                if coerced is not None:
                    normalized[column] = coerced
                    inferred_dtype = "numeric"
                    parse_success_rate = numeric_rate
                    coercion_applied.append("numeric_coercion")
                    if all_integer_like and coerced.isna().sum() == 0:
                        try:
                            normalized[column] = coerced.astype("Int64")
                        except (TypeError, ValueError):
                            pass
                else:
                    inferred_dtype = "string"
                    skipped = True
                    skipped_reason = "numeric candidate could not be coerced safely"
                    column_warnings.append(skipped_reason)
            elif key_like:
                inferred_dtype = "string"
                skipped = True
                skipped_reason = "high-uniqueness integer-shaped column looks like a key"
                parse_success_rate = numeric_rate if numeric_rate >= boolean_rate else best_parse_rate
                column_warnings.append(skipped_reason)
            else:
                inferred_dtype = "categorical" if unique_ratio < 0.5 else "string"
                skipped = True
                skipped_reason = "parse success below safety threshold"
                parse_success_rate = best_parse_rate if best_parse_rate > 0 else None
                column_warnings.append(skipped_reason)

            if inferred_dtype in {"numeric", "time", "boolean"}:
                null_after = int(normalized[column].isna().sum())
                if null_after > null_after_cleaning:
                    column_warnings.append("coercion introduced additional null values for non-parsable entries")

            if skipped:
                columns_skipped.append(
                    {
                        "column": str(column),
                        "reason": skipped_reason,
                        "original_dtype": original_dtype,
                        "parse_success_rate": parse_success_rate,
                    }
                )

        null_after = int(normalized[column].isna().sum())
        report.append(
            ColumnNormalizationResult(
                column=str(column),
                original_dtype=original_dtype,
                inferred_dtype=inferred_dtype,
                coercion_applied=coercion_applied if inferred_dtype in {"numeric", "time", "boolean"} else coercion_applied[:2],
                parse_success_rate=parse_success_rate,
                warnings=column_warnings,
                skipped=skipped,
                skipped_reason=skipped_reason,
                null_before=null_before,
                null_after=null_after,
            )
        )
        column_type_map[str(column)] = inferred_dtype
        warnings.extend(column_warnings)

    return NormalizationResult(
        dataframe=normalized,
        report=report,
        column_type_map=column_type_map,
        warnings=warnings,
        columns_skipped=columns_skipped,
    )


def _clean_text_series(series: pd.Series) -> pd.Series:
    cleaned = series.astype("string")
    cleaned = cleaned.str.replace("\u00A0", " ", regex=False)
    cleaned = cleaned.str.replace(r"\s+", " ", regex=True).str.strip()
    cleaned = cleaned.mask(cleaned == "", pd.NA)
    return cleaned


def _boolean_parse_success(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    normalized = series.astype("string").str.lower()
    return float(normalized.isin(BOOLEAN_TOKEN_MAP.keys()).mean())


def _datetime_parse_success(series: pd.Series) -> tuple[float, float]:
    if series.empty:
        return 0.0, 0.0
    normalized = series.astype("string")
    datetime_like = series_fullmatch(normalized, DATETIME_HINT_RE)
    if not datetime_like.any():
        return 0.0, float(datetime_like.mean())
    parsed = pd.to_datetime(normalized.where(datetime_like), errors="coerce")
    success_rate = float(parsed.notna().sum() / max(datetime_like.sum(), 1))
    return success_rate, float(datetime_like.mean())


def _numeric_parse_success(series: pd.Series) -> tuple[float, float, float]:
    if series.empty:
        return 0.0, 0.0, 0.0
    normalized = series.astype("string").str.replace("\u00A0", " ", regex=False).str.strip()
    normalized = normalized.mask(normalized == "", pd.NA)
    comma_numeric = series_fullmatch(normalized, _COMMA_NUMERIC_RE)
    normalized = normalized.where(~comma_numeric, normalized.str.replace(",", "", regex=False))
    numeric_like = series_fullmatch(normalized, NUMERIC_RE)
    if not numeric_like.any():
        return 0.0, float(numeric_like.mean()), 0.0
    parsed = pd.to_numeric(normalized.where(numeric_like), errors="coerce")
    success_rate = float(parsed.notna().sum() / max(numeric_like.sum(), 1))
    parsed_non_null = parsed.dropna()
    integer_like = (parsed_non_null % 1 == 0) if not parsed_non_null.empty else pd.Series(dtype=bool)
    integer_like_ratio = float(integer_like.mean()) if not integer_like.empty else 0.0
    return success_rate, float(numeric_like.mean()), integer_like_ratio


def _coerce_boolean(series: pd.Series) -> pd.Series | None:
    normalized = series.astype("string").str.replace("\u00A0", " ", regex=False).str.strip().str.lower()
    mapped = normalized.map(BOOLEAN_TOKEN_MAP)
    if mapped.notna().sum() == 0:
        return None
    success_rate = mapped.notna().sum() / max(normalized.notna().sum(), 1)
    if success_rate < 0.95:
        return None
    return mapped.astype("boolean")


def _coerce_numeric(series: pd.Series) -> tuple[pd.Series | None, bool]:
    normalized = series.astype("string").str.replace("\u00A0", " ", regex=False).str.strip()
    normalized = normalized.mask(normalized == "", pd.NA)
    comma_numeric = series_fullmatch(normalized, _COMMA_NUMERIC_RE)
    normalized = normalized.where(~comma_numeric, normalized.str.replace(",", "", regex=False))
    numeric_like = series_fullmatch(normalized, NUMERIC_RE)
    parsed = pd.to_numeric(normalized.where(numeric_like), errors="coerce")
    if parsed.notna().sum() == 0:
        return None, False
    parsed_non_null = parsed.dropna()
    all_integer_like = bool(numeric_like.fillna(False).all() and (parsed_non_null % 1 == 0).all())
    return parsed, all_integer_like


def _looks_like_key(series: pd.Series, *, numeric_like_ratio: float, integer_like_ratio: float) -> bool:
    if series.empty:
        return False
    normalized = series.astype("string").str.replace("\u00A0", " ", regex=False).str.strip()
    normalized = normalized[normalized != ""].dropna()
    if normalized.empty:
        return False
    unique_ratio = float(normalized.nunique(dropna=True) / max(len(normalized), 1))
    average_length = float(normalized.str.len().mean())
    if unique_ratio < 0.9:
        return False
    if numeric_like_ratio < 0.95:
        return False
    if integer_like_ratio < 0.95:
        return False
    if average_length < 5:
        return False
    return True
