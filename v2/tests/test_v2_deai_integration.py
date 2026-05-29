"""
tests/test_v2_deai_integration.py — DEAI-1: 去 AI 味闭环集成测试

验证:
1. _tool_detect_ai_signals section 模式（通过 section 参数从 state 读取）
2. _tool_detect_ai_signals 默认模式（从已编辑 sections 聚合）
3. 迭代追踪: deai_check_count 递增, deai_last_result 更新
4. max 3 rounds 软限制行为
5. Completion Gate 的 deai_unchecked nudge（boundary_guard 集成）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field
from typing import List

from core.harness import Harness
from core.boundary_guard import check_completion_gate
from core.state import WorkspaceState
from core.gate_config import CompletionGateConfig
from core.finding_quality import FindingQualityGate


# ============================================================
# Mock DetectionResult for controlled testing
# ============================================================

def _make_mock_detection_result(verdict="FAIL", overall_score=0.65, signals=None):
    """创建模拟的 DetectionResult。"""
    from core.deai_detector import DetectionResult, AISignal

    if signals is None:
        signals = [
            AISignal(
                signal_type="RHYTHM_UNIFORMITY",
                tier="critical",
                confidence=0.85,
                description="句长过于均匀",
                fix_suggestion="混合使用长短句",
                evidence="This sentence is exactly fifteen words long. This one is also exactly fifteen words long.",
            ),
            AISignal(
                signal_type="THROAT_CLEARING",
                tier="major",
                confidence=0.72,
                description="开头存在典型 AI 套话",
                fix_suggestion="直接进入主题，删除铺垫",
                evidence="In this paper, we explore the fascinating intersection of...",
            ),
        ]

    result = DetectionResult(
        signals=signals,
        overall_score=overall_score,
        verdict=verdict,
        verdict_reason="Multiple AI signals detected" if verdict == "FAIL" else "All checks passed",
    )
    return result


def _make_pass_result():
    """创建 PASS 的检测结果。"""
    from core.deai_detector import DetectionResult
    return DetectionResult(
        signals=[],
        overall_score=0.95,
        verdict="PASS",
        verdict_reason="No significant AI signals detected",
    )


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def harness():
    """创建基本 harness 用于 DEAI 测试。"""
    with patch('core.harness._pl_load_paper'):
        h = Harness(paper_path="fake.pdf", max_loop_turns=50, enable_hdwm=False)
    h.state.paper_sections = {
        "introduction": "This is the introduction section with some content.",
        "methodology": "Here we describe our experimental methodology in detail.",
        "results": "The results show significant improvement across all metrics.",
        "discussion": "We discuss the implications of our findings here.",
    }
    h.state.sections_read = set()
    h.state.findings = []
    h.state.edits = []
    return h


# ============================================================
# Test: Section 模式
# ============================================================

class TestDetectAISignalsSectionMode:
    """测试通过 section 参数指定检测目标。"""

    def test_section_mode_reads_from_state(self, harness):
        """section 参数指定时，从 state.paper_sections 读取文本。"""
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_mock_detection_result()) as mock_detect:
            result = harness._tool_detect_ai_signals({"section": "introduction"})
            mock_detect.assert_called_once()
            called_text = mock_detect.call_args[0][0]
            assert "introduction section" in called_text
            assert "FAIL" in result

    def test_section_mode_fuzzy_match(self, harness):
        """section 名支持模糊匹配。"""
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_mock_detection_result()) as mock_detect:
            result = harness._tool_detect_ai_signals({"section": "intro"})
            mock_detect.assert_called_once()
            called_text = mock_detect.call_args[0][0]
            assert "introduction section" in called_text

    def test_section_not_found_error(self, harness):
        """section 不存在时返回错误信息。"""
        result = harness._tool_detect_ai_signals({"section": "nonexistent_section_xyz"})
        assert "错误" in result
        assert "未找到" in result

    def test_section_mode_with_text_overrides(self, harness):
        """同时传 text 和 section 时，text 优先。"""
        custom_text = "Custom text for detection"
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_pass_result()) as mock_detect:
            harness._tool_detect_ai_signals({"text": custom_text, "section": "introduction"})
            called_text = mock_detect.call_args[0][0]
            assert called_text == custom_text


# ============================================================
# Test: 默认模式（已编辑 sections 聚合）
# ============================================================

class TestDetectAISignalsDefaultMode:
    """测试未传 text/section 时的默认行为。"""

    def test_default_mode_uses_edited_sections(self, harness):
        """默认模式聚合所有已编辑 sections 的内容。"""
        harness.state.edits = [
            {"section": "introduction", "action": "reword"},
            {"section": "results", "action": "reword"},
        ]
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_mock_detection_result()) as mock_detect:
            result = harness._tool_detect_ai_signals({})
            mock_detect.assert_called_once()
            called_text = mock_detect.call_args[0][0]
            assert "introduction section" in called_text
            assert "results show" in called_text

    def test_default_mode_deduplicates_sections(self, harness):
        """多次编辑同一 section 不会重复检测。"""
        harness.state.edits = [
            {"section": "introduction", "action": "reword"},
            {"section": "introduction", "action": "fix_typo"},
            {"section": "results", "action": "reword"},
        ]
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_pass_result()) as mock_detect:
            harness._tool_detect_ai_signals({})
            called_text = mock_detect.call_args[0][0]
            # introduction 内容应只出现一次
            assert called_text.count("introduction section") == 1

    def test_default_mode_no_edits_error(self, harness):
        """无编辑记录时返回错误。"""
        harness.state.edits = []
        result = harness._tool_detect_ai_signals({})
        assert "错误" in result
        assert "编辑记录" in result or "text" in result

    def test_default_mode_skips_missing_sections(self, harness):
        """编辑记录中的 section 不在 paper_sections 时跳过。"""
        harness.state.edits = [
            {"section": "introduction", "action": "reword"},
            {"section": "deleted_section", "action": "reword"},
        ]
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_pass_result()) as mock_detect:
            harness._tool_detect_ai_signals({})
            called_text = mock_detect.call_args[0][0]
            assert "introduction section" in called_text
            # 不应包含 deleted_section 的内容（因为不在 paper_sections 中）


# ============================================================
# Test: 迭代追踪
# ============================================================

class TestDeaiIterationTracking:
    """测试 deai_check_count 和 deai_last_result 的追踪。"""

    def test_first_check_increments_counter(self, harness):
        """第一次检测后 deai_check_count 应为 1。"""
        assert harness.state.deai_check_count == 0
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_mock_detection_result()):
            harness._tool_detect_ai_signals({"text": "some text"})
        assert harness.state.deai_check_count == 1

    def test_counter_increments_each_call(self, harness):
        """每次调用都递增。"""
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_mock_detection_result()):
            harness._tool_detect_ai_signals({"text": "text1"})
            harness._tool_detect_ai_signals({"text": "text2"})
            harness._tool_detect_ai_signals({"text": "text3"})
        assert harness.state.deai_check_count == 3

    def test_last_result_updated(self, harness):
        """deai_last_result 在每次调用后更新为最新结果。"""
        result_1 = _make_mock_detection_result(verdict="FAIL", overall_score=0.45)
        result_2 = _make_mock_detection_result(verdict="CONDITIONAL_PASS", overall_score=0.78)

        with patch("core.deai_detector.detect_ai_signals", return_value=result_1):
            harness._tool_detect_ai_signals({"text": "text1"})
        assert harness.state.deai_last_result["verdict"] == "FAIL"
        assert harness.state.deai_last_result["overall_score"] == 0.45
        assert harness.state.deai_last_result["check_round"] == 1

        with patch("core.deai_detector.detect_ai_signals", return_value=result_2):
            harness._tool_detect_ai_signals({"text": "text2"})
        assert harness.state.deai_last_result["verdict"] == "CONDITIONAL_PASS"
        assert harness.state.deai_last_result["overall_score"] == 0.78
        assert harness.state.deai_last_result["check_round"] == 2

    def test_last_result_contains_signal_counts(self, harness):
        """deai_last_result 包含信号计数信息。"""
        result = _make_mock_detection_result()
        with patch("core.deai_detector.detect_ai_signals", return_value=result):
            harness._tool_detect_ai_signals({"text": "text"})
        lr = harness.state.deai_last_result
        assert "signal_count" in lr
        assert "critical_count" in lr
        assert "major_count" in lr
        assert lr["signal_count"] == 2
        assert lr["critical_count"] == 1
        assert lr["major_count"] == 1


# ============================================================
# Test: Max 3 Rounds 软限制
# ============================================================

class TestDeaiMaxRounds:
    """测试 _MAX_DEAI_CHECKS 的软限制行为。"""

    def test_output_shows_remaining_rounds(self, harness):
        """未达上限时，输出显示剩余轮数。"""
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_mock_detection_result()):
            result = harness._tool_detect_ai_signals({"text": "text"})
        assert "剩余 2 轮" in result

    def test_output_at_second_round(self, harness):
        """第 2 轮时显示剩余 1 轮。"""
        harness.state.deai_check_count = 1
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_mock_detection_result()):
            result = harness._tool_detect_ai_signals({"text": "text"})
        assert "剩余 1 轮" in result

    def test_output_at_max_rounds(self, harness):
        """达到最大轮次时，输出限制提示。"""
        harness.state.deai_check_count = 2  # 下次调用后变为 3
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_mock_detection_result()):
            result = harness._tool_detect_ai_signals({"text": "text"})
        assert "最大轮次" in result
        assert harness.state.deai_check_count == 3

    def test_pass_result_shows_no_further_action(self, harness):
        """PASS 结果不显示剩余轮数，而是显示"无需修改"。"""
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_pass_result()):
            result = harness._tool_detect_ai_signals({"text": "clean text"})
        assert "无需进一步修改" in result
        # 不应显示"剩余 N 轮"
        assert "剩余" not in result

    def test_beyond_max_rounds_still_works(self, harness):
        """超过最大轮次后仍可调用（软限制不阻止）。"""
        harness.state.deai_check_count = 3  # 已达上限
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_mock_detection_result()):
            result = harness._tool_detect_ai_signals({"text": "text"})
        # 应仍能执行，counter 继续递增
        assert harness.state.deai_check_count == 4
        assert "最大轮次" in result


# ============================================================
# Test: 增强输出格式
# ============================================================

class TestDeaiEnhancedOutput:
    """测试增强输出包含可操作修改建议。"""

    def test_output_contains_fix_suggestions(self, harness):
        """输出包含修改建议。"""
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_mock_detection_result()):
            result = harness._tool_detect_ai_signals({"text": "some text"})
        assert "可操作修改建议" in result
        assert "混合使用长短句" in result
        assert "直接进入主题" in result

    def test_output_contains_evidence_location(self, harness):
        """输出包含定位信息。"""
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_mock_detection_result()):
            result = harness._tool_detect_ai_signals({"text": "some text"})
        assert "定位" in result

    def test_output_contains_iteration_progress(self, harness):
        """输出包含迭代进度信息。"""
        with patch("core.deai_detector.detect_ai_signals", return_value=_make_mock_detection_result()):
            result = harness._tool_detect_ai_signals({"text": "some text"})
        assert "de-AI 迭代进度" in result
        assert "第 1 轮" in result

    def test_output_truncates_many_signals(self, harness):
        """信号过多时截断显示。"""
        from core.deai_detector import AISignal
        many_signals = [
            AISignal(
                signal_type=f"SIGNAL_{i}",
                tier="major",
                confidence=0.7,
                description=f"Signal {i}",
                fix_suggestion=f"Fix {i}",
                evidence=f"Evidence {i}",
            )
            for i in range(10)
        ]
        result_obj = _make_mock_detection_result(signals=many_signals)
        with patch("core.deai_detector.detect_ai_signals", return_value=result_obj):
            result = harness._tool_detect_ai_signals({"text": "some text"})
        assert "还有 4 个信号" in result  # 显示 6 个，剩余 4 个


# ============================================================
# Test: Completion Gate DEAI nudge
# NOTE: deai_unchecked in completion_gate 不在当前 REPAIR_PLAN 范围内
# 参见 REPAIR_PLAN.md "不纳入本次修复" 第 4 条
# ============================================================

class TestCompletionGateDeaiNudge:
    """测试 check_completion_gate 中的 deai_unchecked nudge。"""

    @pytest.fixture
    def gate_deps(self):
        """创建 completion gate 所需的依赖。"""
        gate_config = CompletionGateConfig()
        finding_quality_gate = FindingQualityGate()
        return gate_config, finding_quality_gate

    def test_nudge_fires_when_edits_but_no_check(self, gate_deps):
        """有编辑但未检查时触发 nudge。"""
        gate_config, fqg = gate_deps
        state = WorkspaceState()
        state.edits = [{"section": "introduction", "action": "reword"}]
        state.deai_check_count = 0
        state.findings = []  # 确保其他 nudge 不触发

        nudges_fired: set[str] = set()
        msg, nudges_fired = check_completion_gate(
            state=state,
            gate_config=gate_config,
            hypothesis_module=None,
            finding_quality_gate=fqg,
            completion_nudges_fired=nudges_fired,
        )
        assert msg is not None
        assert "detect_ai_signals" in msg
        assert "deai_unchecked" in nudges_fired

    def test_nudge_not_fired_when_check_done(self, gate_deps):
        """已执行过检查时不触发。"""
        gate_config, fqg = gate_deps
        state = WorkspaceState()
        state.edits = [{"section": "introduction", "action": "reword"}]
        state.deai_check_count = 1  # 已检查过
        state.findings = []

        nudges_fired: set[str] = set()
        msg, nudges_fired = check_completion_gate(
            state=state,
            gate_config=gate_config,
            hypothesis_module=None,
            finding_quality_gate=fqg,
            completion_nudges_fired=nudges_fired,
        )
        # deai nudge 不应触发（可能返回 None 或其他 nudge）
        assert "deai_unchecked" not in nudges_fired

    def test_nudge_not_fired_when_no_edits(self, gate_deps):
        """没有编辑记录时不触发。"""
        gate_config, fqg = gate_deps
        state = WorkspaceState()
        state.edits = []
        state.deai_check_count = 0
        state.findings = []

        nudges_fired: set[str] = set()
        msg, nudges_fired = check_completion_gate(
            state=state,
            gate_config=gate_config,
            hypothesis_module=None,
            finding_quality_gate=fqg,
            completion_nudges_fired=nudges_fired,
        )
        assert "deai_unchecked" not in nudges_fired

    def test_nudge_fires_only_once(self, gate_deps):
        """nudge 只触发一次（第二次调用放行）。"""
        gate_config, fqg = gate_deps
        state = WorkspaceState()
        state.edits = [{"section": "results", "action": "reword"}]
        state.deai_check_count = 0
        state.findings = []

        nudges_fired: set[str] = set()
        # 第一次: 触发
        msg1, nudges_fired = check_completion_gate(
            state=state,
            gate_config=gate_config,
            hypothesis_module=None,
            finding_quality_gate=fqg,
            completion_nudges_fired=nudges_fired,
        )
        assert msg1 is not None
        assert "deai_unchecked" in nudges_fired

        # 第二次: 不再触发（已在 fired 集合中）
        msg2, nudges_fired = check_completion_gate(
            state=state,
            gate_config=gate_config,
            hypothesis_module=None,
            finding_quality_gate=fqg,
            completion_nudges_fired=nudges_fired,
        )
        # msg2 可能为 None（所有 nudge 都已 fired）
        # 关键是不会重复触发 deai
        if msg2 is not None:
            assert "detect_ai_signals" not in msg2

    def test_nudge_message_lists_edited_sections(self, gate_deps):
        """nudge 消息中列出已编辑的 sections。"""
        gate_config, fqg = gate_deps
        state = WorkspaceState()
        state.edits = [
            {"section": "introduction", "action": "reword"},
            {"section": "discussion", "action": "restructure"},
        ]
        state.deai_check_count = 0
        state.findings = []

        nudges_fired: set[str] = set()
        msg, _ = check_completion_gate(
            state=state,
            gate_config=gate_config,
            hypothesis_module=None,
            finding_quality_gate=fqg,
            completion_nudges_fired=nudges_fired,
        )
        assert "introduction" in msg or "discussion" in msg
