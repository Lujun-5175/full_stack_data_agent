from __future__ import annotations

from full_stack_data_agent.app.binding_intent import parse_binding_intent
from full_stack_data_agent.app.binding_models import BindingAction


def test_chart_cues_still_match_explicit_chart_requests() -> None:
    assert BindingAction.SHOW_CHART in parse_binding_intent("请画图").requested_actions
    assert BindingAction.SHOW_CHART in parse_binding_intent("给我一个图表").requested_actions
    assert BindingAction.SHOW_CHART in parse_binding_intent("可视化一下").requested_actions


def test_chart_cue_does_not_trigger_on_generic_tu_character() -> None:
    for query in ("上传图片", "打开地图", "这个意图是什么", "推荐图书"):
        assert BindingAction.SHOW_CHART not in parse_binding_intent(query).requested_actions
