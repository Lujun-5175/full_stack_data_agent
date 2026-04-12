from __future__ import annotations

import pandas as pd

from full_stack_data_agent.app.normalization import normalize_dataframe


def test_normalize_dataframe_applies_safe_generic_coercions() -> None:
    dataframe = pd.DataFrame(
        {
            "text_with_spaces": ["  hello  ", "world", "   "],
            "numeric_text": ["1.5", "2", "3.25"],
            "datetime_text": ["2024-01-02", "2024-03-04", "2024-05-06"],
            "boolean_text": ["Yes", "No", "yes"],
            "key_like_text": ["10001", "10002", "10003"],
        }
    )

    result = normalize_dataframe(dataframe)

    assert result.dataframe["text_with_spaces"].tolist()[:2] == ["hello", "world"]
    assert pd.isna(result.dataframe["text_with_spaces"].iloc[2])
    assert pd.api.types.is_float_dtype(result.dataframe["numeric_text"])
    assert pd.api.types.is_datetime64_any_dtype(result.dataframe["datetime_text"])
    assert pd.api.types.is_bool_dtype(result.dataframe["boolean_text"])
    assert result.column_type_map["key_like_text"] == "string"
    assert any(item["column"] == "key_like_text" for item in result.columns_skipped)


def test_normalize_dataframe_reports_parse_rates_and_warnings() -> None:
    dataframe = pd.DataFrame(
        {
            "mixed_column": ["1", "two", "3"],
            "all_blank": [" ", "", None],
        }
    )

    result = normalize_dataframe(dataframe)
    mixed_report = next(item for item in result.report if item.column == "mixed_column")
    blank_report = next(item for item in result.report if item.column == "all_blank")

    assert mixed_report.original_dtype in {"object", "str"}
    assert mixed_report.parse_success_rate is not None
    assert mixed_report.warnings
    assert blank_report.skipped is True
    assert blank_report.skipped_reason
    assert blank_report.parse_success_rate == 0.0
    assert result.warnings


def test_normalize_dataframe_handles_all_null_and_single_row_columns() -> None:
    dataframe = pd.DataFrame(
        {
            "all_null": [None],
            "single_value": ["42"],
            "single_date": ["2024-01-02"],
        }
    )

    result = normalize_dataframe(dataframe)

    assert result.column_type_map["all_null"] == "string"
    assert result.column_type_map["single_value"] == "numeric"
    assert result.column_type_map["single_date"] == "time"
    assert any(item["column"] == "all_null" for item in result.columns_skipped)
