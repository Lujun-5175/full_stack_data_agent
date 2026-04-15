from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from full_stack_data_agent.app.chat_service import ChatService
from full_stack_data_agent.app.conversation_export import build_conversation_export_bundle
from full_stack_data_agent.config.settings import get_settings
from full_stack_data_agent.context.upload_processor import process_uploaded_file


DATASET_PATH = Path(r"D:\data_bao\full_stack_data_agent\data\Telco-Customer-Churn.csv")
OUT_ROOT = Path(r"D:\data_bao\full_stack_data_agent\reports")


PROMPTS: dict[str, str] = {
    "G1": """请基于当前数据集分析不同 Contract 类型的客户流失情况，并严格按下面步骤输出：

1. 先生成一个汇总表，按 Contract 分组，包含以下字段：

* Contract
* total_customers：该合同类型总客户数
* churned_customers：流失客户数（Churn = Yes）
* churn_rate：流失率 = churned_customers / total_customers

2. 将结果表按 churn_rate 从高到低排序。
3. 画一张竖向柱状图（bar chart），要求如下：

* 图表标题：Churn Rate by Contract Type
* x 轴：Contract
* y 轴：churn_rate
* 每个柱子代表一种 Contract 类型
* y 轴显示为百分比
* 柱子上显示具体流失率数值（百分比）
* 按 churn_rate 从高到低排列柱子
* 不要画堆叠图，不要画折线图，不要画饼图

4. 画完图后，用 3-5 句话解释：

* 哪一种 Contract 类型的流失率最高
* 哪一种最低
* 合同期限和流失之间可能有什么关系
* 这个结果对留存策略意味着什么
  请严格按“结果表 → 图表 → 解释”的顺序输出。""",
    "G2": """请帮我识别高风险客户群体，并严格按下面步骤分析：

1. 先筛选出 InternetService 不等于 \"No\" 的客户，只分析真正使用网络服务的客户。
2. 按 OnlineSecurity 和 TechSupport 两个字段的组合分组，生成结果表，包含：

* OnlineSecurity
* TechSupport
* total_customers
* churned_customers（Churn = Yes）
* churn_rate = churned_customers / total_customers

3. 将结果表按 churn_rate 从高到低排序，只保留客户数不少于 50 的组合，避免样本太小失真。
4. 画一张横向柱状图（horizontal bar chart），要求如下：

* 图表标题：Churn Rate by OnlineSecurity and TechSupport Combination
* y 轴：由 OnlineSecurity + TechSupport 组成的组合标签
* x 轴：churn_rate
* 每根横条代表一个服务组合
* 只展示 churn_rate 最高的前 6 个组合
* x 轴显示为百分比
* 条形末端显示具体流失率百分比
* 按 churn_rate 从高到低排序
* 不要画折线图，不要画饼图，不要画散点图

5. 然后再补一张分组柱状图（grouped bar chart），要求如下：

* 图表标题：Customer Count and Churn Rate for High-Risk Service Combinations
* x 轴：前 6 个高风险组合
* 一组柱子中展示两个指标：

  * total_customers
  * churn_rate
* 如果一个图里同时展示 count 和 rate 不方便，就只保留上一张横向柱状图，不要强行画错图

6. 最后解释：

* 风险最高的是哪些服务组合
* 这些高流失组合是否普遍缺少 OnlineSecurity 或 TechSupport
* 这更像是服务缺失问题、客户粘性问题，还是合同约束问题
* 给出 2 条可能的业务建议
  请按“结果表 → 图表 → 解释”的顺序输出。""",
    "G3": """请分析流失是否主要集中在“新用户 + 月费高 + 短合同”群体，并严格按下面步骤执行：

1. 先创建 tenure 分组字段 tenure_bucket，分组规则固定为：

* 0-12 months
* 13-24 months
* 25-48 months
* 49+ months

2. 再创建 MonthlyCharges 分组字段 monthly_charge_bucket，分组规则固定为：

* Low：MonthlyCharges < 35
* Medium：35 ≤ MonthlyCharges < 70
* High：MonthlyCharges ≥ 70

3. 然后按以下三个字段分组：

* tenure_bucket
* monthly_charge_bucket
* Contract

4. 生成结果表，包含：

* tenure_bucket
* monthly_charge_bucket
* Contract
* total_customers
* churned_customers（Churn = Yes）
* churn_rate = churned_customers / total_customers

5. 为了避免噪音，只保留 total_customers ≥ 30 的组合。
6. 先画一张热力图（heatmap），要求如下：

* 图表标题：Churn Rate Heatmap by Tenure and Monthly Charges
* x 轴：monthly_charge_bucket（Low, Medium, High）
* y 轴：tenure_bucket（0-12, 13-24, 25-48, 49+）
* 单元格的值：churn_rate
* 颜色越深表示 churn_rate 越高
* 如果同一个 tenure_bucket 和 monthly_charge_bucket 下有多个 Contract，请先按这两个字段聚合后再画热力图
* 在每个格子里显示具体 churn_rate 百分比

7. 再画一张分组柱状图（grouped bar chart），要求如下：

* 图表标题：Churn Rate by Contract within Each Tenure Group
* x 轴：tenure_bucket
* y 轴：churn_rate
* 颜色分组：Contract
* 每个 tenure_bucket 下按 Contract 展示多个柱子
* y 轴显示为百分比
* 不要堆叠，使用并排分组柱状图
* 如果图过于拥挤，可以只展示 churn_rate 最高的 8 个组合，并在解释中说明

8. 最后解释下面 4 个问题：

* 流失率最高的是哪一类客户组合
* tenure、MonthlyCharges、Contract 三者中，谁看起来影响更明显
* “新用户 + 高月费 + 月付合同”是否真的是最高风险群体
* 如果要优先做 retention，最应该先干预哪一类客户
  请严格按“结果表 → 热力图 → 分组柱状图 → 解释”输出。""",
    "G4": """请分析不同 PaymentMethod 与 PaperlessBilling 组合下的客户流失情况，并严格按下面步骤输出：

1. 按 PaymentMethod 和 PaperlessBilling 分组，生成结果表，包含：

* PaymentMethod
* PaperlessBilling
* total_customers
* churned_customers（Churn = Yes）
* churn_rate = churned_customers / total_customers

2. 只保留 total_customers ≥ 50 的组合。
3. 将结果表按 churn_rate 从高到低排序。
4. 画一张分组柱状图（grouped bar chart）：

* 图表标题：Churn Rate by Payment Method and Paperless Billing
* x 轴：PaymentMethod
* y 轴：churn_rate
* 颜色分组：PaperlessBilling
* y 轴显示为百分比
* 不要堆叠

5. 再用 4-6 句话解释：

* 哪种支付方式组合流失率最高
* 无纸化计费是否和更高流失率有关
* 这个结果更像支付摩擦问题还是客户结构问题
* 给出 2 条业务建议
  请按“结果表 → 图表 → 解释”输出。""",
    "G5": """请分析 SeniorCitizen、Partner、Dependents 三个字段组合下的流失情况，并严格按下面步骤输出：

1. 按 SeniorCitizen、Partner、Dependents 分组，生成结果表，包含：

* SeniorCitizen
* Partner
* Dependents
* total_customers
* churned_customers
* churn_rate

2. 只保留 total_customers ≥ 40 的组合。
3. 将结果表按 churn_rate 从高到低排序，只展示前 8 个组合。
4. 画一张横向柱状图：

* 图表标题：Top 8 Churn Segments by SeniorCitizen, Partner, and Dependents
* y 轴：由三个字段拼成的组合标签
* x 轴：churn_rate
* x 轴显示为百分比
* 条形末端显示 churn_rate

5. 最后解释：

* 哪类家庭结构组合风险最高
* SeniorCitizen 是否明显抬高流失风险
* Partner / Dependents 是否起到缓冲作用
* 给出 2 条留存建议
  请按“结果表 → 图表 → 解释”输出。""",
    "G6": """请分析不同 Contract 类型在收入稳定性上的差异，并严格按下面步骤输出：

1. 按 Contract 分组，生成结果表，包含：

* Contract
* total_customers
* churned_customers（Churn = Yes）
* churn_rate
* avg_monthly_charges = MonthlyCharges 平均值
* avg_total_charges = TotalCharges 平均值

2. 按 churn_rate 从高到低排序。
3. 画第一张竖向柱状图：

* 图表标题：Churn Rate by Contract Type
* x 轴：Contract
* y 轴：churn_rate
* y 轴显示为百分比

4. 再画第二张竖向柱状图：

* 图表标题：Average Total Charges by Contract Type
* x 轴：Contract
* y 轴：avg_total_charges

5. 最后用 5-6 句话解释：

* 哪类合同 churn_rate 最高
* 哪类合同 avg_total_charges 最高
* churn_rate 与 avg_total_charges 是否看起来负相关
* 这对收入稳定性意味着什么
  请按“结果表 → 图1 → 图2 → 解释”输出。""",
    "G7": """请分析客户在不同 tenure_bucket 下的流失率趋势，并严格按下面步骤输出：

1. 创建 tenure_bucket，分组规则固定为：

* 0-12 months
* 13-24 months
* 25-48 months
* 49+ months

2. 按 tenure_bucket 分组，生成结果表，包含：

* tenure_bucket
* total_customers
* churned_customers
* churn_rate

3. 按 tenure_bucket 的自然顺序排列，不要按字母顺序。
4. 画一张折线图（line chart）：

* 图表标题：Churn Rate by Tenure Bucket
* x 轴：tenure_bucket
* y 轴：churn_rate
* y 轴显示为百分比
* 每个点显示 churn_rate 百分比

5. 最后解释：

* 流失率是否随着 tenure 增加而下降
* 哪个 tenure_bucket 风险最高
* 这对新客户 onboarding 有什么启示
  请按“结果表 → 图表 → 解释”输出。""",
    "G10": """请分析不同 Region 的客户流失情况，并严格按下面步骤输出：

1. 按 Region 分组，生成 total_customers、churned_customers、churn_rate
2. 画一张柱状图对比各 Region 的 churn_rate
3. 解释哪个 Region 风险最高
   请按“结果表 → 图表 → 解释”输出。""",
    "G11": """请只分析满足以下条件的客户，并按 Contract 输出流失情况：

* InternetService = \"No\"
* OnlineSecurity = \"Yes\"
* TechSupport = \"Yes\"
  如果没有满足条件的数据，请明确告诉我不要继续画图或总结。""",
    "G12": """请按 Contract 分组计算 total_customers、churned_customers、churn_rate，然后只画一张图：

* 图表标题：Contract Churn Rate Only
* x 轴：Contract
* y 轴：churn_rate
* 不要画 total_customers
* 不要画 churned_customers
* 不要做双轴图
* 不要自动替换成别的指标
  最后只用 2 句话解释，不要重复表格内容。""",
}

