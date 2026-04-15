from __future__ import annotations

from full_stack_data_agent.app.chart_contracts import (
    build_chart_request_contract,
    build_controlled_repair_prompt,
    validate_chart_contract,
)


def test_chart_contract_extracts_bilingual_axes_and_kind_aliases() -> None:
    contract = build_chart_request_contract(
        '请画直方图，横轴："total revenue"，纵轴：销售额，颜色：客户类型，按 渠道 分组',
        chart_requested=True,
    )

    assert contract.chart_requested is True
    assert contract.requested_kind == "histogram"
    assert contract.requested_x == "total revenue"
    assert contract.requested_y == "销售额"
    assert contract.requested_hue == "客户类型"
    assert contract.requested_group_by == "渠道"


def test_chart_contract_extracts_colon_style_and_histplot_alias() -> None:
    contract = build_chart_request_contract(
        "Use histplot with x: total revenue, y: 销售额, group by Customer Segment",
        chart_requested=True,
    )

    assert contract.requested_kind == "histogram"
    assert contract.requested_x == "total revenue"
    assert contract.requested_y == "销售额"
    assert contract.requested_group_by == "Customer Segment"


def test_chart_contract_validation_prefers_chart_plan_over_chart_spec_encoding() -> None:
    contract = build_chart_request_contract(
        "show a horizontal bar chart with x=customer_count and y=Payment Method",
        chart_requested=True,
    )

    validation = validate_chart_contract(
        contract=contract,
        dataframe_columns=["Payment Method", "customer_count"],
        chart_plan={
            "kind": "barplot",
            "x": "customer_count",
            "y": "Payment Method",
            "orientation": "horizontal",
            "stack_mode": "none",
            "normalize_mode": "none",
        },
        chart_spec={
            "mark": "bar",
            "encoding": {
                "x": {"field": "wrong_x"},
                "y": {"field": "wrong_y"},
            },
        },
        artifact_purpose="business_result",
    )

    assert validation.status == "matched"


def test_chart_contract_missing_explicit_field_is_repairable_mismatch() -> None:
    contract = build_chart_request_contract(
        "画热力图，横轴：月份，纵轴：客户数",
        chart_requested=True,
    )

    validation = validate_chart_contract(
        contract=contract,
        dataframe_columns=["月份", "销售额"],
        chart_plan={"kind": "heatmap", "x": "月份", "y": "销售额"},
        artifact_purpose="business_result",
    )

    assert validation.status == "mismatch"
    assert validation.repairable is True
    assert any("客户数" in reason for reason in validation.mismatch_reasons)


def test_chart_contract_blocks_diagnostic_artifact_source() -> None:
    contract = build_chart_request_contract("plot x=category y=value", chart_requested=True)
    validation = validate_chart_contract(
        contract=contract,
        dataframe_columns=["category", "value"],
        chart_plan={"kind": "barplot", "x": "category", "y": "value"},
        artifact_purpose="diagnostic",
    )

    assert validation.status == "mismatch"
    assert validation.repairable is False
    assert "business_result" in validation.mismatch_reasons[0]


def test_controlled_repair_prompt_follows_query_language() -> None:
    zh_contract = build_chart_request_contract("请画柱状图，横轴：合同，纵轴：客户数量", chart_requested=True)
    en_contract = build_chart_request_contract("show a bar chart with x=Contract and y=customer_count", chart_requested=True)

    zh_prompt = build_controlled_repair_prompt(zh_contract, {"合同": "合同", "客户数量": "客户数量"})
    en_prompt = build_controlled_repair_prompt(en_contract, {"Contract": "Contract", "customer_count": "customer_count"})

    assert "不要虚构字段" in zh_prompt
    assert "Do not invent fields" in en_prompt
