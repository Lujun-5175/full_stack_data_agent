from __future__ import annotations

import json
from dataclasses import dataclass
from textwrap import dedent
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from databao.agent.visualizers.chart_contract import ChartRequest


DEEPSEEK_SYSTEM_PROMPT = dedent(
    """
    你是一个图表规格编译器，不是数据分析师，也不是代码生成器。

    你的唯一任务是：
    根据用户的画图请求、当前可用字段、字段类型、当前数据结果角色，输出一个严格的 JSON 对象，用于系统内部生成图表。

    你必须遵守以下规则：

    1. 只输出一个 JSON 对象。
    2. 不要输出 markdown。
    3. 不要输出代码块。
    4. 不要输出解释、注释、分析过程、额外文字。
    5. 不要虚构任何列名，字段名必须来自 available_columns。
    6. 如果用户明确指定了图类型、x、y、hue、方向、是否堆叠、是否百分比堆叠，必须优先遵守。
    7. 如果用户没有明确指定图类型，选择最合理、最保守的图表类型。
    8. 如果是横向柱状图，必须输出 "orientation": "horizontal"。
    9. 如果是普通竖向柱状图，输出 "orientation": "vertical"。
    10. 如果是 100% 堆叠柱状图，必须输出：
       - "stack_mode": "percent_stacked"
       - "normalize_mode": "percent_of_group"
    11. 如果是普通堆叠柱状图，输出：
       - "stack_mode": "stacked"
    12. 如果不需要堆叠，输出：
       - "stack_mode": "none"
       - "normalize_mode": "none"
    13. 如果你不完全确定，也要给出最合理猜测，但把 "confidence" 设为 "low"。
    14. 如果字段不够明确，优先依据：
       - 用户显式提到的字段
       - categorical 字段作为 x / y 分类轴
       - numeric 字段作为 y 或 value
    15. 对于 grouped bar：
       - 分类轴放在 x（或 horizontal 时放在 y）
       - 分组颜色字段放在 hue
    16. 对于 boxplot：
       - 分类字段作为 x
       - 数值字段作为 y
    17. category_order 仅在用户显式要求排序顺序，或结果本身已有清晰顺序时给出；否则返回空列表。
    18. explicit_fields 中请仅记录用户明确说出的语义约束，例如：
       - requested_kind
       - requested_x
       - requested_y
       - requested_hue
       - requested_orientation
       - requested_stack_mode

    你输出的 JSON 必须符合这个结构：

    {
      "kind": "barplot|lineplot|scatterplot|boxplot|histplot|countplot",
      "x": "列名或 null",
      "y": "列名或 null",
      "hue": "列名或 null",
      "value": "列名或 null",
      "orientation": "vertical|horizontal|null",
      "stack_mode": "none|stacked|percent_stacked|null",
      "normalize_mode": "none|percent_of_group|percent_of_total|null",
      "category_order": [],
      "show_value_labels": true,
      "explicit_fields": {},
      "confidence": "high|medium|low"
    }
    """
).strip()


RUNTIME_USER_PROMPT_TEMPLATE = dedent(
    """
    available_columns:
    {available_columns_json}

    column_types:
    {column_types_json}

    dataframe_role:
    {dataframe_role}

    sample_values:
    {sample_values_json}

    user_request:
    {user_request}

    additional_rules:
    - x, y, hue, value 必须来自 available_columns
    - 如果是 horizontal bar，请输出 orientation="horizontal"
    - 如果是 100% stacked bar，请输出 stack_mode="percent_stacked" 和 normalize_mode="percent_of_group"
    - 如果当前 dataframe_role 是 grouped / aggregated / plot_ready，请优先把它视为“已经准备好画图的数据”，不要假设还需要重新聚合
    - 如果用户明确说“横轴/纵轴/分组颜色”，请严格遵守
    - 如果用户明确说“按占比/百分比/100%堆叠”，请不要输出普通 barplot 语义
    - 只输出 JSON
    """
).strip()


@dataclass(frozen=True)
class ChartPlanningError:
    error_code: str
    message: str
    raw_response: str | None = None
    details: dict[str, Any] | None = None


def _extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        start = text.find("{", start + 1)
    return None


def _safe_json_loads(text: str) -> dict[str, Any] | None:
    candidate = _extract_first_json_object(text)
    if candidate is None:
        return None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _validate_columns(parsed: dict[str, Any], available_columns: list[str]) -> list[str]:
    available = {str(column) for column in available_columns}
    unknown: list[str] = []
    for key in ("x", "y", "hue", "value"):
        value = parsed.get(key)
        if value is None:
            continue
        if str(value) not in available:
            unknown.append(f"{key}={value}")
    return unknown


def _normalize_confidence(parsed: dict[str, Any]) -> None:
    confidence = parsed.get("confidence")
    if isinstance(confidence, (int, float)):
        numeric = float(confidence)
        if numeric >= 0.85:
            parsed["confidence"] = "high"
        elif numeric >= 0.6:
            parsed["confidence"] = "medium"
        else:
            parsed["confidence"] = "low"


def _normalize_kind(parsed: dict[str, Any]) -> None:
    if str(parsed.get("kind") or "").lower() == "histogram":
        parsed["kind"] = "histplot"


def plan_chart_request(
    *,
    user_request: str,
    available_columns: list[str],
    column_types: dict[str, str],
    dataframe_role: str | None,
    sample_values: dict[str, list[Any]] | None,
    llm_call: Callable[[list[Any]], str],
    source_result_id: str | None = None,
) -> tuple[ChartRequest | None, ChartPlanningError | None]:
    user_prompt = RUNTIME_USER_PROMPT_TEMPLATE.format(
        available_columns_json=json.dumps(available_columns, ensure_ascii=False),
        column_types_json=json.dumps(column_types, ensure_ascii=False),
        dataframe_role=str(dataframe_role or "unknown"),
        sample_values_json=json.dumps(sample_values or {}, ensure_ascii=False, default=str),
        user_request=user_request.strip(),
    )
    messages = [
        SystemMessage(content=DEEPSEEK_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]
    try:
        raw = llm_call(messages)
    except Exception as exc:
        return None, ChartPlanningError(
            error_code="planning_failed",
            message=f"LLM planner call failed: {exc}",
        )

    parsed = _safe_json_loads(raw)
    if parsed is None:
        return None, ChartPlanningError(
            error_code="schema_parse_failed",
            message="Chart planner did not return valid JSON",
            raw_response=raw,
        )

    unknown_columns = _validate_columns(parsed, available_columns)
    if unknown_columns:
        return None, ChartPlanningError(
            error_code="schema_parse_failed",
            message=f"Chart planner referenced unknown columns: {', '.join(unknown_columns)}",
            raw_response=raw,
            details={"unknown_columns": unknown_columns},
        )

    _normalize_kind(parsed)
    _normalize_confidence(parsed)
    parsed["planner_source"] = "llm_json"
    parsed["source_result_id"] = source_result_id
    parsed["source_df_role"] = parsed.get("source_df_role") or dataframe_role

    try:
        request = ChartRequest.model_validate(parsed)
    except ValidationError as exc:
        return None, ChartPlanningError(
            error_code="schema_parse_failed",
            message=f"Chart planner JSON does not match schema: {exc.errors()}",
            raw_response=raw,
        )
    return request, None
