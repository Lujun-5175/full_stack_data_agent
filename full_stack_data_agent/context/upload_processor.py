from __future__ import annotations

from dataclasses import asdict
import csv
from io import StringIO
from pathlib import Path

import pandas as pd

from full_stack_data_agent.context.semantic_profile import build_semantic_profile
from full_stack_data_agent.context.models import UploadedFileContext


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".toml", ".py", ".log"}


def process_uploaded_file(file_name: str, content: bytes, mime_type: str | None = None) -> UploadedFileContext | None:
    suffix = Path(file_name).suffix.lower()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None

    text = text.strip()
    if not text:
        return None

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    summary_lines = lines[:8]
    snippets = []
    for index, line in enumerate(lines[:5], start=1):
        snippets.append(f"{index}. {line[:220]}")

    summary = " ".join(summary_lines[:4]) if summary_lines else text[:240]
    is_tabular = False
    row_count: int | None = None
    columns: list[str] = []
    table_name: str | None = None
    semantic_profile: dict[str, object] = {}
    if suffix == ".csv" or (mime_type and "csv" in mime_type.lower()):
        try:
            dataframe = pd.read_csv(StringIO(text))
        except Exception:
            dataframe = None
        if dataframe is not None:
            is_tabular = True
            row_count = int(len(dataframe))
            columns = [str(column) for column in dataframe.columns]
            semantic_profile = build_semantic_profile(dataframe)
            stem = Path(file_name).stem.lower().replace("-", "_").replace(" ", "_")
            table_name = "".join(char for char in stem if char.isalnum() or char == "_").strip("_") or "uploaded_data"
            if not table_name[0].isalpha():
                table_name = f"uploaded_{table_name}"
        else:
            rows = list(csv.reader(StringIO(text)))
            if rows and rows[0]:
                is_tabular = True
                columns = [str(value) for value in rows[0]]
                row_count = max(len(rows) - 1, 0)
                stem = Path(file_name).stem.lower().replace("-", "_").replace(" ", "_")
                table_name = "".join(char for char in stem if char.isalnum() or char == "_").strip("_") or "uploaded_data"
                if not table_name[0].isalpha():
                    table_name = f"uploaded_{table_name}"

    return UploadedFileContext(
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=len(content),
        summary=summary[:800],
        snippets=snippets,
        extracted_text=text,
        is_tabular=is_tabular,
        table_name=table_name,
        row_count=row_count,
        columns=columns,
        semantic_profile=semantic_profile if is_tabular else {},
    )


def uploaded_contexts_to_dicts(contexts: list[UploadedFileContext]) -> list[dict]:
    return [asdict(item) for item in contexts]