G8_FOLLOWUP = "把刚才那个结果重新画成横向柱状图，其他都不要变；x 轴还是 churn_rate，y 轴还是 Contract，按 churn_rate 从高到低排列，并保留百分比标签。"
G9_FOLLOWUP = "只解释刚才那张图，不要重新算表，也不要重新画图。重点说最高风险组合、最低风险组合，以及这对留存策略意味着什么。"

SUCCESS_MARKERS = ("analysis complete", "success", "已完成", "已经展示", "图已经", "如下图")


@dataclass
class TurnCapture:
    assistant_text: str
    completion_validation: dict[str, Any]
    turn_failure_state: str | None
    failure_reason: str | None
    final_failure_reason: str | None
    table_artifact_present: bool
    chart_artifact_present: bool
    chart_render_status: str
    primary_table_artifact_id: str | None
    primary_chart_artifact_id: str | None
    chart_source_artifact_id: str | None
    chart_type: str | None
    render_kind: str | None
    x: str | None
    y: str | None
    hue: str | None
    orientation: str | None
    title: str | None
    missing_deliverables: list[str]
    pseudo_success_after_failure: bool
    errors_warnings: list[str]
    artifact_count: int
    artifacts: list[dict[str, Any]]
    followup_target_artifact_id: str | None


def _to_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    fn = getattr(obj, "to_dict", None)
    if callable(fn):
        payload = fn()
        if isinstance(payload, dict):
            return payload
    return {}


