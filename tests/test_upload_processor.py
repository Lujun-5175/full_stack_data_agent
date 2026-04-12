from full_stack_data_agent.context.upload_processor import process_uploaded_file


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
