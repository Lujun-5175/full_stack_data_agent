from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


_FABRICATED_TABLE_PREFIXES = ("simulated_", "fake_", "invented_", "synthetic_", "mock_")


@dataclass(frozen=True)
class FabricationCheckReport:
    suspicious: bool
    issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"suspicious": self.suspicious, "issues": list(self.issues)}


def _has_union_literal_row_fabrication(sql: str) -> bool:
    lowered = str(sql or "").lower()
    if "union" not in lowered:
        return False
    branches = re.split(r"\bunion(?:\s+all)?\b", sql, flags=re.IGNORECASE)
    if len(branches) < 2:
        return False
    literal_pattern = re.compile(r"^\s*(?:'[^']*'|\"[^\"]*\"|-?\d+(?:\.\d+)?)\s*(?:as\s+\w+)?\s*$", re.IGNORECASE)
    for branch in branches[1:]:
        branch_text = branch.strip()
        if not branch_text.lower().startswith("select"):
            continue
        select_clause = re.search(r"^\s*select\s+(.+?)(?:\bfrom\b|\)|$)", branch_text, flags=re.IGNORECASE | re.DOTALL)
        if select_clause is None:
            continue
        select_items = [item.strip() for item in re.split(r",(?![^(]*\))", select_clause.group(1).strip()) if item.strip()]
        if select_items and all(literal_pattern.match(item) for item in select_items):
            return True
    return False


def detect_sql_fabrication(sql: str) -> FabricationCheckReport:
    lowered = str(sql or "").lower()
    issues: list[dict[str, Any]] = []
    if any(prefix in lowered for prefix in _FABRICATED_TABLE_PREFIXES):
        issues.append({"code": "fabricated_table_prefix", "message": "SQL uses fabricated table naming patterns."})
    if "values (" in lowered:
        issues.append({"code": "values_fabrication_blocked", "message": "VALUES-based synthetic rows are blocked."})
    if _has_union_literal_row_fabrication(sql):
        issues.append({"code": "union_literal_fabrication_blocked", "message": "UNION with literal row fabrication is blocked."})
    return FabricationCheckReport(suspicious=bool(issues), issues=issues)