def _extract_field(binding: Any) -> str | None:
    if isinstance(binding, dict):
        field = binding.get("field")
        if field is not None:
            return str(field)
    if isinstance(binding, str):
        return binding
    return None


def _capture_turn(state, result) -> TurnCapture:
    last = result.last_databao_result
    if last is None:
        text = state.turns[-1].assistant_message.content if state.turns else ""
        return TurnCapture(
            assistant_text=text,
            completion_validation={},
            turn_failure_state=None,
            failure_reason=result.last_error,
            final_failure_reason=None,
            table_artifact_present=False,
            chart_artifact_present=False,
            chart_render_status="missing",
            primary_table_artifact_id=None,
            primary_chart_artifact_id=None,
            chart_source_artifact_id=None,
            chart_type=None,
            render_kind=None,
            x=None,
            y=None,
            hue=None,
            orientation=None,
            title=None,
            missing_deliverables=[],
            pseudo_success_after_failure=False,
            errors_warnings=[result.last_error] if result.last_error else [],
            artifact_count=0,
            artifacts=[],
            followup_target_artifact_id=None,
        )

    workspace = _to_dict(last.result_workspace)
    grounded = _to_dict(last.grounded_response)
    artifacts = workspace.get("artifacts", []) if isinstance(workspace.get("artifacts"), list) else []
    by_id = {
        str(item.get("artifact_id")): item
        for item in artifacts
        if isinstance(item, dict) and item.get("artifact_id")
    }

    primary_table = last.primary_table_artifact_id or grounded.get("primary_table_artifact_id")
    primary_chart = last.primary_chart_artifact_id or grounded.get("primary_chart_artifact_id")
    table_artifact = by_id.get(str(primary_table or ""))
    chart_artifact = by_id.get(str(primary_chart or ""))

    table_present = bool(primary_table and isinstance(table_artifact, dict))
    chart_present = bool(primary_chart and isinstance(chart_artifact, dict))

    render_payload = chart_artifact.get("render_payload") if isinstance(chart_artifact, dict) else {}
    if not isinstance(render_payload, dict):
        render_payload = {}
    chart_spec = render_payload.get("chart_spec") or (chart_artifact.get("chart_spec") if chart_artifact else None) or last.plot_spec
    chart_meta = render_payload.get("chart_meta") or (chart_artifact.get("chart_meta") if chart_artifact else None) or last.plot_meta or {}
    if not isinstance(chart_meta, dict):
        chart_meta = {"value": chart_meta}

    x = y = hue = orientation = title = None
    chart_type = None
    if isinstance(chart_spec, dict):
        encoding = chart_spec.get("encoding") if isinstance(chart_spec.get("encoding"), dict) else {}
        x = _extract_field(encoding.get("x"))
        y = _extract_field(encoding.get("y"))
        color = encoding.get("color")
        hue = _extract_field(color)
        title = str(chart_spec.get("title")) if chart_spec.get("title") is not None else None
        mark = chart_spec.get("mark")
        if isinstance(mark, dict):
            chart_type = str(mark.get("type")) if mark.get("type") is not None else None
        elif mark is not None:
            chart_type = str(mark)

    render_kind = (
        chart_meta.get("render_kind")
        or chart_meta.get("kind")
        or last.plot_kind
        or (chart_artifact.get("metadata") or {}).get("plot_kind") if isinstance(chart_artifact, dict) else None
    )
    if render_kind is not None:
        render_kind = str(render_kind)

    if isinstance(chart_meta.get("orientation"), str):
        orientation = chart_meta.get("orientation")
    elif chart_type in {"bar", "barplot"} and x and y:
        if "rate" in (x or "").lower() and "contract" in (y or "").lower():
            orientation = "horizontal"
        elif "contract" in (x or "").lower() and "rate" in (y or "").lower():
            orientation = "vertical"

    chart_debug = last.chart_debug or {}
    if not chart_present:
        chart_render_status = "missing"
    else:
        render_failed = bool(last.plot_error) or bool(chart_meta.get("render_failed"))
        planner_failed = bool(chart_debug.get("chart_failure_stage")) or (chart_debug.get("chart_renderable") is False)
        chart_render_status = "failed" if (render_failed or planner_failed) else "success"

    completion = last.completion_validation or {}
    missing = [str(item) for item in (completion.get("missing_deliverables") or [])]
    text = last.text or (state.turns[-1].assistant_message.content if state.turns else "")
    failed_state = bool(last.turn_failure_state or completion.get("status") == "failed")
    pseudo_success = failed_state and any(marker in text.lower() for marker in SUCCESS_MARKERS)

    errors = []
    for item in [
        result.last_error,
        last.failure_reason,
        (last.thread_meta or {}).get("final_failure_reason"),
        last.plot_error,
        chart_debug.get("chart_failure_reason") if isinstance(chart_debug, dict) else None,
    ]:
        if item:
            errors.append(str(item))
    for note in completion.get("validation_notes") or []:
        if note:
            errors.append(str(note))

    chart_source = None
    if isinstance(chart_artifact, dict):
        chart_source = chart_artifact.get("parent_artifact_id")
        if not chart_source:
            chart_source = render_payload.get("source_artifact_id")
        if not chart_source:
            metadata = chart_artifact.get("metadata")
            if isinstance(metadata, dict):
                chart_source = metadata.get("source_artifact_id")

    return TurnCapture(
        assistant_text=text,
        completion_validation=completion,
        turn_failure_state=last.turn_failure_state,
        failure_reason=last.failure_reason,
        final_failure_reason=(last.thread_meta or {}).get("final_failure_reason"),
        table_artifact_present=table_present,
        chart_artifact_present=chart_present,
        chart_render_status=chart_render_status,
        primary_table_artifact_id=primary_table,
        primary_chart_artifact_id=primary_chart,
        chart_source_artifact_id=chart_source,
        chart_type=chart_type,
        render_kind=render_kind,
        x=x,
        y=y,
        hue=hue,
        orientation=orientation,
        title=title,
        missing_deliverables=missing,
        pseudo_success_after_failure=bool(pseudo_success),
        errors_warnings=errors,
        artifact_count=len(artifacts),
        artifacts=artifacts,
        followup_target_artifact_id=last.followup_target_artifact_id or grounded.get("followup_target_artifact_id"),
    )


