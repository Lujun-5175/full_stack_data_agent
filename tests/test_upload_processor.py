import json

import pandas as pd

from full_stack_data_agent.context.upload_processor import SUPPORTED_UPLOAD_EXTENSIONS, process_uploaded_file


def test_process_uploaded_text_file() -> None:
    result = process_uploaded_file("notes.txt", b"Hello world\nThis is a test file.\nMore text.")

    assert result is not None
    assert result.file_name == "notes.txt"
    assert "Hello world" in result.summary
    assert result.snippets


def test_process_uploaded_csv_file_marks_tabular() -> None:
    result = process_uploaded_file("salary.csv", b"borough,salary\nQueens,100\nBronx,80\n", "text/csv")

    assert result is not None
    assert result.is_tabular is True
    assert result.tabular_payload == "borough,salary\nQueens,100\nBronx,80\n"
    assert result.table_name == "salary"
    assert result.row_count == 2
    assert result.columns == ["borough", "salary"]
    assert result.semantic_profile["profiling_summary"]["row_count"] == 2
    assert "salary" in result.semantic_profile["measure_candidates"]


def test_process_uploaded_csv_file_keeps_alias_collisions_visible() -> None:
    result = process_uploaded_file("collision.csv", b"a_b,a b\n1,2\n3,4\n", "text/csv")

    assert result is not None
    assert result.is_tabular is True
    assert result.semantic_profile["alias_map"]["a_b"] == ["a_b", "a b"]


def test_process_uploaded_csv_file_supports_utf8_bom_and_single_row() -> None:
    result = process_uploaded_file("single.csv", b"\xef\xbb\xbfname,value\nalpha,1\n", "text/csv")

    assert result is not None
    assert result.is_tabular is True
    assert result.row_count == 1
    assert result.columns == ["name", "value"]


def test_process_uploaded_excel_file_uses_first_sheet_and_records_sheet_metadata(monkeypatch) -> None:
    first_sheet = pd.DataFrame({"name": ["alpha", "beta"], "value": [1, 2]})
    second_sheet = pd.DataFrame({"ignored": [1]})

    monkeypatch.setattr(
        "full_stack_data_agent.context.upload_processor.pd.read_excel",
        lambda *_args, **_kwargs: {"Overview": first_sheet, "Archive": second_sheet},
    )

    result = process_uploaded_file("workbook.xlsx", b"fake-xlsx-bytes", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    assert result is not None
    assert result.is_tabular is True
    assert result.table_name == "workbook"
    assert result.row_count == 2
    assert result.columns == ["name", "value"]
    assert result.semantic_profile["source_format"] == "excel"
    assert result.semantic_profile["sheet_count"] == 2
    assert result.semantic_profile["active_sheet_name"] == "Overview"
    assert result.tabular_payload is not None
    assert "alpha" in result.tabular_payload.lower()
    assert "Excel workbook" in result.summary
    assert "Only the first sheet" in result.summary
    assert "Overview" in result.summary


def test_process_uploaded_json_file_parses_tabular_content() -> None:
    payload = json.dumps([{"team": "A", "score": 1}, {"team": "B", "score": 2}]).encode("utf-8")

    result = process_uploaded_file("scores.json", payload, "application/json")

    assert result is not None
    assert result.is_tabular is True
    assert result.table_name == "scores"
    assert result.row_count == 2
    assert result.columns == ["team", "score"]
    assert result.semantic_profile["source_format"] == "json"
    assert result.tabular_payload is not None
    assert "team,score" in result.tabular_payload.replace(" ", "")
    assert "JSON file parsed as a table" in result.summary


def test_process_uploaded_json_file_falls_back_to_text_when_not_tabular(monkeypatch) -> None:
    monkeypatch.setattr(
        "full_stack_data_agent.context.upload_processor.pd.read_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("nested json")),
    )

    payload = json.dumps({"meta": {"kind": "nested"}, "items": [{"x": 1}]}).encode("utf-8")
    result = process_uploaded_file("nested.json", payload, "application/json")

    assert result is not None
    assert result.is_tabular is False
    assert result.tabular_payload is None
    assert result.extracted_text.startswith("{")
    assert result.summary


def test_process_uploaded_file_returns_none_for_empty_or_invalid_utf8() -> None:
    assert process_uploaded_file("empty.txt", b"   \n") is None
    assert process_uploaded_file("bad.txt", b"\xff\xfe\x00\x00") is None


def test_process_uploaded_file_rejects_oversized_payload() -> None:
    payload = b"a" * ((50 * 1024 * 1024) + 1)
    assert process_uploaded_file("too_large.txt", payload) is None


def test_supported_upload_extensions_include_text_and_tabular_types() -> None:
    assert set(SUPPORTED_UPLOAD_EXTENSIONS) == {
        "csv",
        "json",
        "xlsx",
        "xls",
        "txt",
        "md",
        "yaml",
        "yml",
        "toml",
        "py",
        "log",
    }
