from __future__ import annotations

from dataclasses import asdict
import csv
import logging
from io import BytesIO, StringIO
from pathlib import Path

import pandas as pd

from full_stack_data_agent.context.semantic_profile import build_semantic_profile
from full_stack_data_agent.context.models import UploadedFileContext


logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {".txt", ".md", ".yaml", ".yml", ".toml", ".py", ".log"}
TABULAR_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json"}
SUPPORTED_UPLOAD_EXTENSIONS = tuple(
    sorted(
        {
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
    )
)


def _table_name_from_file_name(file_name: str) -> str:
    stem = Path(file_name).stem.lower().replace("-", "_").replace(" ", "_")
    table_name = "".join(char for char in stem if char.isalnum() or char == "_").strip("_") or "uploaded_data"
    if not table_name[0].isalpha():
        table_name = f"uploaded_{table_name}"
    return table_name


def _text_summary_and_snippets(text: str) -> tuple[str, list[str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    summary_lines = lines[:8]
    snippets = [f"{index}. {line[:220]}" for index, line in enumerate(lines[:5], start=1)]
    summary = " ".join(summary_lines[:4]) if summary_lines else text[:240]
    return summary[:800], snippets


def _tabular_payload_from_dataframe(dataframe: pd.DataFrame) -> str:
    return dataframe.to_csv(index=False)


def _tabular_context(
    *,
    file_name: str,
    mime_type: str | None,
    size_bytes: int,
    dataframe: pd.DataFrame,
    source_format: str,
    summary_prefix: str = "",
    extra_summary: str = "",
    extracted_text: str | None = None,
    tabular_payload: str | None = None,
    extra_snippets: list[str] | None = None,
    semantic_profile: dict[str, object] | None = None,
) -> UploadedFileContext:
    row_count = int(len(dataframe))
    columns = [str(column) for column in dataframe.columns]
    preview_text = dataframe.head(5).to_markdown(index=False)
    snippets = []
    if summary_prefix:
        snippets.append(summary_prefix)
    for index, line in enumerate(preview_text.splitlines()[:6], start=1):
        snippets.append(f"{index}. {line[:220]}")
    if extra_snippets:
        snippets.extend(extra_snippets)
    summary = f"{summary_prefix} {extra_summary}".strip()
    if not summary:
        summary = f"{source_format.upper()} table with {row_count} rows and {len(columns)} columns."
    profile = build_semantic_profile(dataframe)
    profile.update(semantic_profile or {})
    profile["source_format"] = source_format
    tabular_payload = tabular_payload or _tabular_payload_from_dataframe(dataframe)
    table_name = _table_name_from_file_name(file_name)
    return UploadedFileContext(
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        summary=summary[:800],
        snippets=snippets,
        extracted_text=(extracted_text or preview_text),
        tabular_payload=tabular_payload,
        is_tabular=True,
        table_name=table_name,
        row_count=row_count,
        columns=columns,
        semantic_profile=profile,
    )


def _json_dataframe_is_tabular(dataframe: pd.DataFrame) -> bool:
    if dataframe.empty or not len(dataframe.columns):
        return False
    sample = dataframe.head(20).to_numpy().ravel()
    for value in sample:
        if isinstance(value, (dict, list, tuple, set)):
            return False
    return True


def process_uploaded_file(file_name: str, content: bytes, mime_type: str | None = None) -> UploadedFileContext | None:
    suffix = Path(file_name).suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        try:
            sheets = pd.read_excel(BytesIO(content), sheet_name=None)
        except Exception as exc:
            logger.warning("Failed to parse Excel upload %s: %s", file_name, exc)
            return None
        if not isinstance(sheets, dict) or not sheets:
            return None
        sheet_names = list(sheets.keys())
        active_sheet_name = sheet_names[0]
        dataframe = sheets[active_sheet_name]
        if not isinstance(dataframe, pd.DataFrame) or dataframe.empty or not len(dataframe.columns):
            return None
        summary_prefix = f"Excel workbook with {len(sheet_names)} sheet(s)."
        extra_summary = f"Processing sheet '{active_sheet_name}'."
        context = _tabular_context(
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=len(content),
            dataframe=dataframe,
            source_format="excel",
            summary_prefix=summary_prefix,
            extra_summary=extra_summary,
            extracted_text=(
                f"{summary_prefix} {extra_summary}\n\n"
                f"Preview of '{active_sheet_name}':\n{dataframe.head(5).to_markdown(index=False)}"
            )[:2000],
            extra_snippets=[summary_prefix, extra_summary],
            semantic_profile={
                "sheet_count": len(sheet_names),
                "sheet_names": sheet_names,
                "active_sheet_name": active_sheet_name,
            },
        )
        return context

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        if suffix in TABULAR_EXTENSIONS:
            logger.warning("Failed to decode uploaded text-like file %s", file_name)
        return None

    raw_text = text
    text = text.strip()
    if not text:
        return None

    if suffix == ".csv" or (mime_type and "csv" in mime_type.lower()):
        try:
            dataframe = pd.read_csv(StringIO(text))
        except Exception:
            dataframe = None
        if dataframe is not None:
            return _tabular_context(
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=len(content),
                dataframe=dataframe,
                source_format="csv",
                tabular_payload=raw_text,
            )
        rows = list(csv.reader(StringIO(text)))
        if rows and rows[0]:
            header = [str(value) for value in rows[0]]
            dataframe = pd.DataFrame(rows[1:], columns=header)
            return _tabular_context(
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=len(content),
                dataframe=dataframe,
                source_format="csv",
                tabular_payload=raw_text,
            )
        summary, snippets = _text_summary_and_snippets(text)
        return UploadedFileContext(
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=len(content),
            summary=summary,
            snippets=snippets,
            extracted_text=text,
        )

    if suffix == ".json" or (mime_type and "json" in mime_type.lower()):
        dataframe: pd.DataFrame | None = None
        for reader in (lambda: pd.read_json(BytesIO(content)), lambda: pd.read_json(StringIO(text))):
            try:
                candidate = reader()
            except Exception:
                continue
            if isinstance(candidate, pd.DataFrame) and _json_dataframe_is_tabular(candidate):
                dataframe = candidate
                break
        if dataframe is not None:
            context = _tabular_context(
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=len(content),
                dataframe=dataframe,
                source_format="json",
                summary_prefix="JSON file parsed as a table.",
                extracted_text=("JSON parsed as a table.\n\n" f"{dataframe.head(5).to_markdown(index=False)}")[:2000],
                extra_snippets=["JSON parsed as a table."],
            )
            return context
        summary, snippets = _text_summary_and_snippets(text)
        return UploadedFileContext(
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=len(content),
            summary=summary,
            snippets=snippets,
            extracted_text=text[:2000],
        )

    if suffix in TEXT_EXTENSIONS or suffix not in TABULAR_EXTENSIONS:
        summary, snippets = _text_summary_and_snippets(text)
        return UploadedFileContext(
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=len(content),
            summary=summary,
            snippets=snippets,
            extracted_text=text,
        )

    return None


def uploaded_contexts_to_dicts(contexts: list[UploadedFileContext]) -> list[dict]:
    return [asdict(item) for item in contexts]