def _evaluate_case(case_id: str, capture: TurnCapture, *, expected: str, extra: dict[str, Any] | None = None) -> tuple[str, str, str, list[str]]:
    notes: list[str] = []
    extra = extra or {}

    if expected == "deliverables_table_chart_explain":
        explain_ok = len((capture.assistant_text or "").strip()) >= 40
        if capture.table_artifact_present and capture.chart_artifact_present and capture.chart_render_status == "success" and explain_ok and (capture.completion_validation or {}).get("status") not in {"failed"} and not capture.missing_deliverables:
            return "PASS", "", "", notes
        layer = "completion validation"
        reason = "Missing required deliverables (table/chart/explain) or chart not rendered."
        if not capture.table_artifact_present:
            layer = "execution"
            reason = "No table artifact generated."
        elif not capture.chart_artifact_present or capture.chart_render_status != "success":
            layer = "chart planning/render"
            reason = "Chart artifact missing or render failed."
        elif capture.missing_deliverables or (capture.completion_validation or {}).get("status") == "failed":
            layer = "completion validation"
            reason = "Completion validation reports missing deliverables."
        return "FAIL", reason, layer, notes

    if expected == "followup_replot":
        prev_table = extra.get("prev_table")
        expected_target = prev_table
        bound_ok = bool(expected_target) and capture.followup_target_artifact_id == expected_target
        orient_ok = capture.orientation == "horizontal" or ((capture.x or "").lower() == "churn_rate" and (capture.y or "").lower() == "contract")
        if bound_ok and capture.chart_artifact_present and capture.chart_render_status == "success" and orient_ok:
            return "PASS", "", "", notes
        return "FAIL", "Follow-up did not correctly bind/replot prior artifact as requested.", "follow-up binding", notes

    if expected == "followup_explain_only":
        prev_artifact_count = int(extra.get("prev_artifact_count") or 0)
        no_new_artifacts = capture.artifact_count <= prev_artifact_count
        no_chart = not capture.chart_artifact_present
        text_ok = len((capture.assistant_text or "").strip()) >= 20
        if no_new_artifacts and no_chart and text_ok:
            return "PASS", "", "", notes
        return "FAIL", "Explain-only follow-up generated/attempted new artifacts or failed to stay grounded.", "follow-up binding", notes

    if expected == "fail_stop_missing_field":
        failed = (capture.completion_validation or {}).get("status") == "failed" or bool(capture.turn_failure_state)
        if failed and not capture.table_artifact_present and not capture.chart_artifact_present and not capture.pseudo_success_after_failure:
            return "PASS", "", "", notes
        return "FAIL", "Expected explicit fail-stop for nonexistent field but behavior was not strict.", "sql guardrail", notes

    if expected == "fail_stop_empty_filter":
        failed = (capture.completion_validation or {}).get("status") == "failed" or bool(capture.turn_failure_state)
        if failed and not capture.chart_artifact_present and not capture.pseudo_success_after_failure:
            return "PASS", "", "", notes
        return "FAIL", "Expected empty-result fail-stop (no chart/summary), but system did not stop safely.", "execution validation", notes

    return "FAIL", "Unknown expected rule", "completion validation", notes


