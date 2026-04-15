from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from databao.agent.visualizers.chart_registry import canonicalize_chart_kind, chart_kind_aliases


_SORT_PATTERN = re.compile(r"(?:sort(?:ed)?\s+by|按)\s+(.+?)(?:\s+(?:asc|ascending|desc|descending|升序|降序|从高到低|从低到高))?(?=$|[，,;；])", flags=re.IGNORECASE)
_DESC_MARKERS = ("desc", "descending", "降序", "从高到低")
_ASC_MARKERS = ("asc", "ascending", "升序", "从低到高")
_ORIENTATION_MARKERS = {
    "vertical": ("vertical", "竖向", "纵向", "竖", "纵"),
    "horizontal": ("horizontal", "horizontal bar", "横向", "水平", "横"),
}
_STACK_MODE_MARKERS = {
    "percent_stacked": ("100% stacked", "100 percent stacked", "percentage stacked", "percent stacked", "100%堆叠", "百分比堆叠"),
    "stacked": ("stacked", "堆叠"),
}
_NORMALIZE_MODE_MARKERS = {
    "percent_of_group": ("percent of group", "group percentage", "百分比", "占比"),
    "percent_of_total": ("percent of total", "total percentage", "总体百分比", "总占比"),
}
_FIELD_LABELS = {
    "x": ("x", "x-axis", "横轴"),
    "y": ("y", "y-axis", "纵轴"),
    "hue": ("hue", "color", "colour", "颜色", "色彩"),
    "value": ("value", "值", "数值"),
}


def _looks_like_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(text or "")))


def _normalize_field_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]+", "_", str(value or "").strip().casefold())
    return cleaned.strip("_")


def _best_field_match(target: str, columns: list[str]) -> str | None:
    if not target:
        return None
    norm_target = _normalize_field_name(target)
    if not norm_target:
        return None
    normalized_map = {_normalize_field_name(column): column for column in columns}
    if norm_target in normalized_map:
        return normalized_map[norm_target]
    if target in columns:
        return target
    best_score = 0.0
    best_name: str | None = None
    for column in columns:
        ratio = SequenceMatcher(a=norm_target, b=_normalize_field_name(column)).ratio()
        if ratio > best_score:
            best_score = ratio
            best_name = column
    if best_score >= 0.76:
        return best_name
    return None


def _strip_field_quotes(value: str) -> str:
    return str(value or "").strip().strip("\"'`“”’‘")


def _extract_field_value(query: str, labels: tuple[str, ...]) -> str | None:
    label_pattern = "|".join(
        re.escape(label)
        for items in _FIELD_LABELS.values()
        for label in items
    )
    patterns = [
        rf"(?:{'|'.join(re.escape(label) for label in labels)})\s*(?:=|:|：|is|是)\s*(\"[^\"]+\"|'[^']+'|`[^`]+`|.+?)(?=\s+(?:and|with)\s+(?:{label_pattern})\s*(?:=|:|：)|\s+(?:group(?:ed)?\s+by|sort(?:ed)?\s+by|按)|$|[,\n;；，])",
    ]
    for pattern in patterns:
        match = re.search(pattern, query, flags=re.IGNORECASE)
        if match:
            return _strip_field_quotes(match.group(1))
    return None


def _extract_group_by(query: str) -> str | None:
    patterns = [
        r"(?:group(?:ed)?\s+by)\s*(\"[^\"]+\"|'[^']+'|`[^`]+`|[^\n,;；，]+)",
        r"按\s*(\"[^\"]+\"|'[^']+'|`[^`]+`|[^\n,;；，]+?)\s*分组",
    ]
    for pattern in patterns:
        match = re.search(pattern, query, flags=re.IGNORECASE)
        if match:
            return _strip_field_quotes(match.group(1))
    return None


