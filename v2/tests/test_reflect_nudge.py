"""
tests/test_reflect_nudge.py — Phase P1: Reflect Nudge 条件 C 集成测试。

测试当 agent 搜索过文献但有未校准的高优方法论 finding 时，
reflect_and_plan 应产生方法论校准 nudge。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.tool_reflect import reflect_and_plan
from core.state import WorkspaceState
from core.metacognition import CognitiveState


# ============================================================
# Test: 方法论未校准 nudge 触发
# ============================================================

def test_uncalibrated_methodology_nudge():
    """搜索过但有未校准的高优方法论 finding 时应触发 nudge。"""
    state = WorkspaceState()
    state.findings = [
        {"finding": "bandwidth selection 200km seems arbitrary for identification",
         "priority": "high", "section": "methodology", "status": "needs_verification"},
        {"finding": "minor typo in abstract",
         "priority": "low", "section": "abstract", "status": "suggestion"},
    ]
    state.sections_read = ["abstract", "methodology", "results"]
    state.paper_sections = {"abstract": "...", "methodology": "...", "results": "...", "discussion": "..."}
    state.loop_turns = 5
    state.total_tokens = 50000

    cognitive_state = CognitiveState()

    # 搜索过但与方法论 finding 无关的查询
    search_log = [{"query": "novelty verification first to study X"}]

    gate_config = None
    args = {"trigger": "自主反思"}

    output, _ = reflect_and_plan(state, cognitive_state, [], "", search_log, gate_config, args)
    assert "方法论判断" in output
    assert "大概记得" in output


def test_no_nudge_when_search_covers_finding():
    """当搜索历史已覆盖方法论 finding 时，不应触发 nudge。"""
    state = WorkspaceState()
    state.findings = [
        {"finding": "bandwidth selection 200km seems arbitrary for identification strategy",
         "priority": "high", "section": "methodology", "status": "needs_verification"},
    ]
    state.sections_read = ["abstract", "methodology", "results"]
    state.paper_sections = {"abstract": "...", "methodology": "...", "results": "...", "discussion": "..."}
    state.loop_turns = 5
    state.total_tokens = 50000

    cognitive_state = CognitiveState()

    # 搜索了与 finding 直接相关的内容（多个词重叠）
    search_log = [{"query": "bandwidth selection identification strategy spatial economics"}]

    gate_config = None
    args = {"trigger": "自主反思"}

    output, _ = reflect_and_plan(state, cognitive_state, [], "", search_log, gate_config, args)
    # 不应触发方法论 nudge
    assert "方法论判断" not in output


def test_no_nudge_when_zero_search():
    """零搜索时走条件 A/B，不走条件 C。"""
    state = WorkspaceState()
    state.findings = [
        {"finding": "bandwidth selection arbitrary for identification",
         "priority": "high", "section": "methodology", "status": "needs_verification"},
        {"finding": "robustness check insufficient for estimation validity",
         "priority": "high", "section": "results", "status": "needs_verification"},
    ]
    state.sections_read = ["abstract", "methodology", "results", "discussion"]
    state.paper_sections = {"abstract": "...", "methodology": "...", "results": "...", "discussion": "..."}
    state.loop_turns = 5
    state.total_tokens = 50000

    cognitive_state = CognitiveState()

    search_log = []  # 零搜索

    gate_config = None
    args = {"trigger": "自主反思"}

    output, _ = reflect_and_plan(state, cognitive_state, [], "", search_log, gate_config, args)
    # 走条件 B（零搜索 + 4+ sections），不是条件 C
    assert "尚未查过外部文献" in output
    assert "方法论判断" not in output


def test_no_nudge_when_findings_less_than_two():
    """搜索过但 findings < 2 时，条件 C 不触发。"""
    state = WorkspaceState()
    state.findings = [
        {"finding": "bandwidth selection arbitrary for identification",
         "priority": "high", "section": "methodology", "status": "needs_verification"},
    ]
    state.sections_read = ["abstract", "methodology"]
    state.paper_sections = {"abstract": "...", "methodology": "...", "results": "..."}
    state.loop_turns = 3
    state.total_tokens = 30000

    cognitive_state = CognitiveState()

    # 有搜索记录，但只有 1 个 finding
    search_log = [{"query": "novelty check for study X"}]

    gate_config = None
    args = {"trigger": "自主反思"}

    output, _ = reflect_and_plan(state, cognitive_state, [], "", search_log, gate_config, args)
    assert "方法论判断" not in output


def test_no_nudge_when_all_low_priority():
    """搜索过且 findings >= 2 但全是 low priority 时，条件 C 不触发。"""
    state = WorkspaceState()
    state.findings = [
        {"finding": "bandwidth selection arbitrary for identification",
         "priority": "low", "section": "methodology", "status": "suggestion"},
        {"finding": "robustness check could be improved for estimation validity",
         "priority": "low", "section": "results", "status": "suggestion"},
    ]
    state.sections_read = ["abstract", "methodology", "results"]
    state.paper_sections = {"abstract": "...", "methodology": "...", "results": "...", "discussion": "..."}
    state.loop_turns = 5
    state.total_tokens = 50000

    cognitive_state = CognitiveState()

    search_log = [{"query": "novelty verification first to study X"}]

    gate_config = None
    args = {"trigger": "自主反思"}

    output, _ = reflect_and_plan(state, cognitive_state, [], "", search_log, gate_config, args)
    assert "方法论判断" not in output
