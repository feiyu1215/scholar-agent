"""
tests/test_finding_dedup.py — Phase P1: Finding 去重增强单元测试。

测试 check_finding_overlap 的多信号融合去重逻辑：
1. 同一问题不同措辞应被去重
2. 状态升级应原地更新而非追加
3. 不同问题不应被误判为重复
4. 数字/表格引用重叠应降低术语匹配阈值
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.tool_handlers.findings import check_finding_overlap


# ============================================================
# MockState — 最小化 state 对象
# ============================================================

class MockState:
    def __init__(self, findings=None):
        self.findings = findings or []
        self.loop_turns = 1


# ============================================================
# Test Cases
# ============================================================

def test_same_finding_different_wording():
    """同一问题不同措辞应被去重（术语重叠+数字+同section）。"""
    state = MockState(findings=[
        {"finding": "Table 2 coefficient 0.067 conversion to percentage point 6.7% is unclear and not transparent",
         "section": "results", "status": "needs_verification", "priority": "high"}
    ])
    new = {"finding": "The coefficient 0.067 in Table 2 lacks explicit conversion step to percentage point 6.7% claim",
           "section": "results", "status": "needs_verification", "priority": "high"}
    result = check_finding_overlap(new, state, False, None)
    assert result is not None  # 应被拦截
    assert "未记录" in result or "已更新" in result or "补充" in result


def test_status_upgrade_updates_in_place():
    """状态升级应更新原记录而非追加。"""
    state = MockState(findings=[
        {"finding": "identification assumption quasi-random assignment 可能被违反",
         "section": "methodology", "status": "needs_verification", "priority": "high"}
    ])
    new = {"finding": "quasi-random assignment 已验证：identification assumption 确实被违反",
           "section": "methodology", "status": "verified", "priority": "high",
           "evidence": "搜索结果表明..."}
    result = check_finding_overlap(new, state, False, None)
    assert result is not None
    assert "已更新" in result
    assert state.findings[0]["status"] == "verified"
    assert len(state.findings) == 1  # 不应追加


def test_different_findings_not_blocked():
    """不同问题不应被误判为重复。"""
    state = MockState(findings=[
        {"finding": "identification via quasi-random assignment",
         "section": "methodology", "status": "verified", "priority": "high"}
    ])
    new = {"finding": "external validity concerns: single hospital setting",
           "section": "discussion", "status": "suggestion", "priority": "medium"}
    result = check_finding_overlap(new, state, False, None)
    assert result is None  # 应允许通过


def test_numeric_refs_boost_detection():
    """共同引用相同数字/表格应降低术语匹配阈值。"""
    state = MockState(findings=[
        {"finding": "Table 3 reports treatment effect coefficient 0.045 standard deviation units but the text claims 4.5% improvement",
         "section": "results", "status": "needs_verification", "priority": "high"}
    ])
    new = {"finding": "The treatment effect coefficient 0.045 in Table 3 appears inconsistent with stated percentage improvement claim",
           "section": "results", "status": "needs_verification", "priority": "high"}
    result = check_finding_overlap(new, state, False, None)
    assert result is not None  # 数字重叠+术语重叠应触发去重


def test_evidence_appended_on_duplicate():
    """同状态重复但有新证据时，证据应追加到原记录。"""
    state = MockState(findings=[
        {"finding": "bandwidth selection 200km seems arbitrary for identification strategy",
         "section": "methodology", "status": "needs_verification", "priority": "high",
         "evidence": ""}
    ])
    new = {"finding": "bandwidth 200km arbitrary choice for identification",
           "section": "methodology", "status": "needs_verification", "priority": "high",
           "evidence": "文献显示该领域常用 100-150km"}
    result = check_finding_overlap(new, state, False, None)
    assert result is not None
    assert "补充证据" in result or "追加" in result
    assert state.findings[0]["evidence"] == "文献显示该领域常用 100-150km"
    assert len(state.findings) == 1


def test_status_downgrade_blocked():
    """状态降级应被阻止。"""
    state = MockState(findings=[
        {"finding": "parallel trends assumption for difference-in-differences identification strategy is clearly violated based on pre-treatment period analysis",
         "section": "methodology", "status": "verified", "priority": "high"}
    ])
    new = {"finding": "parallel trends assumption for difference-in-differences identification strategy may be violated and needs further verification",
           "section": "methodology", "status": "needs_verification", "priority": "high"}
    result = check_finding_overlap(new, state, False, None)
    assert result is not None
    assert "状态更高" in result
    assert state.findings[0]["status"] == "verified"  # 不变


def test_short_finding_passes_through():
    """极短的 finding（术语 < 3）不应参与去重，直接放行。"""
    state = MockState(findings=[
        {"finding": "bad typo", "section": "abstract", "status": "suggestion", "priority": "low"}
    ])
    new = {"finding": "bad typo", "section": "abstract", "status": "suggestion", "priority": "low"}
    result = check_finding_overlap(new, state, False, None)
    assert result is None  # 术语不足，不参与去重


def test_pure_term_overlap_70_percent():
    """纯术语重叠 >= 70% 时（无数字引用、不同 section），应触发去重。"""
    state = MockState(findings=[
        {"finding": "regression discontinuity bandwidth selection conservative reduces statistical power substantially",
         "section": "methodology", "status": "needs_verification", "priority": "high"}
    ])
    new = {"finding": "bandwidth selection regression discontinuity conservative reduces statistical power significantly",
           "section": "results", "status": "needs_verification", "priority": "high"}
    # 术语重叠 88%（8/9），无数字，不同 section → 走纯术语条件 1
    result = check_finding_overlap(new, state, False, None)
    assert result is not None  # 纯术语 >= 70% 应触发