def _extract_kind(query: str) -> str | None:
    lowered = str(query or "").casefold()
    best: tuple[int, str] | None = None
    for canonical, aliases in chart_kind_aliases().items():
        for alias in (canonical, *aliases):
            token = alias.casefold()
            if _looks_like_chinese(token):
                found = token in lowered
            else:
                found = re.search(r"(?<![a-z])" + re.escape(token).replace(r"\ ", r"\s+") + r"(?![a-z])", lowered) is not None
            if found and (best is None or len(token) > best[0]):
                best = (len(token), canonical)
    return None if best is None else best[1]


def _extract_marker(query: str, markers: dict[str, tuple[str, ...]]) -> str | None:
    lowered = str(query or "").casefold()
    for value, aliases in markers.items():
        for alias in aliases:
            token = alias.casefold()
            if token in lowered:
                return value
    return None


def _plan_value(chart_plan: dict[str, Any] | None, key: str) -> Any:
    if not isinstance(chart_plan, dict):
        return None
    return chart_plan.get(key)


def _spec_value(chart_spec: dict[str, Any] | None, key: str) -> Any:
    if not isinstance(chart_spec, dict):
        return None
    encoding = chart_spec.get("encoding")
    if not isinstance(encoding, dict):
        return None
    field = encoding.get(key)
    if not isinstance(field, dict):
        return None
    return field.get("field")


@dataclass
class ChartRequestContract:
    raw_query: str
    chart_requested: bool
    requested_kind: str | None = None
    requested_orientation: str | None = None
    requested_x: str | None = None
    requested_y: str | None = None
    requested_hue: str | None = None
    requested_value: str | None = None
    requested_group_by: str | None = None
    requested_stack_mode: str | None = None
    requested_normalize_mode: str | None = None
    requested_sort_by: str | None = None
    requested_sort_direction: str | None = None
    explicit_fields: list[str] = field(default_factory=list)
    required_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_query": self.raw_query,
            "chart_requested": self.chart_requested,
            "requested_kind": self.requested_kind,
            "requested_orientation": self.requested_orientation,
            "requested_x": self.requested_x,
            "requested_y": self.requested_y,
            "requested_hue": self.requested_hue,
            "requested_value": self.requested_value,
            "requested_group_by": self.requested_group_by,
            "requested_stack_mode": self.requested_stack_mode,
            "requested_normalize_mode": self.requested_normalize_mode,
            "requested_sort_by": self.requested_sort_by,
            "requested_sort_direction": self.requested_sort_direction,
            "explicit_fields": list(self.explicit_fields),
            "required_fields": list(self.required_fields),
        }


@dataclass
class ChartContractValidationResult:
    status: str
    repairable: bool
    mismatch_reasons: list[str] = field(default_factory=list)
    resolved_fields: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "repairable": self.repairable,
            "mismatch_reasons": list(self.mismatch_reasons),
            "resolved_fields": dict(self.resolved_fields),
        }


def build_chart_request_contract(query: str, *, chart_requested: bool) -> ChartRequestContract:
    contract = ChartRequestContract(raw_query=query, chart_requested=chart_requested)
    if not chart_requested:
        return contract

    query_text = str(query or "")
    lowered = query_text.casefold()
    contract.requested_kind = _extract_kind(query_text)
    contract.requested_orientation = _extract_marker(query_text, _ORIENTATION_MARKERS)
    contract.requested_stack_mode = _extract_marker(query_text, _STACK_MODE_MARKERS)
    contract.requested_normalize_mode = _extract_marker(query_text, _NORMALIZE_MODE_MARKERS)

    for attr, labels in _FIELD_LABELS.items():
        value = _extract_field_value(query_text, labels)
        if value:
            setattr(contract, f"requested_{attr}", value)
            contract.explicit_fields.append(value)

    group_by = _extract_group_by(query_text)
    if group_by:
        contract.requested_group_by = group_by
        contract.explicit_fields.append(group_by)

    sort_match = _SORT_PATTERN.search(query_text)
    if sort_match:
        contract.requested_sort_by = _strip_field_quotes(sort_match.group(1))
    if any(marker in lowered for marker in _DESC_MARKERS):
        contract.requested_sort_direction = "desc"
    elif any(marker in lowered for marker in _ASC_MARKERS):
        contract.requested_sort_direction = "asc"

    required = [
        field_name
        for field_name in (
            contract.requested_x,
            contract.requested_y,
            contract.requested_hue,
            contract.requested_value,
            contract.requested_group_by,
            contract.requested_sort_by,
        )
        if field_name
    ]
    contract.required_fields = list(dict.fromkeys(required))
    contract.explicit_fields = list(dict.fromkeys(contract.explicit_fields))
    return contract


