from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class RegisteredTable:
    name: str
    source_file: str
    row_count: int
    columns: list[str]
    description: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DatabaoTurnResult:
    text: str
    dataframe: pd.DataFrame | None = None
    dataframe_preview: list[dict[str, Any]] | None = None
    columns: list[str] | None = None
    row_count: int | None = None
    plot_code: str | None = None
    plot_meta: dict[str, Any] | None = None
    thread_meta: dict[str, Any] = field(default_factory=dict)
    used_databao: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "dataframe_preview": self.dataframe_preview,
            "columns": self.columns,
            "row_count": self.row_count,
            "plot_code": self.plot_code,
            "plot_meta": self.plot_meta,
            "thread_meta": self.thread_meta,
            "used_databao": self.used_databao,
            "error": self.error,
        }


@dataclass
class DatabaoSessionSnapshot:
    conversation_id: str
    llm_name: str
    executor_type: str
    registered_tables: list[RegisteredTable] = field(default_factory=list)
    context_build_error: str | None = None
    context_replayed: bool = False
    datasource_changed: bool = False
    thread_reset_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "llm_name": self.llm_name,
            "executor_type": self.executor_type,
            "registered_tables": [table.to_dict() for table in self.registered_tables],
            "context_build_error": self.context_build_error,
            "context_replayed": self.context_replayed,
            "datasource_changed": self.datasource_changed,
            "thread_reset_reason": self.thread_reset_reason,
        }
