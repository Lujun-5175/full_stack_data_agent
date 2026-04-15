from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
from typing import Any, Literal

import pandas as pd
import matplotlib
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from PIL import Image
from pydantic import Field

from databao.agent.configs.llm import LLMConfig
from databao.agent.core import ExecutionResult, VisualisationResult, Visualizer
from databao.agent.core.visualizer import HistoryMode
from databao.agent.executors.llm import call_model_with_retry
from databao.agent.visualizers.chart_contract import ChartPlan
from databao.agent.visualizers.chart_planner import ChartPlanningError, plan_chart_request
from databao.agent.visualizers.chart_registry import (
    canonicalize_chart_kind,
    chart_kind_aliases,
    supported_chart_kinds,
)
from full_stack_data_agent.utils.json_extract import extract_first_json_object
from full_stack_data_agent.utils.pandas_types import is_categorical_dtype, is_datetime64tz_dtype

logger = logging.getLogger(__name__)

_MATPLOTLIB_BACKEND_READY = False


def _ensure_matplotlib_backend() -> None:
    global _MATPLOTLIB_BACKEND_READY
    if _MATPLOTLIB_BACKEND_READY:
        return
    if "MPLBACKEND" not in os.environ:
        matplotlib.use("Agg", force=False)
    _MATPLOTLIB_BACKEND_READY = True


from matplotlib import pyplot as plt


try:  # optional dependency
    import seaborn as sns
except (ImportError, ModuleNotFoundError, OSError):  # pragma: no cover
    sns = None


_CHART_KINDS = supported_chart_kinds()
_CHART_KIND_ALIASES = chart_kind_aliases()