def validate_chart_contract(
    *,
    contract: ChartRequestContract,
    dataframe_columns: list[str],
    chart_plan: dict[str, Any] | None = None,
    chart_spec: dict[str, Any] | None = None,
    artifact_purpose: str = "business_result",
) -> ChartContractValidationResult:
    if not contract.chart_requested:
        return ChartContractValidationResult(status="not_requested", repairable=False)
    if artifact_purpose != "business_result":
        return ChartContractValidationResult(
            status="mismatch",
            repairable=False,
            mismatch_reasons=["chart source is not a business_result artifact"],
        )

    plan = chart_plan if isinstance(chart_plan, dict) else chart_spec if isinstance(chart_spec, dict) else None
    reasons: list[str] = []
    resolved_fields: dict[str, str] = {}
    columns = list(dataframe_columns or [])

    for required in contract.required_fields:
        matched = _best_field_match(required, columns)
        if matched is None:
            reasons.append(f"required field '{required}' is not available in source dataframe")
        else:
            resolved_fields[required] = matched

    def _expected(actual: str | None) -> str | None:
        if actual is None:
            return None
        return resolved_fields.get(actual, actual)

    expected_kind = canonicalize_chart_kind(contract.requested_kind)
    actual_kind = canonicalize_chart_kind(_plan_value(plan, "kind"))
    if expected_kind and actual_kind and expected_kind != actual_kind:
        reasons.append(f"chart kind mismatch: expected '{expected_kind}', got '{actual_kind}'")

    expected_orientation = contract.requested_orientation
    actual_orientation = _plan_value(plan, "orientation")
    if expected_orientation and actual_orientation and str(actual_orientation) != expected_orientation:
        reasons.append(f"orientation mismatch: expected '{expected_orientation}', got '{actual_orientation}'")

    expected_stack_mode = contract.requested_stack_mode
    actual_stack_mode = _plan_value(plan, "stack_mode")
    if expected_stack_mode and actual_stack_mode and str(actual_stack_mode) != expected_stack_mode:
        reasons.append(f"stack_mode mismatch: expected '{expected_stack_mode}', got '{actual_stack_mode}'")

    expected_normalize_mode = contract.requested_normalize_mode
    actual_normalize_mode = _plan_value(plan, "normalize_mode")
    if expected_normalize_mode and actual_normalize_mode and str(actual_normalize_mode) != expected_normalize_mode:
        reasons.append(f"normalize_mode mismatch: expected '{expected_normalize_mode}', got '{actual_normalize_mode}'")

    field_pairs = (
        ("x", contract.requested_x),
        ("y", contract.requested_y),
        ("hue", contract.requested_hue),
        ("value", contract.requested_value),
        ("group_by", contract.requested_group_by),
        ("sort_by", contract.requested_sort_by),
    )
    for field_name, requested in field_pairs:
        expected_value = _expected(requested)
        actual_value = _plan_value(plan, field_name)
        if actual_value is None and field_name in {"x", "y"}:
            actual_value = _spec_value(chart_spec, field_name)
        if expected_value and actual_value and _normalize_field_name(str(actual_value)) != _normalize_field_name(expected_value):
            label = "x axis" if field_name == "x" else "y axis" if field_name == "y" else field_name
            reasons.append(f"{label} mismatch: expected '{expected_value}', got '{actual_value}'")

    expected_sort_direction = contract.requested_sort_direction
    actual_sort_direction = _plan_value(plan, "sort_direction")
    if expected_sort_direction and actual_sort_direction and str(actual_sort_direction) != expected_sort_direction:
        reasons.append(f"sort_direction mismatch: expected '{expected_sort_direction}', got '{actual_sort_direction}'")

    if reasons:
        return ChartContractValidationResult(
            status="mismatch",
            repairable=bool(resolved_fields),
            mismatch_reasons=reasons,
            resolved_fields=resolved_fields,
        )

    return ChartContractValidationResult(status="matched", repairable=False, resolved_fields=resolved_fields)