def _prepare_context():
    payload = DATASET_PATH.read_bytes()
    ctx = process_uploaded_file(DATASET_PATH.name, payload, "text/csv")
    if ctx is None:
        raise RuntimeError("Failed to parse Telco dataset as upload context")
    return ctx


def main() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ["LLM_PROVIDER"] = "ollama"
    os.environ["LLM_FALLBACK_PROVIDER"] = "ollama"
    os.environ["OLLAMA_MODEL"] = "gemma4:e4b"
    os.environ["LLM_TIMEOUT"] = os.environ.get("LLM_TIMEOUT", "120")
    os.environ.setdefault("BINDING_LLM_ENABLED", "false")
    get_settings.cache_clear()

    out_dir = OUT_ROOT / f"telco_golden_e2e_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    bundles_dir = out_dir / "bundles"
    out_dir.mkdir(parents=True, exist_ok=True)
    bundles_dir.mkdir(parents=True, exist_ok=True)

    service = ChatService(get_settings())
    upload_ctx = _prepare_context()

    plan = [
        ("G1", "single", "deliverables_table_chart_explain"),
        ("G2", "single", "deliverables_table_chart_explain"),
        ("G3", "single", "deliverables_table_chart_explain"),
        ("G4", "single", "deliverables_table_chart_explain"),
        ("G5", "single", "deliverables_table_chart_explain"),
        ("G6", "single", "deliverables_table_chart_explain"),
        ("G7", "single", "deliverables_table_chart_explain"),
        ("G8", "followup_replot", "followup_replot"),
        ("G9", "followup_explain", "followup_explain_only"),
        ("G10", "single", "fail_stop_missing_field"),
        ("G11", "single", "fail_stop_empty_filter"),
        ("G12", "single", "deliverables_table_chart_explain"),
    ]

    all_records: list[dict[str, Any]] = []
    checkpoint_path = out_dir / "golden_results.partial.json"

    for case_id, mode, expected in plan:
        print(f"RUN {case_id} ...", flush=True)
        if mode == "single":
            state = service.create_state()
            state, result = service.send_message(state, PROMPTS[case_id], uploaded_contexts=[upload_ctx])
            capture = _capture_turn(state, result)
            status, reason, layer, notes = _evaluate_case(case_id, capture, expected=expected)
        elif mode == "followup_replot":
            state = service.create_state()
            state, r1 = service.send_message(state, PROMPTS["G1"], uploaded_contexts=[upload_ctx])
            c1 = _capture_turn(state, r1)
            state, r2 = service.send_message(state, G8_FOLLOWUP)
            capture = _capture_turn(state, r2)
            status, reason, layer, notes = _evaluate_case(case_id, capture, expected=expected, extra={"prev_table": c1.primary_table_artifact_id})
            notes.append(f"base_case_primary_table={c1.primary_table_artifact_id}")
            notes.append(f"base_case_primary_chart={c1.primary_chart_artifact_id}")
        elif mode == "followup_explain":
            state = service.create_state()
            state, r1 = service.send_message(state, PROMPTS["G2"], uploaded_contexts=[upload_ctx])
            c1 = _capture_turn(state, r1)
            state, r2 = service.send_message(state, G9_FOLLOWUP)
            capture = _capture_turn(state, r2)
            status, reason, layer, notes = _evaluate_case(case_id, capture, expected=expected, extra={"prev_artifact_count": c1.artifact_count})
            notes.append(f"base_case_artifact_count={c1.artifact_count}")
            notes.append(f"base_case_primary_chart={c1.primary_chart_artifact_id}")
        else:
            raise ValueError(f"Unknown mode: {mode}")

        bundle_path = None
        if status == "FAIL":
            bundle = build_conversation_export_bundle(state)
            bundle_path = bundles_dir / f"{case_id}_conversation_export.zip"
            bundle_path.write_bytes(bundle.zip_bytes)
            (bundles_dir / f"{case_id}_conversation_report.md").write_text(bundle.markdown_report, encoding="utf-8")
            (bundles_dir / f"{case_id}_conversation_full.json").write_text(
                json.dumps(bundle.full_json, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        record = {
            "case_id": case_id,
            "status": status,
            "failure_reason": reason,
            "failure_layer": layer,
            "notes": notes,
            "user_prompt": PROMPTS.get(case_id, G8_FOLLOWUP if case_id == "G8" else G9_FOLLOWUP),
            "final_assistant_text": capture.assistant_text,
            "table_artifact_present": capture.table_artifact_present,
            "chart_artifact_present": capture.chart_artifact_present,
            "chart_render_status": capture.chart_render_status,
            "primary_table_artifact_id": capture.primary_table_artifact_id,
            "primary_chart_artifact_id": capture.primary_chart_artifact_id,
            "chart_source_artifact_id": capture.chart_source_artifact_id,
            "chart_type": capture.chart_type,
            "render_kind": capture.render_kind,
            "x": capture.x,
            "y": capture.y,
            "hue": capture.hue,
            "orientation": capture.orientation,
            "title": capture.title,
            "completion_validation_status": (capture.completion_validation or {}).get("status"),
            "missing_deliverables": capture.missing_deliverables,
            "pseudo_success_after_upstream_failure": capture.pseudo_success_after_failure,
            "errors_warnings": capture.errors_warnings,
            "followup_target_artifact_id": capture.followup_target_artifact_id,
            "artifact_count": capture.artifact_count,
            "bundle_path": str(bundle_path) if bundle_path else None,
        }
        all_records.append(record)
        checkpoint_path.write_text(json.dumps(all_records, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_rows = [
        {
            "case_id": row["case_id"],
            "status": row["status"],
            "table": int(bool(row["table_artifact_present"])),
            "chart": int(bool(row["chart_artifact_present"])),
            "explain": int(bool((row["final_assistant_text"] or "").strip())),
            "completion_validation": row["completion_validation_status"],
            "root_cause": row["failure_layer"] if row["status"] == "FAIL" else "",
            "notes": row["failure_reason"] if row["status"] == "FAIL" else "",
        }
        for row in all_records
    ]

    results_path = out_dir / "golden_results.json"
    results_path.write_text(json.dumps(all_records, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path = out_dir / "summary_table.json"
    summary_path.write_text(json.dumps(summary_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# Telco Golden E2E Regression Report",
        "",
        f"- generated_at: {datetime.now().isoformat(timespec='seconds')}",
        f"- dataset: {DATASET_PATH}",
        "",
        "| case_id | status | table | chart | explain | completion_validation | root_cause | notes |",
        "| ------- | ------ | ----: | ----: | ------: | --------------------- | ---------- | ----- |",
    ]
    for row in summary_rows:
        md_lines.append(
            "| {case_id} | {status} | {table} | {chart} | {explain} | {completion_validation} | {root_cause} | {notes} |".format(**row)
        )

    for row in all_records:
        if row["status"] != "FAIL":
            continue
        md_lines.extend(
            [
                "",
                f"## Case {row['case_id']} Failure Analysis",
                "",
                f"* User prompt: {row['user_prompt']}",
                "* What was expected: See golden case contract for this case.",
                f"* What actually happened: {row['final_assistant_text']}",
                f"* Which artifact was produced: table={row['primary_table_artifact_id']}, chart={row['primary_chart_artifact_id']}",
                "* Which layer failed:",
                f"  * {row['failure_layer'] or 'unknown'}",
                f"* Concrete evidence: completion={row['completion_validation_status']}, missing={row['missing_deliverables']}, errors={row['errors_warnings']}",
                "* Recommended fix direction: Repair SQL guardrail/parser + obligation-to-SQL mapping + chart planning/binding after successful execution.",
            ]
        )
        if row.get("bundle_path"):
            md_lines.append(f"* Bundle: {row['bundle_path']}")

    report_path = out_dir / "golden_report.md"
    report_path.write_text("\n".join(md_lines), encoding="utf-8")

    print(json.dumps({
        "out_dir": str(out_dir),
        "report": str(report_path),
        "results": str(results_path),
        "summary": str(summary_path),
        "total": len(all_records),
        "pass": sum(1 for item in all_records if item['status'] == 'PASS'),
        "fail": sum(1 for item in all_records if item['status'] == 'FAIL'),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
