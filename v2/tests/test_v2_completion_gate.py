"""
tests/test_v2_completion_gate.py — Phase 7: Completion Gate 单元测试

验证 _check_completion_gate 的信号式检查逻辑：
1. 未验证高优发现
2. HD-WM 活跃假说
以及防死循环机制（每类 nudge 最多触发一次）

设计原则: 不设硬性数量门槛。Agent 自主判断何时审完。
Gate 只检查"你确定收尾了吗？"的状态信号。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from unittest.mock import patch
from core.harness import Harness
from core.hypothesis import HypothesisModule, HypothesisStatus


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def harness_basic():
    """创建一个基本 harness（无 HD-WM）。"""
    with patch('core.harness._pl_load_paper'):
        h = Harness(paper_path="fake.pdf", max_loop_turns=50, enable_hdwm=False)
    h.state.paper_sections = {f"section_{i}": f"content_{i}" for i in range(42)}
    h.state.sections_read = set()
    h.state.findings = []
    return h


@pytest.fixture
def harness_with_hdwm():
    """创建一个启用 HD-WM 的 harness。"""
    with patch('core.harness._pl_load_paper'):
        h = Harness(paper_path="fake.pdf", max_loop_turns=50, enable_hdwm=True)
    h.state.paper_sections = {f"section_{i}": f"content_{i}" for i in range(42)}
    h.state.sections_read = set()
    h.state.findings = []
    return h


# ============================================================
# 基本通过条件: 无未收尾工作时直接放行
# ============================================================

class TestCompletionGatePassThrough:
    """Gate 在无问题时直接放行"""

    def test_passes_with_no_findings(self, harness_basic):
        """没有 findings（论文本身没问题）→ 直接通过"""
        result = harness_basic._check_completion_gate()
        assert result is None

    def test_passes_with_verified_findings(self, harness_basic):
        """所有 findings 都 verified 且质量合格 → 通过"""
        h = harness_basic
        h.state.findings = [
            {"finding": "DID identification should include pre-trend test", "priority": "high", "status": "verified",
             "evidence": "Table 3 shows no pre-trend test results, which weakens the parallel trends assumption."},
            {"finding": "Sample size is adequate for subgroup analysis", "priority": "medium", "status": "verified",
             "evidence": "Section 4.2 reports N=12,000 observations across 6 subgroups."},
        ]
        result = h._check_completion_gate()
        assert result is None

    def test_passes_with_low_priority_unverified(self, harness_basic):
        """低优先级 needs_verification → 不阻止（unverified 检查只看 high）"""
        h = harness_basic
        h.state.findings = [
            {"finding": "Minor typo in equation numbering on page 7", "priority": "low", "status": "needs_verification",
             "evidence": "Equation 3 is referenced as Eq. 4 in paragraph 2 of Section 3."},
        ]
        result = h._check_completion_gate()
        assert result is None


# ============================================================
# 未验证高优发现
# ============================================================

class TestCompletionGateUnverified:
    """高优先级 needs_verification 检查"""

    def test_blocks_when_unverified_high_exists(self, harness_basic):
        """有 high + needs_verification → 触发 nudge"""
        h = harness_basic
        h.state.findings = [
            {"finding": "important issue", "priority": "high", "status": "needs_verification"},
        ]
        result = h._check_completion_gate()
        assert result is not None
        assert "needs_verification" in result

    def test_nudge_fires_only_once(self, harness_basic):
        """同一类 nudge 只触发一次"""
        h = harness_basic
        h.state.findings = [
            {"finding": "Identification strategy should address reverse causality", "priority": "high", "status": "needs_verification",
             "evidence": "Section 3.1 uses OLS without instrumental variable, raising endogeneity concerns."},
        ]
        result1 = h._check_completion_gate()
        assert result1 is not None
        assert "needs_verification" in result1
        # 第二次: unverified 不再触发（已 fired），quality_check 也不应触发（evidence 充分）
        result2 = h._check_completion_gate()
        assert result2 is None


# ============================================================
# HD-WM 活跃假说
# ============================================================

class TestCompletionGateHDWM:
    """HD-WM 活跃假说检查"""

    def test_blocks_when_active_hypothesis_exists(self, harness_with_hdwm):
        """有 ACTIVE 假说 → 触发 nudge"""
        h = harness_with_hdwm
        h.hypothesis_module.generate("test hypothesis", source="test")
        result = h._check_completion_gate()
        assert result is not None
        assert "待验证判断" in result or "test hypothesis" in result

    def test_passes_when_all_hypotheses_resolved(self, harness_with_hdwm):
        """所有假说都 resolved → 通过"""
        h = harness_with_hdwm
        hyp = h.hypothesis_module.generate("test hypothesis", source="test")
        h.hypothesis_module.resolve(hyp.id, "supported", "evidence confirms it")
        result = h._check_completion_gate()
        assert result is None

    def test_no_hdwm_skipped(self, harness_basic):
        """HD-WM 未启用 → 不触发"""
        result = harness_basic._check_completion_gate()
        assert result is None

    def test_hdwm_nudge_fires_only_once(self, harness_with_hdwm):
        """HD-WM nudge 只触发一次"""
        h = harness_with_hdwm
        h.hypothesis_module.generate("hyp1", source="test")
        r1 = h._check_completion_gate()
        assert r1 is not None
        r2 = h._check_completion_gate()
        assert r2 is None


# ============================================================
# 多层按序触发
# ============================================================

class TestCompletionGateSequential:
    """多层 nudge 按优先级依次触发"""

    def test_unverified_before_hdwm(self, harness_with_hdwm):
        """未验证高优发现优先于 HD-WM 假说"""
        h = harness_with_hdwm
        h.state.findings = [
            {"finding": "Identification strategy should address reverse causality", "priority": "high", "status": "needs_verification",
             "evidence": "Section 3 uses OLS without IV, suggesting potential endogeneity issues remain."},
        ]
        h.hypothesis_module.generate("hyp1", source="test")

        # Call 1: unverified 触发
        r1 = h._check_completion_gate()
        assert "needs_verification" in r1

        # Call 2: hdwm 触发
        r2 = h._check_completion_gate()
        assert "待验证判断" in r2 or "hyp1" in r2

        # Call 3: 全部跳过（quality_check 也不应触发因为 evidence 充分），放行
        r3 = h._check_completion_gate()
        assert r3 is None

    def test_no_nudge_when_everything_clean(self, harness_with_hdwm):
        """所有状态干净时直接放行，即使 Agent 只审了一小部分"""
        h = harness_with_hdwm
        # 只读了 1 个 section，1 条 finding — 这完全可以
        h.state.sections_read = {"section_0"}
        h.state.findings = [
            {"finding": "Paper is methodologically sound with minor presentation issues", "priority": "medium", "status": "verified",
             "evidence": "All key robustness checks in Appendix B confirm the main results hold."},
        ]
        result = h._check_completion_gate()
        assert result is None