def build_controlled_repair_prompt(contract: ChartRequestContract, resolved_fields: dict[str, str]) -> str:
    is_chinese = _looks_like_chinese(contract.raw_query)

    def _resolved(value: str | None) -> str | None:
        if value is None:
            return None
        return resolved_fields.get(value, value)

    if is_chinese:
        parts = ["只能使用当前查询结果 dataframe 重新绘图。"]
        if contract.requested_kind:
            parts.append(f"图表类型必须是 {contract.requested_kind}。")
        if contract.requested_orientation:
            parts.append(f"方向必须是 {contract.requested_orientation}。")
        if _resolved(contract.requested_x):
            parts.append(f"横轴使用 {_resolved(contract.requested_x)}。")
        if _resolved(contract.requested_y):
            parts.append(f"纵轴使用 {_resolved(contract.requested_y)}。")
        if _resolved(contract.requested_hue):
            parts.append(f"颜色字段使用 {_resolved(contract.requested_hue)}。")
        if _resolved(contract.requested_value):
            parts.append(f"数值字段使用 {_resolved(contract.requested_value)}。")
        if _resolved(contract.requested_group_by):
            parts.append(f"按 {_resolved(contract.requested_group_by)} 分组。")
        if contract.requested_stack_mode:
            parts.append(f"stack_mode 必须为 {contract.requested_stack_mode}。")
        if contract.requested_normalize_mode:
            parts.append(f"normalize_mode 必须为 {contract.requested_normalize_mode}。")
        if _resolved(contract.requested_sort_by):
            direction = contract.requested_sort_direction or "desc"
            parts.append(f"按 {_resolved(contract.requested_sort_by)} {direction} 排序。")
        parts.append("不要虚构字段，也不要退化成占位的 count 图。")
        return "".join(parts)

    parts = ["Re-plot using the current query result dataframe only."]
    if contract.requested_kind:
        parts.append(f"Chart type must be {contract.requested_kind}.")
    if contract.requested_orientation:
        parts.append(f"Orientation must be {contract.requested_orientation}.")
    if _resolved(contract.requested_x):
        parts.append(f"Use x={_resolved(contract.requested_x)}.")
    if _resolved(contract.requested_y):
        parts.append(f"Use y={_resolved(contract.requested_y)}.")
    if _resolved(contract.requested_hue):
        parts.append(f"Use hue={_resolved(contract.requested_hue)}.")
    if _resolved(contract.requested_value):
        parts.append(f"Use value={_resolved(contract.requested_value)}.")
    if _resolved(contract.requested_group_by):
        parts.append(f"Group by {_resolved(contract.requested_group_by)}.")
    if contract.requested_stack_mode:
        parts.append(f"stack_mode must be {contract.requested_stack_mode}.")
    if contract.requested_normalize_mode:
        parts.append(f"normalize_mode must be {contract.requested_normalize_mode}.")
    if _resolved(contract.requested_sort_by):
        direction = contract.requested_sort_direction or "desc"
        parts.append(f"Sort by {_resolved(contract.requested_sort_by)} {direction}.")
    parts.append("Do not invent fields and do not fallback to count-style placeholder charts.")
    return " ".join(parts)