_REQUEST_FIELD_LABELS = {
    "x": ("x", "x-axis", "横轴"),
    "y": ("y", "y-axis", "纵轴"),
    "hue": ("hue", "color", "colour", "颜色", "色彩"),
    "value": ("value", "数值", "值"),
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _instruction_request_slice(request: str) -> str:
    match = re.search(r"Instructions:\s*(.*)$", request, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return request.strip()


def _is_dt(series: pd.Series) -> bool:
    return pd.api.types.is_datetime64_any_dtype(series) or is_datetime64tz_dtype(series)


def _is_cat(series: pd.Series) -> bool:
    return bool(
        pd.api.types.is_object_dtype(series)
        or pd.api.types.is_string_dtype(series)
        or is_categorical_dtype(series)
        or pd.api.types.is_bool_dtype(series)
    )


def _plot_like(obj: Any) -> Figure | Axes | Any | None:
    if obj is None:
        return None
    if isinstance(obj, (Figure, Axes)):
        return obj
    for attr in ("fig", "figure"):
        candidate = getattr(obj, attr, None)
        if candidate is not None and hasattr(candidate, "savefig"):
            return candidate
    if hasattr(obj, "savefig"):
        return obj
    return None


def _fig_png_bytes(fig: Any) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=144)
    return buf.getvalue()


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if text is not None:
                    parts.append(str(text))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def _safe_json_loads(text: str) -> dict[str, Any] | None:
    return extract_first_json_object(text)


def _make_unique_column_labels(columns: list[Any]) -> list[Any]:
    seen: dict[Any, int] = {}
    used: set[Any] = set()
    unique: list[Any] = []
    for column in columns:
        count = seen.get(column, 0)
        if count == 0 and column not in used:
            unique.append(column)
            seen[column] = 1
            used.add(column)
            continue

        suffix = count
        candidate: Any = f"{column}__dup{suffix}"
        while candidate in used:
            suffix += 1
            candidate = f"{column}__dup{suffix}"
        unique.append(candidate)
        seen[column] = count + 1
        used.add(candidate)
    return unique


def _dedupe_dataframe_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    if df.columns.is_unique:
        return df, {"duplicate_columns": [], "renamed_columns": {}}

    duplicate_columns: list[str] = []
    seen: set[Any] = set()
    for column in df.columns:
        if column in seen and str(column) not in duplicate_columns:
            duplicate_columns.append(str(column))
        seen.add(column)

    deduped = df.copy()
    deduped.columns = _make_unique_column_labels(list(deduped.columns))
    renamed_columns = {
        str(original): str(new)
        for original, new in zip(df.columns, deduped.columns, strict=False)
        if original != new
    }
    return deduped, {"duplicate_columns": duplicate_columns, "renamed_columns": renamed_columns}


class SeabornChatResult(VisualisationResult):
    backend: Literal["seaborn"] = "seaborn"
    kind: str | None = None
    dataframe: pd.DataFrame | None = None
    plot_config: dict[str, Any] = Field(default_factory=dict)
    chart_plan: dict[str, Any] = Field(default_factory=dict)

    def figure(self) -> Any | None:
        return _plot_like(self.plot)

    def png_bytes(self) -> bytes | None:
        plot = self.figure()
        if plot is None:
            return None
        if isinstance(plot, Axes):
            plot = plot.figure
        return _fig_png_bytes(plot)

    def png_base64(self) -> str | None:
        png = self.png_bytes()
        return None if png is None else base64.b64encode(png).decode("utf-8")

    def image(self) -> Image.Image | None:
        png = self.png_bytes()
        if png is None:
            return None
        image = Image.open(io.BytesIO(png))
        image.load()
        return image

    def _repr_mimebundle_(self, include: Any = None, exclude: Any = None) -> Any:
        png = self.png_bytes()
        if png is None:
            return None
        return {"image/png": png, "text/plain": self.text}


class SeabornChatVisualizer(Visualizer):
    def __init__(
        self,
        llm_config: LLMConfig | None = None,
        *,
        history_mode: HistoryMode = HistoryMode.LAST_QUESTION,
        allow_semantic_fallback_for_explicit_requests: bool = False,
    ):
        super().__init__(history_mode=history_mode)
        self._llm_config = llm_config
        self._allow_semantic_fallback_for_explicit_requests = allow_semantic_fallback_for_explicit_requests

    def _call_chart_planner(self, messages: list[Any]) -> str:
        if self._llm_config is None:
            raise RuntimeError("No LLM config available for chart planning")
        temperature = min(float(self._llm_config.temperature), 0.2)
        model = self._llm_config.model_copy(update={"temperature": temperature}).new_chat_model()
        response = call_model_with_retry(model, messages)
        return _message_text(response)

    @staticmethod
    def _prepare_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
        return _dedupe_dataframe_columns(df)

    @staticmethod
    def _planner_sample_values(df: pd.DataFrame, limit: int = 5) -> dict[str, list[Any]]:
        sample: dict[str, list[Any]] = {}
        for column in df.columns:
            series = df[column].dropna().head(limit)
            sample[str(column)] = [str(value) for value in series.tolist()]
        return sample

    @staticmethod
    def _column_types(df: pd.DataFrame) -> dict[str, str]:
        return {str(column): str(df[column].dtype) for column in df.columns}

    @staticmethod
    def _detect_requested_kind(request: str) -> str | None:
        lowered = _clean(_instruction_request_slice(request))
        for kind, aliases in _CHART_KIND_ALIASES.items():
            for alias in aliases:
                alias_lower = alias.casefold()
                if re.search(r"[\u4e00-\u9fff]", alias_lower):
                    if alias_lower in lowered:
                        return kind
                    continue
                pattern = r"(?<![a-z])" + re.escape(alias_lower).replace(r"\ ", r"\s+") + r"(?![a-z])"
                if re.search(pattern, lowered):
                    return str(canonicalize_chart_kind(kind))
        return None

    @staticmethod
    def _looks_like_grouped_hue_request(request: str) -> bool:
        lowered = _clean(request)
        markers = (
            "grouped bar",
            "grouped bar chart",
            "color by",
            "group by color",
            "hue",
            "分组颜色",
            "按颜色分组",
            "按某字段分组着色",
            "分组柱状图",
        )
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _resolve_column_name(requested: str | None, df: pd.DataFrame) -> str | None:
        if requested is None:
            return None
        candidate = requested.strip().strip("\"'`“”’")
        if not candidate:
            return None
        if candidate in df.columns:
            return str(candidate)
        lowered = candidate.casefold()
        for column in df.columns:
            if str(column).casefold() == lowered:
                return str(column)
        compact = re.sub(r"[\s_]+", "", lowered)
        for column in df.columns:
            if re.sub(r"[\s_]+", "", str(column).casefold()) == compact:
                return str(column)
        return None

    @staticmethod
    def _looks_like_horizontal_bar_request(request: str) -> bool:
        lowered = _clean(_instruction_request_slice(request))
        markers = (
            "horizontal bar",
            "horizontal bars",
            "barh",
            "横向柱状图",
            "水平柱状图",
            "横向条形图",
        )
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _extract_category_order(request: str, df: pd.DataFrame) -> list[str]:
        request_slice = _instruction_request_slice(request)
        match = re.search(
            r"(?:category[_\s-]*order|order|排序)\s*(?:=|:|：)\s*([^\n]+)",
            request_slice,
            flags=re.IGNORECASE,
        )
        if not match:
            return []
        raw = match.group(1).strip()
        values = [item.strip().strip("\"'`") for item in re.split(r"[>,，,;；]", raw) if item.strip()]
        normalized: list[str] = []
        seen: set[str] = set()
        # pandas>=3.0 removed stack(dropna=...) on DataFrame; use default stack behavior.
        flattened_values = df.astype("string").stack().tolist()
        for value in values:
            for column_value in flattened_values:
                candidate = str(column_value).strip()
                if candidate.casefold() == value.casefold() and candidate.casefold() not in seen:
                    normalized.append(candidate)
                    seen.add(candidate.casefold())
                    break
            else:
                if value.casefold() not in seen:
                    normalized.append(value)
                    seen.add(value.casefold())
        return normalized

    def _extract_explicit_field_constraints(self, request: str, df: pd.DataFrame) -> dict[str, Any]:
        request_slice = _instruction_request_slice(request)
        constraints: dict[str, Any] = {}
        explicit_kind = self._detect_requested_kind(request_slice)
        if explicit_kind is not None:
            constraints["kind"] = explicit_kind
        patterns = {
            "kind": r"(?:chart\s*type|chart|kind|plot\s*type|图类型)\s*(?:=|:|：|is|是)\s*([^\n,;，；]+)",
            "x": r"(?:x|x-axis|横轴)\s*(?:=|:|：|is|是)\s*([^\n,;，；]+)",
            "y": r"(?:y|y-axis|纵轴)\s*(?:=|:|：|is|是)\s*([^\n,;，；]+)",
            "hue": r"(?:hue|grouped\s*color|group\s*color|color|colour|分组颜色|颜色|色彩)\s*(?:=|:|：|is|是)\s*([^\n,;，；]+)",
            "value": r"(?:value|数值|值)\s*(?:=|:|：|is|是)\s*([^\n,;，；]+)",
        }
        for field_name, pattern in patterns.items():
            match = re.search(pattern, request_slice, flags=re.IGNORECASE)
            if match:
                if field_name == "kind":
                    detected_kind = self._detect_requested_kind(match.group(1))
                    if detected_kind is not None:
                        constraints["kind"] = detected_kind
                    continue
                resolved = self._resolve_column_name(match.group(1), df)
                if resolved is not None:
                    constraints[field_name] = resolved
        orientation_match = re.search(
            r"(?:orientation|方向)\s*(?:=|:|：)\s*(horizontal|vertical|横向|纵向|水平)",
            request_slice,
            flags=re.IGNORECASE,
        )
        if orientation_match:
            orientation_value = orientation_match.group(1).lower()
            if orientation_value in {"horizontal", "横向", "水平"}:
                constraints["orientation"] = "horizontal"
            elif orientation_value in {"vertical", "纵向"}:
                constraints["orientation"] = "vertical"
        elif self._looks_like_horizontal_bar_request(request_slice):
            constraints["orientation"] = "horizontal"
        lowered = _clean(request_slice)
        percent_stacked_pattern = (
            r"(100%\s*stack(?:ed)?|percent(?:age)?\s*stack(?:ed)?|100%\s*堆叠|100\s*％\s*堆叠|百分比\s*堆叠)"
        )
        plain_stacked_pattern = r"(stacked|堆叠)"
        negated_percent_pattern = (
            r"((不是|非|not|don't|do not)\s*(100%\s*stack(?:ed)?|percent(?:age)?\s*stack(?:ed)?|100%\s*堆叠|100\s*％\s*堆叠|百分比\s*堆叠))"
        )
        has_percent_stacked = re.search(percent_stacked_pattern, lowered, flags=re.IGNORECASE) is not None
        has_negated_percent_stacked = re.search(negated_percent_pattern, lowered, flags=re.IGNORECASE) is not None
        lowered_without_negated_percent = re.sub(negated_percent_pattern, "", lowered, flags=re.IGNORECASE)
        has_plain_stacked = re.search(plain_stacked_pattern, lowered_without_negated_percent, flags=re.IGNORECASE) is not None

        if has_percent_stacked and not has_negated_percent_stacked:
            constraints["stack_mode"] = "percent_stacked"
            constraints["normalize_mode"] = "percent_of_group"
        elif has_plain_stacked:
            constraints["stack_mode"] = "stacked"
            constraints["normalize_mode"] = "none"
        category_order = self._extract_category_order(request_slice, df)
        if category_order:
            constraints["category_order"] = category_order
        if self._looks_like_value_label_request(request_slice):
            constraints["show_value_labels"] = True
        return constraints

    @staticmethod
    def _looks_like_value_label_request(request: str) -> bool:
        lowered = _clean(_instruction_request_slice(request))
        markers = (
            "每个柱子上显示人数",
            "每个柱子显示人数",
            "柱子上显示人数",
            "柱上显示人数",
            "显示柱子人数",
            "显示柱子数值",
            "显示数值标签",
            "value labels",
            "bar labels",
            "show value labels",
            "show labels on bars",
            "annotate bars",
            "bar annotations",
        )
        return any(marker in lowered for marker in markers)

    def _is_explicit_chart_request(self, request: str, df: pd.DataFrame) -> bool:
        constraints = self._extract_explicit_field_constraints(request, df)
        if "kind" in constraints:
            return True
        return any(
            field in constraints
            for field in ("x", "y", "hue", "value", "orientation", "stack_mode", "normalize_mode", "category_order")
        )

    def _build_chart_plan(
        self,
        request: str,
        df: pd.DataFrame,
        profile: dict[str, list[str]],
        *,
        dataframe_role: str | None = None,
        source_result_id: str | None = None,
    ) -> tuple[ChartPlan | None, ChartPlanningError | None]:
        available_columns = [str(column) for column in df.columns]
        planned, planning_error = plan_chart_request(
            user_request=_instruction_request_slice(request),
            available_columns=available_columns,
            column_types=self._column_types(df),
            dataframe_role=dataframe_role or "plot_ready",
            sample_values=self._planner_sample_values(df),
            llm_call=self._call_chart_planner,
            source_result_id=source_result_id,
        )
        return planned, planning_error

    def _validate_chart_plan(
        self,
        request: str,
        df: pd.DataFrame,
        plan: ChartPlan,
        *,
        explicit_fields: dict[str, Any] | None = None,
    ) -> list[str]:
        errors: list[str] = []
        profile = self._profile(df)
        explicit_fields = dict(explicit_fields or self._extract_explicit_field_constraints(request, df))
        explicit_kind = explicit_fields.get("kind")
        lowered_request = _clean(_instruction_request_slice(request))
        numeric_columns = set(profile["numeric"])
        categorical_columns = set(profile["categorical"] + profile["boolean"])
        allowed_contract_kinds = set(supported_chart_kinds())

        if plan.kind not in allowed_contract_kinds:
            errors.append(f"Invalid chart kind: {plan.kind}")
        if explicit_kind is not None and plan.kind != explicit_kind:
            errors.append(f"User explicitly requested {explicit_kind}, but plan chose {plan.kind}.")

        referenced = [value for value in [plan.x, plan.y, plan.hue, plan.value, *plan.variables] if value is not None]
        missing = [column for column in referenced if column not in df.columns]
        if missing:
            errors.append(f"Referenced columns do not exist: {', '.join(sorted(set(missing)))}")

        if explicit_fields:
            for field_name, expected_column in explicit_fields.items():
                if field_name in {"kind", "show_value_labels"}:
                    continue
                actual_column = getattr(plan, field_name)
                if actual_column is None:
                    errors.append(f"User explicitly requested {field_name}={expected_column}, but plan omitted it.")
                elif actual_column != expected_column:
                    errors.append(
                        f"User explicitly requested {field_name}={expected_column}, but plan used {actual_column}."
                    )

        if "hue" in explicit_fields and not plan.hue:
            errors.append(f"User explicitly requested hue={explicit_fields['hue']}, but plan omitted it.")
        if explicit_kind == "barplot" and "hue" in explicit_fields and not plan.hue:
            errors.append("Grouped bar chart contract requires hue, but plan omitted hue.")
        if self._looks_like_grouped_hue_request(request) and "hue" in explicit_fields and not plan.hue:
            errors.append("Request explicitly asked for grouped/color-by bars, but plan omitted hue.")
        if plan.kind == "barplot" and plan.hue is not None and plan.hue not in categorical_columns:
            errors.append("barplot hue should be categorical.")

        def _ensure_numeric(column_name: str | None, *, label: str) -> None:
            if column_name is None:
                return
            if column_name not in numeric_columns:
                errors.append(f"{label} must be numeric, but {column_name} is not numeric.")

        def _ensure_low_cardinality(column_name: str | None, *, label: str) -> None:
            if column_name is None or column_name not in df.columns:
                return
            series = df[column_name]
            if pd.api.types.is_numeric_dtype(series) and series.nunique(dropna=True) > max(8, len(df) // 5 or 1):
                errors.append(f"{label} should be categorical or low-cardinality, but {column_name} looks high-cardinality.")

        def _is_categorical_or_low_cardinality(column_name: str | None) -> bool:
            if column_name is None or column_name not in df.columns:
                return False
            if column_name in categorical_columns:
                return True
            series = df[column_name]
            if not pd.api.types.is_numeric_dtype(series):
                return True
            return bool(series.nunique(dropna=True) <= max(8, len(df) // 5 or 1))

        if plan.kind == "scatterplot":
            _ensure_numeric(plan.x, label="x")
            _ensure_numeric(plan.y, label="y")
        elif plan.kind == "lineplot":
            _ensure_numeric(plan.y, label="y")
            if plan.x is not None and plan.x in df.columns:
                series = df[plan.x]
                if not (
                    pd.api.types.is_numeric_dtype(series)
                    or _is_dt(series)
                    or plan.x in categorical_columns
                ):
                    errors.append(f"x should be numeric, datetime, or categorical for lineplot, but {plan.x} is not.")
        elif plan.kind == "barplot":
            orientation = plan.orientation or ("horizontal" if explicit_fields.get("orientation") == "horizontal" else "vertical")
            stack_mode = plan.stack_mode or explicit_fields.get("stack_mode") or "none"
            value_column = plan.value or plan.y

            if orientation not in {"vertical", "horizontal"}:
                errors.append(f"barplot orientation must be vertical or horizontal, but got {orientation}.")

            if stack_mode in {"stacked", "percent_stacked"}:
                if plan.hue is None:
                    errors.append(f"{stack_mode} barplot requires hue.")
                if value_column is None:
                    errors.append(f"{stack_mode} barplot requires a numeric value/y column.")
                else:
                    _ensure_numeric(value_column, label="value")
                if plan.x is None:
                    errors.append(f"{stack_mode} barplot requires x.")
                elif not _is_categorical_or_low_cardinality(plan.x):
                    errors.append(f"{stack_mode} barplot x should be categorical or low-cardinality.")
                if plan.hue is not None and not _is_categorical_or_low_cardinality(plan.hue):
                    errors.append(f"{stack_mode} barplot hue should be categorical or low-cardinality.")
                if stack_mode == "percent_stacked":
                    percent_marked = plan.normalize_mode in {"percent_of_group", "percent_of_total"} or any(
                        token in lowered_request for token in ("percent", "100%", "百分比")
                    )
                    if not percent_marked:
                        errors.append(
                            "percent_stacked barplot requires explicit percentage/normalization semantics."
                        )
                    if plan.source_df_role not in {"aggregated", "plot_ready"}:
                        errors.append("percent_stacked barplot requires source_df_role in {aggregated, plot_ready}.")
                    if value_column is not None and value_column in df.columns:
                        value_series = pd.to_numeric(df[value_column], errors="coerce").dropna()
                        if not value_series.empty:
                            max_value = float(value_series.max())
                            min_value = float(value_series.min())
                            if min_value < 0.0 or max_value > 100.0:
                                errors.append(
                                    "percent_stacked barplot requires percentage-like values in [0, 100]."
                                )
                if orientation == "horizontal" and value_column is not None and plan.y is None:
                    errors.append("horizontal stacked barplot requires y categorical axis.")
            else:
                if orientation == "horizontal":
                    if plan.x is None:
                        errors.append("horizontal barplot requires x.")
                    else:
                        _ensure_numeric(plan.x, label="x")
                    if plan.y is None:
                        errors.append("horizontal barplot requires y.")
                    elif not _is_categorical_or_low_cardinality(plan.y):
                        errors.append("horizontal barplot y should be categorical or low-cardinality.")
                else:
                    if plan.x is None:
                        errors.append("vertical barplot requires x.")
                    elif not _is_categorical_or_low_cardinality(plan.x):
                        errors.append("vertical barplot x should be categorical or low-cardinality.")
                    if plan.y is not None:
                        _ensure_numeric(plan.y, label="y")
        elif plan.kind == "histogram":
            if plan.x is None:
                errors.append("histogram requires x.")
            if plan.y is not None:
                errors.append("histogram should not specify y.")
            if plan.x is not None and plan.x in df.columns:
                series = df[plan.x]
                if not (
                    pd.api.types.is_numeric_dtype(series)
                    or _is_dt(series)
                    or pd.api.types.is_bool_dtype(series)
                    or pd.api.types.is_categorical_dtype(series)
                    or pd.api.types.is_string_dtype(series)
                ):
                    errors.append(f"histogram x should be a single numeric/categorical column, but {plan.x} is not suitable.")
        elif plan.kind == "countplot":
            if plan.x is None:
                errors.append("countplot requires x.")
            _ensure_low_cardinality(plan.x, label="countplot x")
        elif plan.kind == "boxplot":
            _ensure_numeric(plan.y, label="y")
            if plan.x is None:
                errors.append("boxplot requires x.")
            _ensure_low_cardinality(plan.x, label="boxplot x")
        if plan.confidence is not None and plan.confidence not in {"high", "medium", "low"}:
            errors.append("confidence must be one of high|medium|low when provided.")

        return errors

    def _normalize_chart_plan(
        self,
        request: str,
        df: pd.DataFrame,
        plan: ChartPlan,
        *,
        explicit_fields: dict[str, Any] | None = None,
    ) -> ChartPlan:
        updates: dict[str, Any] = {}
        if plan.title is None:
            updates["title"] = self._default_chart_title(plan.kind)
        explicit_fields = dict(explicit_fields or self._extract_explicit_field_constraints(request, df))
        if explicit_fields.get("show_value_labels") and plan.show_value_labels is not True:
            updates["show_value_labels"] = True
        if plan.category_order == [] and explicit_fields.get("category_order"):
            updates["category_order"] = list(explicit_fields["category_order"])
        merged_explicit = dict(explicit_fields)
        if plan.explicit_fields:
            merged_explicit = {**merged_explicit, **plan.explicit_fields}
        updates["explicit_fields"] = merged_explicit
        if plan.planner_source is None:
            updates["planner_source"] = "llm_json"
        return plan.model_copy(update=updates) if updates else plan

    @staticmethod
    def _default_chart_title(kind: str) -> str:
        return kind.replace("plot", " plot").title()

    def _plan_chart(
        self,
        request: str,
        df: pd.DataFrame,
        profile: dict[str, list[str]],
        *,
        explicit_fields: dict[str, Any] | None = None,
    ) -> tuple[ChartPlan | None, dict[str, Any]]:
        debug: dict[str, Any] = {
            "planner": "llm_json",
            "planner_status": "planning_failed",
            "validated": False,
            "planner_error": None,
            "validation_errors": [],
            "render_error": None,
            "raw_planner_response": None,
            "parsed_plan": None,
            "final_plan": None,
            "fallback_blocked": False,
            "fallback_reason": None,
            "explicit_contract_kind": None,
            "explicit_fields": None,
            "parsed_plan_kind": None,
            "planner_source": "llm_json",
            "schema_error": None,
        }
        if self._llm_config is None:
            debug["planner_error"] = "No llm_config available"
            return None, debug
        explicit_contract = dict(explicit_fields or self._extract_explicit_field_constraints(request, df))
        explicit_kind = explicit_contract.get("kind")
        debug["explicit_contract_kind"] = explicit_kind
        debug["explicit_fields"] = dict(explicit_contract)

        plan, planning_error = self._build_chart_plan(
            request,
            df,
            profile,
            dataframe_role="plot_ready",
            source_result_id=None,
        )
        if planning_error is not None:
            debug["planner_status"] = planning_error.error_code
            debug["planner_error"] = planning_error.message
            debug["raw_planner_response"] = planning_error.raw_response
            debug["schema_error"] = planning_error.details
            return None, debug
        if plan is None:
            debug["planner_status"] = "planning_failed"
            debug["planner_error"] = "Planner returned no plan"
            return None, debug

        debug["parsed_plan"] = plan.model_dump(mode="json")
        debug["parsed_plan_kind"] = plan.kind

        try:
            validation_errors = self._validate_chart_plan(request, df, plan, explicit_fields=explicit_contract)
        except TypeError:
            validation_errors = self._validate_chart_plan(request, df, plan)
        if validation_errors:
            debug["planner_status"] = "validation_failed"
            debug["validation_errors"] = list(validation_errors)
            debug["planner_error"] = "chart plan failed validation"
            return None, debug

        plan = self._normalize_chart_plan(request, df, plan, explicit_fields=explicit_contract)
        try:
            validation_errors = self._validate_chart_plan(request, df, plan, explicit_fields=explicit_contract)
        except TypeError:
            validation_errors = self._validate_chart_plan(request, df, plan)
        if validation_errors:
            debug["planner_error"] = "final plan failed validation"
            debug["validation_errors"] = validation_errors
            debug["planner_status"] = "validation_failed"
            return None, debug

        debug.update(
            {
                "planner_status": "ready",
                "validated": True,
                "confidence": plan.confidence,
                "reason": plan.reason,
                "final_plan": plan.model_dump(mode="json"),
            }
        )
        return plan, debug

    def _profile(self, df: pd.DataFrame) -> dict[str, list[str]]:
        cols = {
            "numeric": [str(c) for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])],
            "datetime": [str(c) for c in df.columns if _is_dt(df[c])],
            "boolean": [str(c) for c in df.columns if pd.api.types.is_bool_dtype(df[c])],
        }
        cols["categorical"] = [str(c) for c in df.columns if c not in cols["numeric"] + cols["datetime"] + cols["boolean"] and _is_cat(df[c])]
        return cols

    def _intent(self, request: str) -> str | None:
        lowered = _clean(request)
        for kind, keywords in (
            ("heatmap", ("heatmap", "correlation matrix", "corr matrix", "corr", "matrix")),
            ("pairplot", ("pairplot", "pair plot", "pairwise", "scatter matrix")),
            ("jointplot", ("jointplot", "joint plot", "joint", "bivariate")),
            ("violinplot", ("violinplot", "violin", "violin plot")),
            ("swarmplot", ("swarmplot", "swarm plot", "swarm")),
            ("stripplot", ("stripplot", "strip plot", "strip", "jitter")),
            ("boxplot", ("box", "outlier", "quartile")),
            ("scatterplot", ("scatter", "relationship", "correlation")),
            ("lineplot", ("line", "trend", "time series", "over time")),
            ("barplot", ("bar", "compare", "comparison", "aggregate")),
            ("countplot", ("countplot", "count plot", "count", "frequency")),
            ("histogram", ("hist", "histogram", "distribution")),
        ):
            if any(keyword in lowered for keyword in keywords):
                return kind
        return None

    def _choose_kind(self, request: str, profile: dict[str, list[str]]) -> str | None:
        intent = self._intent(request)
        if intent:
            return intent
        if profile["datetime"] and profile["numeric"]:
            return "lineplot"
        if len(profile["numeric"]) == 2 and not profile["categorical"]:
            return "jointplot"
        if len(profile["numeric"]) >= 3 and not profile["categorical"]:
            return "heatmap"
        if len(profile["numeric"]) >= 2:
            return "scatterplot"
        if profile["categorical"] and profile["numeric"]:
            return "barplot"
        if profile["categorical"]:
            return "countplot"
        if profile["numeric"]:
            return "histogram"
        if profile["datetime"]:
            return "lineplot"
        return None

    def _pick_columns(self, kind: str | None, profile: dict[str, list[str]]) -> dict[str, Any]:
        x = y = hue = None
        variables: list[str] = []
        if kind == "histogram":
            x = next((profile[k][0] for k in ("numeric", "datetime", "categorical") if profile[k]), None)
        elif kind == "countplot":
            x = next((profile[k][0] for k in ("categorical", "boolean", "datetime") if profile[k]), None)
        elif kind == "barplot":
            x = next((profile[k][0] for k in ("categorical", "boolean", "datetime") if profile[k]), None)
            y = profile["numeric"][0] if profile["numeric"] else None
        elif kind == "lineplot":
            x = next((profile[k][0] for k in ("datetime", "numeric", "categorical") if profile[k]), None)
            y = profile["numeric"][0] if profile["numeric"] else None
        elif kind == "scatterplot":
            x = profile["numeric"][0] if profile["numeric"] else None
            y = profile["numeric"][1] if len(profile["numeric"]) > 1 else None
            hue = profile["categorical"][0] if profile["categorical"] else None
        elif kind == "boxplot":
            x = profile["categorical"][0] if profile["categorical"] else None
            y = profile["numeric"][0] if profile["numeric"] else None
        elif kind in {"violinplot", "swarmplot", "stripplot"}:
            x = next((profile[k][0] for k in ("categorical", "boolean", "datetime") if profile[k]), None)
            y = profile["numeric"][0] if profile["numeric"] else None
            hue = profile["categorical"][1] if len(profile["categorical"]) > 1 else None
        elif kind == "jointplot":
            x = profile["numeric"][0] if profile["numeric"] else None
            y = profile["numeric"][1] if len(profile["numeric"]) > 1 else None
            hue = profile["categorical"][0] if profile["categorical"] else None
        elif kind == "pairplot":
            variables = profile["numeric"][:6]
            hue = profile["categorical"][0] if profile["categorical"] else None
        elif kind == "heatmap":
            variables = profile["numeric"][:12]
        return {"x": x, "y": y, "hue": hue, "variables": variables}

    def _render(self, kind: str, df: pd.DataFrame, columns: dict[str, Any]) -> Any:
        _ensure_matplotlib_backend()
        if sns is not None:
            sns.set_theme(style="whitegrid")
        self._apply_cjk_font_defaults()
        fig: Figure | None = None
        ax: Axes | None = None
        x, y, hue = columns["x"], columns["y"], columns["hue"]
        value = columns.get("value")
        variables = [str(column) for column in columns.get("variables", []) if column in df.columns]
        title = str(columns.get("title") or self._default_chart_title(kind))
        show_value_labels = bool(columns.get("show_value_labels"))
        if kind in {"histogram", "histplot"}:
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if x is None:
                raise ValueError("No suitable column found for histplot")
            if pd.api.types.is_bool_dtype(df[x]):
                raise ValueError("histplot does not support boolean x columns without explicit conversion")
            if sns is not None:
                sns.histplot(data=df, x=x, bins=min(30, max(5, len(df) // 10 or 10)), kde=False, ax=ax)
            else:
                ax.hist(df[x].dropna(), bins=min(30, max(5, len(df) // 10 or 10)), color="#4C72B0")
            ax.set_ylabel("count")
        elif kind == "countplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if x is None:
                raise ValueError("No suitable column found for countplot")
            if sns is not None:
                sns.countplot(data=df, x=x, hue=hue if hue and hue != x else None, ax=ax)
            else:
                df[x].astype("string").value_counts(dropna=False).plot(kind="bar", ax=ax, color="#4C72B0")
            ax.set_ylabel("count")
            if show_value_labels:
                self._annotate_bar_labels(ax)
        elif kind == "barplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            orientation = str(columns.get("orientation") or "vertical").lower()
            stack_mode = str(columns.get("stack_mode") or "none").lower()
            category_order = [str(item) for item in (columns.get("category_order") or [])]
            if orientation not in {"vertical", "horizontal"}:
                orientation = "vertical"
            if stack_mode in {"stacked", "percent_stacked"}:
                value_column = str(value or y or "")
                if not x or not hue or not value_column:
                    raise ValueError(f"{stack_mode} barplot requires x, hue, and value/y columns")
                if value_column not in df.columns:
                    raise ValueError(f"{stack_mode} barplot value column {value_column} not found")
                plot_cols = [x, hue, value_column]
                data = df[plot_cols].dropna()
                if category_order:
                    data[x] = pd.Categorical(data[x], categories=category_order, ordered=True)
                pivot = data.pivot_table(index=x, columns=hue, values=value_column, aggfunc="sum", fill_value=0)
                if category_order:
                    pivot = pivot.reindex(category_order)
                pivot = pivot.fillna(0)
                palette = sns.color_palette(n_colors=len(pivot.columns)) if sns is not None else None
                cumulative = pd.Series(0.0, index=pivot.index)
                for idx, hue_value in enumerate(pivot.columns):
                    segment = pivot[hue_value].astype(float)
                    color = palette[idx] if palette is not None else None
                    if orientation == "horizontal":
                        bars = ax.barh(pivot.index.astype(str), segment, left=cumulative, label=str(hue_value), color=color)
                    else:
                        bars = ax.bar(pivot.index.astype(str), segment, bottom=cumulative, label=str(hue_value), color=color)
                    if show_value_labels and stack_mode == "percent_stacked":
                        for bar in bars:
                            label_value = bar.get_width() if orientation == "horizontal" else bar.get_height()
                            if float(label_value) <= 0:
                                continue
                            text_x, text_y = self._label_position(bar, orientation=orientation)
                            ax.text(text_x, text_y, f"{float(label_value):g}%", ha="center", va="center", fontsize=8, color="white")
                    cumulative = cumulative + segment
                ax.legend(title=str(hue))
                columns["hue_used"] = True
                columns["stack_mode_used"] = stack_mode
                if show_value_labels and stack_mode == "percent_stacked":
                    columns["value_labels_rendered"] = True
                    columns["annotation_mode"] = "percent_stacked_segment_labels"
                elif show_value_labels:
                    self._annotate_bar_labels(ax, orientation=orientation)
                    columns["value_labels_rendered"] = True
                    columns["annotation_mode"] = "stacked_top_labels"
                else:
                    columns["value_labels_rendered"] = False
                    columns["annotation_mode"] = None
            else:
                if x is None and orientation == "vertical":
                    raise ValueError("No suitable column found for barplot")
                if y is None:
                    category_col = y if orientation == "horizontal" else x
                    if category_col is None:
                        raise ValueError("count-style barplot requires a categorical axis")
                    if sns is not None:
                        if orientation == "horizontal":
                            sns.countplot(data=df, y=category_col, hue=hue if hue and hue != category_col else None, ax=ax)
                        else:
                            sns.countplot(data=df, x=category_col, hue=hue if hue and hue != category_col else None, ax=ax)
                    else:
                        kind_name = "barh" if orientation == "horizontal" else "bar"
                        df[category_col].astype("string").value_counts(dropna=False).plot(kind=kind_name, ax=ax, color="#4C72B0")
                else:
                    if orientation == "horizontal":
                        if x is None:
                            raise ValueError("horizontal barplot requires numeric x")
                        if y is None:
                            raise ValueError("horizontal barplot requires categorical y")
                        plot_columns = [x, y]
                        if hue and hue not in {x, y} and hue in df.columns:
                            plot_columns.append(hue)
                        data = df[plot_columns].dropna()
                        if category_order:
                            data[y] = pd.Categorical(data[y], categories=category_order, ordered=True)
                        if sns is not None:
                            sns.barplot(data=data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, ax=ax, errorbar=None)
                        else:
                            group_keys = [y] + ([hue] if hue and hue not in {x, y} and hue in data.columns else [])
                            grouped = data.groupby(group_keys, dropna=False)[x].mean()
                            grouped.unstack(hue).plot(kind="barh", ax=ax) if len(group_keys) == 2 else grouped.plot(kind="barh", ax=ax)
                    else:
                        data = df[[x, y]].dropna()
                        if hue and hue not in {x, y} and hue in df.columns:
                            data = df[[x, y, hue]].dropna()
                        if category_order and x is not None:
                            data[x] = pd.Categorical(data[x], categories=category_order, ordered=True)
                        if sns is not None:
                            sns.barplot(data=data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, ax=ax, errorbar=None)
                        else:
                            if hue and hue not in {x, y} and hue in data.columns:
                                grouped = data.groupby([x, hue], dropna=False)[y].mean().unstack(hue)
                                grouped.plot(kind="bar", ax=ax)
                            else:
                                data.groupby(x, dropna=False)[y].mean().plot(kind="bar", ax=ax, color="#55A868")
                columns["hue_used"] = bool(hue and hue not in {x, y} and hue in df.columns)
                if show_value_labels:
                    self._annotate_bar_labels(ax, orientation=orientation)
                    columns["value_labels_rendered"] = True
                    columns["annotation_mode"] = "bar_labels"
                else:
                    columns["value_labels_rendered"] = False
                    columns["annotation_mode"] = None
            chart_debug = dict(columns.get("chart_debug") or {})
            chart_debug["orientation"] = orientation
            chart_debug["stack_mode"] = stack_mode
            chart_debug["hue_used"] = columns["hue_used"]
            chart_debug["value_labels_rendered"] = columns["value_labels_rendered"]
            chart_debug["annotation_mode"] = columns["annotation_mode"]
            chart_debug["show_value_labels"] = bool(columns.get("show_value_labels"))
            columns["chart_debug"] = chart_debug
        elif kind == "lineplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if x is None:
                raise ValueError("No suitable column found for lineplot")
            if y is None:
                if _is_dt(df[x]):
                    data = pd.DataFrame({x: pd.to_datetime(df[x], errors="coerce")}).dropna()
                    data = data.groupby(x, dropna=False).size().reset_index(name="count").sort_values(x)
                    if sns is not None:
                        sns.lineplot(data=data, x=x, y="count", marker="o", ax=ax)
                    else:
                        ax.plot(data[x], data["count"], marker="o")
                else:
                    data = df.reset_index(drop=False).rename(columns={"index": "_index"})[[x, "_index"]].dropna()
                    if sns is not None:
                        sns.lineplot(data=data, x="_index", y=x, marker="o", ax=ax)
                    else:
                        ax.plot(data["_index"], data[x], marker="o")
            else:
                data_columns = [x, y] + ([hue] if hue and hue not in {x, y} else [])
                data = df[data_columns].dropna()
                if _is_dt(data[x]):
                    data = data.assign(**{x: pd.to_datetime(data[x], errors="coerce")}).dropna().sort_values(x)
                else:
                    data = data.sort_values(x)
                if sns is not None:
                    sns.lineplot(data=data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, marker="o", ax=ax)
                else:
                    ax.plot(data[x], data[y], marker="o")
        elif kind == "scatterplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if x is None or y is None:
                raise ValueError("Need two numeric columns for scatterplot")
            data = df[[x, y] + ([hue] if hue and hue not in {x, y} else [])].dropna()
            if sns is not None:
                sns.scatterplot(data=data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, ax=ax)
            else:
                ax.scatter(data[x], data[y], c="#4C72B0")
        elif kind == "boxplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if y is None:
                raise ValueError("No numeric column found for boxplot")
            if x is not None and x != y:
                data = df[[x, y]].dropna()
                if sns is not None:
                    sns.boxplot(data=data, x=x, y=y, ax=ax)
                else:
                    groups = [group[y].dropna().tolist() for _, group in data.groupby(x, dropna=False)]
                    ax.boxplot(groups, tick_labels=[str(label) for label in data[x].dropna().astype("string").unique().tolist()])
            else:
                data = df[y].dropna().tolist()
                if sns is not None:
                    sns.boxplot(y=data, ax=ax)
                else:
                    ax.boxplot(data)
        elif kind == "violinplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if x is None or y is None:
                raise ValueError("Need one categorical column and one numeric column for violinplot")
            data = df[[x, y] + ([hue] if hue and hue not in {x, y} else [])].dropna()
            if sns is not None:
                sns.violinplot(data=data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, ax=ax, inner="quartile")
            else:
                groups = [group[y].dropna().tolist() for _, group in data.groupby(x, dropna=False)]
                ax.violinplot(groups, showmeans=False, showmedians=True)
                ax.set_xticks(range(1, len(groups) + 1))
                ax.set_xticklabels([str(label) for label in data[x].dropna().astype("string").unique().tolist()])
        elif kind == "swarmplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if x is None or y is None:
                raise ValueError("Need one categorical column and one numeric column for swarmplot")
            data = df[[x, y] + ([hue] if hue and hue not in {x, y} else [])].dropna()
            if sns is not None:
                sns.swarmplot(data=data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, ax=ax, size=3)
            else:
                self._fallback_categorical_scatter(ax, data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, size=20)
        elif kind == "stripplot":
            fig, ax = plt.subplots(figsize=(8, 4.5))
            if x is None or y is None:
                raise ValueError("Need one categorical column and one numeric column for stripplot")
            data = df[[x, y] + ([hue] if hue and hue not in {x, y} else [])].dropna()
            if sns is not None:
                sns.stripplot(data=data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, ax=ax, jitter=True, size=3, alpha=0.7)
            else:
                self._fallback_categorical_scatter(ax, data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, size=16, alpha=0.7)
        elif kind == "jointplot":
            if x is None or y is None:
                raise ValueError("Need two numeric columns for jointplot")
            data = df[[x, y] + ([hue] if hue and hue not in {x, y} else [])].dropna()
            if sns is not None:
                grid = sns.jointplot(data=data, x=x, y=y, hue=hue if hue and hue not in {x, y} else None, kind="scatter")
                grid.fig.suptitle(kind.replace("plot", " plot").title())
                grid.fig.tight_layout()
                return grid
            fig, ax = plt.subplots(figsize=(6, 6))
            ax.scatter(data[x], data[y], c="#4C72B0")
            ax.set_xlabel(x)
            ax.set_ylabel(y)
        elif kind == "pairplot":
            if len(variables) < 2:
                raise ValueError("Need at least two numeric columns for pairplot")
            data = df[variables + ([hue] if hue and hue not in variables else [])].dropna()
            if sns is not None:
                grid = sns.pairplot(data=data, vars=variables, hue=hue if hue and hue in data.columns else None, corner=True, diag_kind="hist")
                grid.fig.suptitle(kind.replace("plot", " plot").title())
                grid.fig.tight_layout()
                return grid
            axes = pd.plotting.scatter_matrix(data[variables], figsize=(8, 8))
            fig = axes[0, 0].figure
            fig.suptitle(kind.replace("plot", " plot").title())
            fig.tight_layout()
            return fig
        elif kind == "heatmap":
            fig, ax = plt.subplots(figsize=(8, 6))
            mode = str(columns.get("mode") or "correlation").lower()
            if mode == "pivot":
                if x is None or y is None or columns.get("value") is None:
                    raise ValueError("Need x, y, and value for pivot heatmap")
                value = str(columns["value"])
                pivot = df.pivot_table(index=y, columns=x, values=value, aggfunc="mean")
                if sns is not None:
                    sns.heatmap(pivot, cmap="viridis", annot=len(pivot.columns) <= 8, ax=ax)
                else:
                    im = ax.imshow(pivot.values, cmap="viridis")
                    ax.set_xticks(range(len(pivot.columns)))
                    ax.set_yticks(range(len(pivot.index)))
                    ax.set_xticklabels(list(map(str, pivot.columns)), rotation=45, ha="right")
                    ax.set_yticklabels(list(map(str, pivot.index)))
                    fig.colorbar(im, ax=ax)
            else:
                if len(variables) < 2:
                    raise ValueError("Need at least two numeric columns for heatmap")
                corr = df[variables].corr(numeric_only=True)
                if sns is not None:
                    sns.heatmap(corr, cmap="viridis", annot=len(variables) <= 8, ax=ax)
                else:
                    im = ax.imshow(corr.values, cmap="viridis")
                    ax.set_xticks(range(len(corr.columns)))
                    ax.set_yticks(range(len(corr.index)))
                    ax.set_xticklabels(list(corr.columns), rotation=45, ha="right")
                    ax.set_yticklabels(list(corr.index))
                    fig.colorbar(im, ax=ax)
        else:
            raise ValueError("No suitable chart kind could be inferred")
        if ax is not None:
            self._apply_axis_labels(
                ax,
                kind,
                x,
                y,
                orientation=str(columns.get("orientation") or "vertical"),
                stack_mode=str(columns.get("stack_mode") or "none"),
                value=str(columns.get("value")) if columns.get("value") is not None else None,
            )
            ax.set_title(title)
        elif fig is not None:
            fig.suptitle(title)
        fig.tight_layout()
        return fig

    @staticmethod
    def _label_position(patch: Any, *, orientation: str = "vertical") -> tuple[float, float]:
        if orientation == "horizontal":
            return (patch.get_x() + patch.get_width(), patch.get_y() + patch.get_height() / 2)
        return (patch.get_x() + patch.get_width() / 2, patch.get_y() + patch.get_height())

    @staticmethod
    def _categorical_positions(data: pd.DataFrame, category_column: str) -> tuple[pd.Series, list[str]]:
        categories = [str(value) for value in data[category_column].astype("string").dropna().drop_duplicates().tolist()]
        if not categories:
            raise ValueError(f"No categorical values available for {category_column}")
        mapping = {category: index for index, category in enumerate(categories)}
        positions = data[category_column].astype("string").map(lambda item: mapping.get(str(item)))
        return positions.astype(float), categories

    @staticmethod
    def _fallback_categorical_scatter(
        ax: Axes,
        data: pd.DataFrame,
        *,
        x: str,
        y: str,
        hue: str | None = None,
        alpha: float = 0.7,
        size: float = 16,
    ) -> None:
        positions, categories = SeabornChatVisualizer._categorical_positions(data, x)
        if hue and hue in data.columns and data[hue].notna().any():
            for hue_value, group in data.assign(_cat_pos=positions).groupby(hue, dropna=False):
                ax.scatter(group["_cat_pos"], group[y], s=size, alpha=alpha, label=str(hue_value))
            ax.legend(title=str(hue))
        else:
            ax.scatter(positions, data[y], c="#4C72B0", s=size, alpha=alpha)
        ax.set_xticks(range(len(categories)))
        ax.set_xticklabels(categories)

    @staticmethod
    def _annotate_bar_labels(ax: Axes, *, orientation: str = "vertical") -> None:
        for container in getattr(ax, "containers", []):
            for patch in getattr(container, "patches", container):
                try:
                    value = float(patch.get_width() if orientation == "horizontal" else patch.get_height())
                except Exception:
                    continue
                if value is None:
                    continue
                if orientation == "horizontal":
                    x = patch.get_x() + patch.get_width()
                    y = patch.get_y() + patch.get_height() / 2
                    offset_xy = (3 if value >= 0 else -3, 0)
                    ha = "left" if value >= 0 else "right"
                    va = "center"
                else:
                    x = patch.get_x() + patch.get_width() / 2
                    y = value
                    offset_xy = (0, 3 if value >= 0 else -3)
                    ha = "center"
                    va = "bottom" if value >= 0 else "top"
                ax.annotate(
                    f"{value:g}",
                    xy=(x, y),
                    xytext=offset_xy,
                    textcoords="offset points",
                    ha=ha,
                    va=va,
                    fontsize=9,
                    clip_on=False,
                )

    @staticmethod
    def _apply_cjk_font_defaults() -> None:
        try:
            from matplotlib import font_manager
        except Exception:
            return
        available_fonts = {font.name for font in font_manager.fontManager.ttflist}
        for candidate in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "PingFang SC", "WenQuanYi Zen Hei"):
            if candidate in available_fonts:
                matplotlib.rcParams["font.family"] = "sans-serif"
                sans_serif = list(matplotlib.rcParams.get("font.sans-serif", []))
                if candidate not in sans_serif:
                    matplotlib.rcParams["font.sans-serif"] = [candidate, *sans_serif]
                break
        matplotlib.rcParams["axes.unicode_minus"] = False

    @staticmethod
    def _apply_axis_labels(
        ax: Axes,
        kind: str,
        x: str | None,
        y: str | None,
        *,
        orientation: str = "vertical",
        stack_mode: str = "none",
        value: str | None = None,
    ) -> None:
        if kind in {"barplot", "countplot"}:
            if kind == "barplot" and orientation == "horizontal":
                ax.set_xlabel(str(x) if x is not None else (str(value) if value else "value"))
                ax.set_ylabel(str(y) if y is not None else "category")
                return
            if kind == "barplot" and stack_mode in {"stacked", "percent_stacked"}:
                ax.set_xlabel(str(x) if x is not None else "category")
                if stack_mode == "percent_stacked":
                    ax.set_ylabel("percent")
                else:
                    ax.set_ylabel(str(value or y or "value"))
                return
            if x is not None:
                ax.set_xlabel(str(x))
            if y is not None:
                ax.set_ylabel(str(y))
            elif kind == "countplot":
                ax.set_ylabel("count")
            return
        if kind in {"histogram", "histplot"}:
            if x is not None:
                ax.set_xlabel(str(x))
            ax.set_ylabel("count")
            return
        if x is not None:
            ax.set_xlabel(str(x))
        if y is not None:
            ax.set_ylabel(str(y))

    def _result(self, request: str, df: pd.DataFrame | None, kind: str | None, text: str, plot: Any | None, columns: dict[str, Any]) -> SeabornChatResult:
        chart_debug = dict(columns.get("chart_debug") or {})
        chart_plan = {
            "kind": kind,
            "x": columns.get("x"),
            "y": columns.get("y"),
            "hue": columns.get("hue"),
            "value": columns.get("value"),
            "orientation": columns.get("orientation"),
            "stack_mode": columns.get("stack_mode"),
            "normalize_mode": columns.get("normalize_mode"),
            "category_order": list(columns.get("category_order") or []),
            "explicit_fields": dict(columns.get("explicit_fields") or {}),
            "source_result_id": columns.get("source_result_id"),
            "source_df_role": columns.get("source_df_role"),
            "confidence": columns.get("confidence"),
            "planner_source": columns.get("planner_source"),
            "sort_by": columns.get("sort_by"),
            "sort_direction": columns.get("sort_direction"),
        }
        meta = {
            VisualisationResult.META_PLOT_MESSAGES_KEY: [] if df is None else [
                {"backend": "seaborn", "request": request, "kind": kind, "columns": columns}
            ],
            "plot_backend": "seaborn",
            "plot_kind": kind,
            "plot_config": columns,
            "chart_plan": chart_plan,
            "planner": columns.get("planner"),
            "validated": columns.get("validated"),
            "repair_used": columns.get("repair_used"),
            "confidence": columns.get("confidence"),
            "reason": columns.get("reason"),
            "fallback_reason": columns.get("fallback_reason"),
            "fallback_blocked": columns.get("fallback_blocked"),
            "planner_status": columns.get("planner_status"),
            "planner_error": columns.get("planner_error"),
            "validation_errors": columns.get("validation_errors"),
            "render_error": columns.get("render_error"),
            "show_value_labels": columns.get("show_value_labels"),
            "planner_source": columns.get("planner_source"),
            "orientation": columns.get("orientation"),
            "stack_mode": columns.get("stack_mode"),
            "normalize_mode": columns.get("normalize_mode"),
            "chart_debug": chart_debug,
            "plot_error": columns.get("plot_error"),
        }
        return SeabornChatResult(
            text=text,
            meta=meta,
            plot=plot,
            code=json.dumps({"backend": "seaborn", "kind": kind, "plot_config": columns, "chart_plan": chart_plan}, indent=2),
            dataframe=df,
            kind=kind,
            plot_config=columns,
            chart_plan=chart_plan,
            visualizer=self,
        )

    def _visualize(self, request: str, data: ExecutionResult, *, stream: bool = False) -> SeabornChatResult:
        if data.df is None or data.df.empty:
            return self._result(
                request,
                data.df,
                None,
                "Nothing to visualize",
                None,
                {
                    "x": None,
                    "y": None,
                    "hue": None,
                    "orientation": None,
                    "stack_mode": None,
                    "normalize_mode": None,
                    "category_order": [],
                    "explicit_fields": {},
                    "planner_source": "fallback",
                    "planner": "fallback",
                    "validated": False,
                    "planner_status": "no_data",
                    "chart_debug": {"planner": "fallback", "planner_status": "no_data", "validated": False},
                },
            )
        working_df, dataframe_debug = self._prepare_dataframe(data.df)
        profile = self._profile(working_df)
        explicit_fields = self._extract_explicit_field_constraints(request, working_df)
        plan, plan_debug = self._plan_chart(request, working_df, profile, explicit_fields=explicit_fields)
        if plan is not None and plan_debug.get("validated"):
            try:
                final_validation_errors = self._validate_chart_plan(request, working_df, plan, explicit_fields=explicit_fields)
            except TypeError:
                final_validation_errors = self._validate_chart_plan(request, working_df, plan)
            if final_validation_errors:
                plan_debug["planner_status"] = "validation_failed"
                plan_debug["validated"] = False
                plan_debug["validation_errors"] = final_validation_errors
                plan = None
            else:
                columns = {
                    "x": plan.x,
                    "y": plan.y,
                    "hue": plan.hue,
                    "variables": list(plan.variables),
                    "value": plan.value,
                    "mode": plan.mode,
                    "orientation": plan.orientation,
                    "stack_mode": plan.stack_mode,
                    "normalize_mode": plan.normalize_mode,
                    "category_order": list(plan.category_order),
                    "explicit_fields": dict(plan.explicit_fields),
                    "source_result_id": plan.source_result_id,
                    "source_df_role": plan.source_df_role,
                    "planner_source": plan.planner_source or "llm_json",
                    "title": plan.title,
                    "show_value_labels": bool(plan.show_value_labels or explicit_fields.get("show_value_labels")),
                    "planner": "llm_json",
                    "validated": True,
                    "confidence": plan.confidence,
                    "reason": plan.reason,
                    "fallback_reason": None,
                    "fallback_blocked": False,
                    "planner_status": str(plan_debug.get("planner_status") or "ready"),
                    "planner_error": plan_debug.get("planner_error"),
                    "validation_errors": list(plan_debug.get("validation_errors") or []),
                    "render_error": None,
                    "chart_debug": dict(plan_debug),
                    "plot_error": None,
                }
                try:
                    columns["dataframe_debug"] = dataframe_debug
                    fig = self._render(plan.kind, working_df, columns)
                except Exception as exc:
                    plan_debug["planner_status"] = "render_failed"
                    plan_debug["validated"] = False
                    plan_debug["render_error"] = str(exc)
                    plan_debug["fallback_blocked"] = True
                    logger.warning("LLM-planned chart failed to render: %s", exc)
                    return self._result(
                        request,
                        working_df,
                        plan.kind,
                        f"Failed to render requested {plan.kind} chart: {exc}",
                        None,
                        {
                            **columns,
                            "validated": False,
                            "planner_status": "render_failed",
                            "render_error": str(exc),
                            "chart_debug": dict(plan_debug),
                            "fallback_reason": None,
                            "fallback_blocked": True,
                            "plot_error": str(exc),
                            "dataframe_debug": dataframe_debug,
                        },
                    )
                else:
                    return self._result(request, working_df, plan.kind, f"Rendered {plan.kind} chart via LLM-planned spec.", fig, columns)
        planner_status = str(plan_debug.get("planner_status") or "planning_failed")
        fallback_allowed_status = {"planning_failed", "schema_parse_failed", "validation_failed"}
        explicit_percent_stacked = explicit_fields.get("stack_mode") == "percent_stacked"
        if (
            planner_status in fallback_allowed_status
            and explicit_percent_stacked
            and not self._allow_semantic_fallback_for_explicit_requests
        ):
            failure_text = (
                plan_debug.get("planner_error")
                or "; ".join(str(item) for item in (plan_debug.get("validation_errors") or []))
                or "Failed to generate explicit percent-stacked chart."
            )
            plan_debug["fallback_blocked"] = True
            return self._result(
                request,
                working_df,
                plan.kind if plan is not None else "barplot",
                failure_text,
                None,
                {
                    "x": plan.x if plan is not None else explicit_fields.get("x"),
                    "y": plan.y if plan is not None else explicit_fields.get("y"),
                    "hue": plan.hue if plan is not None else explicit_fields.get("hue"),
                    "variables": list(plan.variables) if plan is not None else [],
                    "value": plan.value if plan is not None else explicit_fields.get("value"),
                    "mode": plan.mode if plan is not None else None,
                    "orientation": plan.orientation if plan is not None else explicit_fields.get("orientation"),
                    "stack_mode": "percent_stacked",
                    "normalize_mode": "percent_of_group",
                    "category_order": list(plan.category_order) if plan is not None else list(explicit_fields.get("category_order") or []),
                    "explicit_fields": dict(plan.explicit_fields) if plan is not None else dict(explicit_fields),
                    "source_result_id": plan.source_result_id if plan is not None else None,
                    "source_df_role": plan.source_df_role if plan is not None else "plot_ready",
                    "planner_source": (plan.planner_source if plan is not None else "llm_json") or "llm_json",
                    "title": plan.title if plan is not None else None,
                    "show_value_labels": bool((plan.show_value_labels if plan is not None else False) or explicit_fields.get("show_value_labels")),
                    "planner": "llm_json",
                    "validated": False,
                    "confidence": plan_debug.get("confidence"),
                    "reason": plan_debug.get("reason"),
                    "fallback_reason": None,
                    "fallback_blocked": True,
                    "planner_status": planner_status,
                    "planner_error": plan_debug.get("planner_error"),
                    "validation_errors": list(plan_debug.get("validation_errors") or []),
                    "render_error": plan_debug.get("render_error"),
                    "chart_debug": dict(plan_debug),
                    "plot_error": failure_text,
                    "dataframe_debug": dataframe_debug,
                },
            )
        if planner_status not in fallback_allowed_status:
            failure_text = (
                plan_debug.get("render_error")
                or plan_debug.get("planner_error")
                or "; ".join(str(item) for item in (plan_debug.get("validation_errors") or []))
                or "Failed to generate requested chart."
            )
            plan_debug["fallback_blocked"] = True
            failed_kind = plan.kind if plan is not None else None
            return self._result(
                request,
                working_df,
                failed_kind,
                failure_text,
                None,
                {
                    "x": plan.x if plan is not None else None,
                    "y": plan.y if plan is not None else None,
                    "hue": plan.hue if plan is not None else None,
                    "variables": list(plan.variables) if plan is not None else [],
                    "value": plan.value if plan is not None else None,
                    "mode": plan.mode if plan is not None else None,
                    "orientation": plan.orientation if plan is not None else None,
                    "stack_mode": plan.stack_mode if plan is not None else None,
                    "normalize_mode": plan.normalize_mode if plan is not None else None,
                    "category_order": list(plan.category_order) if plan is not None else [],
                    "explicit_fields": dict(plan.explicit_fields) if plan is not None else dict(explicit_fields),
                    "source_result_id": plan.source_result_id if plan is not None else None,
                    "source_df_role": plan.source_df_role if plan is not None else "plot_ready",
                    "planner_source": (plan.planner_source if plan is not None else "llm_json") or "llm_json",
                    "title": plan.title if plan is not None else None,
                    "show_value_labels": bool((plan.show_value_labels if plan is not None else False) or explicit_fields.get("show_value_labels")),
                    "planner": "llm_json",
                    "validated": False,
                    "confidence": plan_debug.get("confidence"),
                    "reason": plan_debug.get("reason"),
                    "fallback_reason": None,
                    "fallback_blocked": True,
                    "planner_status": planner_status,
                    "planner_error": plan_debug.get("planner_error"),
                    "validation_errors": list(plan_debug.get("validation_errors") or []),
                    "render_error": plan_debug.get("render_error"),
                    "chart_debug": dict(plan_debug),
                    "plot_error": failure_text,
                    "dataframe_debug": dataframe_debug,
                },
            )

        fallback_reason = plan_debug.get("planner_error") or "LLM planning unavailable"
        if plan is None or not plan_debug.get("validated"):
            logger.warning("Falling back to seaborn heuristics: %s", fallback_reason)
        kind = self._choose_kind(request, profile)
        columns = self._pick_columns(kind, profile)
        columns.update(
            {
                "planner": "fallback",
                "validated": False,
                "fallback_reason": fallback_reason,
                "fallback_blocked": False,
                "confidence": None,
                "reason": None,
                "title": self._default_chart_title(kind) if kind is not None else None,
                "show_value_labels": bool(explicit_fields.get("show_value_labels")),
                "orientation": "vertical" if kind == "barplot" else None,
                "stack_mode": "none" if kind == "barplot" else None,
                "normalize_mode": "none",
                "category_order": list(explicit_fields.get("category_order") or []),
                "explicit_fields": dict(explicit_fields),
                "source_result_id": None,
                "source_df_role": "plot_ready",
                "planner_source": "fallback",
                "planner_status": "fallback",
                "planner_error": plan_debug.get("planner_error"),
                "validation_errors": list(plan_debug.get("validation_errors") or []),
                "render_error": None,
                "chart_debug": {
                    **dict(plan_debug),
                    "planner": "fallback",
                    "planner_status": "fallback",
                    "fallback_reason": fallback_reason,
                    "fallback_blocked": False,
                },
                "plot_error": None,
            }
        )
        if kind is None:
            return self._result(request, working_df, None, "Failed to infer a chart kind for the provided dataframe.", None, columns)
        try:
            fig = self._render(kind, working_df, columns)
        except Exception as exc:
            logger.warning("Failed to render seaborn chart: %s", exc)
            columns["render_error"] = str(exc)
            columns["plot_error"] = str(exc)
            chart_debug = dict(columns.get("chart_debug") or {})
            chart_debug["render_error"] = str(exc)
            chart_debug["planner_status"] = "render_failed"
            chart_debug["show_value_labels"] = bool(columns.get("show_value_labels"))
            columns["chart_debug"] = chart_debug
            return self._result(request, working_df, kind, f"Failed to visualize request! Output: {exc}", None, columns)
        return self._result(request, working_df, kind, f"Rendered {kind} chart via fallback.", fig, columns)

    def edit(self, request: str, visualization: VisualisationResult, *, stream: bool = False) -> SeabornChatResult:
        if not isinstance(visualization, SeabornChatResult):
            raise ValueError(f"{self.__class__.__name__} can only edit {SeabornChatResult.__name__} objects")
        if visualization.dataframe is None:
            raise ValueError("No dataframe found in the provided visualization")
        if visualization.meta.get(VisualisationResult.META_PLOT_MESSAGES_KEY) is None:
            raise ValueError("No plot message history found in the provided visualization")
        return self._visualize(request, ExecutionResult(text=visualization.text, meta={}, df=visualization.dataframe), stream=stream)


