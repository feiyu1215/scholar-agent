"""
tests/test_two_phase_spawn.py — 两阶段 spawn 调度测试

覆盖:
1. Phase 1 (role-based): 进度 15% 时触发，建议不同审稿视角
2. Phase 2 (content-specific): 进度 50% 时触发，对 needs_verification findings 做验证
3. 两阶段独立触发（各自只一次）
4. Fallback: CognitiveHints 为空时的通用提醒（不含关键词启发式）
5. _build_role_based_spawn_plan 纯 CognitiveHints 驱动
6. _build_verify_spawn_plan 基于 findings + tool_call_history
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.boundary_guard import (
    check_auto_spawn_needed,
    _build_role_based_spawn_plan,
    _build_verify_spawn_plan,
)


# ============================================================
# Helpers
# ============================================================

def make_state(
    loop_turns=0,
    max_loop_turns=50,
    paper_sections=None,
    cognitive_hints=None,
    findings=None,
):
    """创建 mock WorkspaceState。"""
    state = MagicMock()
    state.loop_turns = loop_turns
    state.max_loop_turns = max_loop_turns
    state.paper_sections = paper_sections or {
        "introduction": "This paper studies the effect of X on Y using DID...",
        "methodology": "We employ a difference-in-differences with assumption that...",
        "results": "Table 1 reports the main results. Column (1) shows OLS estimate...",
        "robustness": "Table 2 confirms robustness to alternative specifications...",
        "conclusion": "We find that X significantly affects Y...",
    }
    state.findings = findings or []

    # CognitiveHints mock
    if cognitive_hints is None:
        hints = MagicMock()
        hints.is_empty.return_value = False
        hints.focus_dimensions = [
            "平行趋势假设验证（pre-trend test）",
            "内生性问题与工具变量有效性",
            "样本选择偏误与外推性",
            "统计推断（聚类标准误选择）",
        ]
        hints.typical_weaknesses = [
            "DID论文常见的处理组/控制组composition变化",
            "估计量对窗口期选择的敏感性",
        ]
        hints.verification_strategies = [
            "逐表对比baseline和robustness的系数变化幅度",
            "交叉验证各表的标准误聚类层级是否一致",
        ]
        state.cognitive_hints = hints
    else:
        state.cognitive_hints = cognitive_hints

    # sections_read: 默认已读所有 sections（Fallback 测试中可覆盖）
    state.sections_read = list(state.paper_sections.keys())

    # 三阶段 flag 初始为 False
    state._role_spawn_nudge_fired = False
    state._verify_spawn_nudge_fired = False
    state._fallback_spawn_nudge_fired = False

    return state


# ============================================================
# 1. Phase 1: Role-Based Spawn
# ============================================================

class TestPhase1RoleBasedSpawn:
    """阶段 1：进度 ~15% 时建议 role-based spawn。"""

    def test_fires_at_15_percent_progress(self):
        state = make_state(loop_turns=8, max_loop_turns=50)  # 16%
        result = check_auto_spawn_needed(state, "deep_review", [])
        assert result is not None
        assert "多视角 Spawn 建议" in result
        assert state._role_spawn_nudge_fired is True

    def test_not_fire_before_15_percent(self):
        state = make_state(loop_turns=5, max_loop_turns=50)  # 10%
        result = check_auto_spawn_needed(state, "deep_review", [])
        assert result is None
        assert state._role_spawn_nudge_fired is False

    def test_not_fire_if_already_spawned(self):
        state = make_state(loop_turns=8, max_loop_turns=50)
        tool_history = [{"name": "spawn_parallel_readers"}]
        result = check_auto_spawn_needed(state, "deep_review", tool_history)
        assert result is None

    def test_only_fires_once(self):
        state = make_state(loop_turns=8, max_loop_turns=50)
        r1 = check_auto_spawn_needed(state, "deep_review", [])
        assert r1 is not None
        # 再次调用应不再触发
        state.loop_turns = 10
        r2 = check_auto_spawn_needed(state, "deep_review", [])
        assert r2 is None

    def test_not_fire_in_other_phases(self):
        state = make_state(loop_turns=8, max_loop_turns=50)
        assert check_auto_spawn_needed(state, "initial_scan", []) is None
        assert check_auto_spawn_needed(state, "synthesis", []) is None

    def test_not_fire_with_few_sections(self):
        state = make_state(
            loop_turns=8,
            max_loop_turns=50,
            paper_sections={"intro": "short", "method": "short", "results": "short"},
        )
        result = check_auto_spawn_needed(state, "deep_review", [])
        assert result is None  # < 4 sections

    def test_suggests_reviewer_roles_from_focus_dimensions(self):
        state = make_state(loop_turns=8, max_loop_turns=50)
        result = check_auto_spawn_needed(state, "deep_review", [])
        assert "reviewer" in result
        assert "认知分析" in result or "认知框架" in result

    def test_marks_fired_even_when_hints_empty(self):
        """CognitiveHints 为空时，Phase 1 标记为已触发但不返回消息。"""
        empty_hints = MagicMock()
        empty_hints.is_empty.return_value = True
        empty_hints.focus_dimensions = []
        empty_hints.typical_weaknesses = []
        empty_hints.verification_strategies = []

        state = make_state(
            loop_turns=8,
            max_loop_turns=50,
            cognitive_hints=empty_hints,
        )
        result = check_auto_spawn_needed(state, "deep_review", [])
        # hints 为空 → suggestions 为空 → 不返回消息
        # 但 _role_spawn_nudge_fired 应被标记为 True
        assert state._role_spawn_nudge_fired is True
        assert result is None


# ============================================================
# 2. Phase 2: Content-Specific Verify Spawn
# ============================================================

class TestPhase2VerifySpawn:
    """阶段 2：进度 ~50% 时建议 content-specific 验证。"""

    def test_fires_at_50_percent_with_unverified_findings(self):
        findings = [
            {"finding": "表1中的DID系数0.032与表3的0.031不一致", "status": "needs_verification", "priority": "high", "section": "results"},
            {"finding": "标准误聚类层级在表1和表2中不同", "status": "needs_verification", "priority": "medium", "section": "methodology"},
            {"finding": "样本量与描述性统计不吻合", "status": "needs_verification", "priority": "high", "section": "results"},
        ]
        state = make_state(loop_turns=23, max_loop_turns=50, findings=findings)  # 46%
        state._role_spawn_nudge_fired = True  # 阶段 1 已触发
        tool_history = [{"name": "spawn_parallel_readers"}]  # 已 spawn 过

        result = check_auto_spawn_needed(state, "deep_review", tool_history)
        assert result is not None
        assert "逐行验证 Spawn 建议" in result
        assert state._verify_spawn_nudge_fired is True

    def test_not_fire_without_prior_spawn(self):
        findings = [
            {"finding": "x" * 20, "status": "needs_verification", "priority": "high", "section": "a"},
            {"finding": "y" * 20, "status": "needs_verification", "priority": "high", "section": "b"},
        ]
        state = make_state(loop_turns=25, max_loop_turns=50, findings=findings)
        state._role_spawn_nudge_fired = True
        # spawn_count = 0
        result = check_auto_spawn_needed(state, "deep_review", [])
        # 应该不触发 phase 2（需要 spawn_count >= 1）
        if result is not None:
            assert "逐行验证" not in result

    def test_not_fire_if_few_unverified(self):
        findings = [
            {"finding": "only one unverified item here", "status": "needs_verification", "priority": "high", "section": "a"},
        ]
        state = make_state(loop_turns=25, max_loop_turns=50, findings=findings)
        state._role_spawn_nudge_fired = True
        tool_history = [{"name": "spawn_parallel_readers"}]
        result = check_auto_spawn_needed(state, "deep_review", tool_history)
        # 只有 1 条 unverified，< 2 不触发
        assert result is None or "逐行验证" not in (result or "")

    def test_only_fires_once(self):
        findings = [
            {"finding": "f" * 20, "status": "needs_verification", "priority": "high", "section": "a"},
            {"finding": "g" * 20, "status": "needs_verification", "priority": "medium", "section": "b"},
        ]
        state = make_state(loop_turns=25, max_loop_turns=50, findings=findings)
        state._role_spawn_nudge_fired = True
        tool_history = [{"name": "spawn_parallel_readers"}]
        r1 = check_auto_spawn_needed(state, "deep_review", tool_history)
        assert r1 is not None
        state.loop_turns = 30
        r2 = check_auto_spawn_needed(state, "deep_review", tool_history)
        assert r2 is None


# ============================================================
# 3. Fallback（CognitiveHints 为空时的通用提醒）
# ============================================================

class TestFallbackSpawn:
    """Fallback: Phase 1 已触发但 CognitiveHints 为空（无具体建议）时的通用提醒。

    触发条件（H1 修复后）:
        - state._role_spawn_nudge_fired == True（Phase 1 已标记）
        - state._fallback_spawn_nudge_fired == False
        - progress >= 30%
        - spawn_count == 0（Agent 仍未 spawn）

    设计意图：Phase 1 因 CognitiveHints 为空而标记 fired 但没给出具体建议，
    到 30% 进度时给 Agent 一个通用提醒（不含具体视角）。
    """

    def test_fallback_fires_after_phase1_empty_hints(self):
        """Phase 1 因 hints 为空标记 fired 但无消息 → 30% 时 Fallback 触发。"""
        empty_hints = MagicMock()
        empty_hints.is_empty.return_value = True
        empty_hints.focus_dimensions = []
        empty_hints.typical_weaknesses = []
        empty_hints.verification_strategies = []

        state = make_state(
            loop_turns=16,  # 32%
            max_loop_turns=50,
            cognitive_hints=empty_hints,
        )
        # 模拟 Phase 1 已经在 15% 时触发过（hints 为空 → 标记 fired 但无消息）
        state._role_spawn_nudge_fired = True
        state._fallback_spawn_nudge_fired = False

        result = check_auto_spawn_needed(state, "deep_review", [])
        assert result is not None
        assert "Spawn 时机提示" in result
        assert state._fallback_spawn_nudge_fired is True

    def test_fallback_not_fire_if_role_not_fired(self):
        """Phase 1 未触发时，Fallback 不触发（需要 Phase 1 先标记）。"""
        state = make_state(loop_turns=16, max_loop_turns=50)
        state._role_spawn_nudge_fired = False
        state._fallback_spawn_nudge_fired = False
        # 但 hints 非空 + 进度 > 15% → Phase 1 会先触发并给出具体建议
        result = check_auto_spawn_needed(state, "deep_review", [])
        # Phase 1 触发了（hints 非空 → 有具体建议）
        assert "多视角 Spawn 建议" in result
        assert state._role_spawn_nudge_fired is True
        # Fallback 不会触发（Phase 1 已给出具体建议，Agent 有信息可用）

    def test_fallback_not_fire_if_already_spawned(self):
        """Agent 已经 spawn 过时，Fallback 不触发。"""
        empty_hints = MagicMock()
        empty_hints.is_empty.return_value = True
        empty_hints.focus_dimensions = []
        empty_hints.typical_weaknesses = []
        empty_hints.verification_strategies = []

        state = make_state(
            loop_turns=16,
            max_loop_turns=50,
            cognitive_hints=empty_hints,
        )
        state._role_spawn_nudge_fired = True
        state._fallback_spawn_nudge_fired = False
        tool_history = [{"name": "spawn_parallel_readers"}]  # 已 spawn

        result = check_auto_spawn_needed(state, "deep_review", tool_history)
        # spawn_count >= 1 → Fallback 条件不满足
        assert result is None or "Spawn 时机提示" not in (result or "")

    def test_fallback_only_fires_once(self):
        """Fallback 只触发一次。"""
        empty_hints = MagicMock()
        empty_hints.is_empty.return_value = True
        empty_hints.focus_dimensions = []
        empty_hints.typical_weaknesses = []
        empty_hints.verification_strategies = []

        state = make_state(
            loop_turns=16,
            max_loop_turns=50,
            cognitive_hints=empty_hints,
        )
        state._role_spawn_nudge_fired = True
        state._fallback_spawn_nudge_fired = False

        r1 = check_auto_spawn_needed(state, "deep_review", [])
        assert r1 is not None
        assert state._fallback_spawn_nudge_fired is True

        state.loop_turns = 20
        r2 = check_auto_spawn_needed(state, "deep_review", [])
        assert r2 is None

    def test_fallback_message_is_generic_no_specific_lenses(self):
        """Fallback 消息不包含具体视角建议，只是通用提醒。"""
        empty_hints = MagicMock()
        empty_hints.is_empty.return_value = True
        empty_hints.focus_dimensions = []
        empty_hints.typical_weaknesses = []
        empty_hints.verification_strategies = []

        state = make_state(
            loop_turns=16,
            max_loop_turns=50,
            cognitive_hints=empty_hints,
        )
        state._role_spawn_nudge_fired = True
        state._fallback_spawn_nudge_fired = False

        result = check_auto_spawn_needed(state, "deep_review", [])
        assert result is not None
        # 不包含具体 lens 建议
        assert "lens=" not in result
        # 包含通用提醒
        assert "spawn_perspective" in result or "spawn_parallel_readers" in result
        assert "认知盲区" in result

    def test_fallback_includes_unread_sections(self):
        """Fallback 消息包含未读 sections 信息。"""
        empty_hints = MagicMock()
        empty_hints.is_empty.return_value = True
        empty_hints.focus_dimensions = []
        empty_hints.typical_weaknesses = []
        empty_hints.verification_strategies = []

        state = make_state(
            loop_turns=16,
            max_loop_turns=50,
            cognitive_hints=empty_hints,
        )
        state._role_spawn_nudge_fired = True
        state._fallback_spawn_nudge_fired = False
        state.sections_read = ["introduction"]  # 只读了一个

        result = check_auto_spawn_needed(state, "deep_review", [])
        assert result is not None
        assert "尚未阅读" in result


# ============================================================
# 4. _build_role_based_spawn_plan 单元测试
# ============================================================

class TestBuildRoleBasedPlan:
    """_build_role_based_spawn_plan 纯 CognitiveHints 驱动，无关键词启发式。"""

    def test_generates_from_focus_dimensions(self):
        state = make_state()
        suggestions = _build_role_based_spawn_plan(state)
        assert len(suggestions) >= 2
        # 应该包含 reviewer 在 lens 名中
        assert any("reviewer" in s for s in suggestions)

    def test_generates_from_typical_weaknesses(self):
        state = make_state()
        suggestions = _build_role_based_spawn_plan(state)
        # typical_weaknesses 应生成 hunter 视角
        assert any("hunter" in s for s in suggestions)

    def test_no_verification_strategies_in_phase1(self):
        """Phase 1 不再使用 verification_strategies（M2 修复：避免 Phase 1/2 重复消费）。"""
        state = make_state()
        suggestions = _build_role_based_spawn_plan(state)
        # verification_strategies 只在 Phase 2 使用，Phase 1 不应生成 executor 视角
        assert not any("executor" in s for s in suggestions)
        # 但应该有 reviewer（from focus_dimensions）和 hunter（from typical_weaknesses）
        assert any("reviewer" in s for s in suggestions)
        assert any("hunter" in s for s in suggestions)

    def test_empty_hints_returns_empty(self):
        """CognitiveHints 为空时，不做任何建议（不使用关键词启发式）。"""
        empty_hints = MagicMock()
        empty_hints.is_empty.return_value = True
        empty_hints.focus_dimensions = []
        empty_hints.typical_weaknesses = []
        empty_hints.verification_strategies = []

        state = make_state(cognitive_hints=empty_hints)
        suggestions = _build_role_based_spawn_plan(state)
        # 无 CognitiveHints → 无建议（不再有关键词启发式）
        assert len(suggestions) == 0

    def test_max_eight_suggestions(self):
        state = make_state()
        suggestions = _build_role_based_spawn_plan(state)
        assert len(suggestions) <= 8

    def test_dedup_by_lens_key_with_prefix(self):
        """相同来源类型前缀 + 前 40 字符相同时去重（M1 修复后的 lens_key 格式）。"""
        hints = MagicMock()
        hints.is_empty.return_value = False
        # 构造两个 focus_dimensions，前 40 字符完全相同
        common_prefix = "data_consistency_check_for_all_tables_in"  # 恰好 40 字符
        hints.focus_dimensions = [
            common_prefix + "_main_body_of_the_paper",
            common_prefix + "_appendix_section_results",  # 前 40 字符相同
        ]
        hints.typical_weaknesses = []
        hints.verification_strategies = []
        state = make_state(cognitive_hints=hints)
        suggestions = _build_role_based_spawn_plan(state)
        # lens_key = "dim:" + dim[:40].lower() → 两者相同 → 去重为 1
        assert len(suggestions) == 1

    def test_no_dedup_when_prefix_differs(self):
        """前 40 字符不同时不去重。"""
        hints = MagicMock()
        hints.is_empty.return_value = False
        hints.focus_dimensions = [
            "data_consistency_check_for_tables_and_figures_in_main_body",
            "statistical_inference_robustness_across_specifications_test",
        ]
        hints.typical_weaknesses = []
        hints.verification_strategies = []
        state = make_state(cognitive_hints=hints)
        suggestions = _build_role_based_spawn_plan(state)
        assert len(suggestions) == 2

    def test_short_dimensions_skipped(self):
        """长度 < 10 的 dimension 被跳过。"""
        hints = MagicMock()
        hints.is_empty.return_value = False
        hints.focus_dimensions = ["short", "数据一致性检查（data consistency check）"]
        hints.typical_weaknesses = []
        hints.verification_strategies = []
        state = make_state(cognitive_hints=hints)
        suggestions = _build_role_based_spawn_plan(state)
        assert len(suggestions) == 1  # "short" 被跳过


# ============================================================
# 5. _build_verify_spawn_plan 单元测试
# ============================================================

class TestBuildVerifySpawnPlan:
    """_build_verify_spawn_plan 基于 findings + tool_call_history 生成验证建议。"""

    def _make_state_no_strategies(self, findings):
        """创建不含 verification_strategies 的 state。"""
        hints = MagicMock()
        hints.is_empty.return_value = False
        hints.focus_dimensions = []
        hints.typical_weaknesses = []
        hints.verification_strategies = []
        return make_state(findings=findings, cognitive_hints=hints)

    def test_generates_from_needs_verification_findings(self):
        findings = [
            {"finding": "表1中的DID系数与表3的0.031不一致，需要确认", "status": "needs_verification", "priority": "high", "section": "results"},
            {"finding": "标准误聚类层级在不同表中的选择不一致", "status": "needs_verification", "priority": "medium", "section": "methodology"},
        ]
        state = self._make_state_no_strategies(findings)
        suggestions = _build_verify_spawn_plan(state, [])
        assert len(suggestions) == 2
        assert all("verifier" in s for s in suggestions)

    def test_skips_low_priority(self):
        findings = [
            {"finding": "minor formatting inconsistency", "status": "needs_verification", "priority": "low", "section": "appendix"},
            {"finding": "重要的数据不一致，需要逐行核实", "status": "needs_verification", "priority": "high", "section": "results"},
        ]
        state = self._make_state_no_strategies(findings)
        suggestions = _build_verify_spawn_plan(state, [])
        assert len(suggestions) == 1  # low priority skipped

    def test_skips_already_verified(self):
        findings = [
            {"finding": "已确认的问题，该数据不一致已经得到解释", "status": "verified", "priority": "high", "section": "results"},
            {"finding": "未确认的重要嫌疑点，需要逐行验证确认", "status": "needs_verification", "priority": "high", "section": "methodology"},
        ]
        state = self._make_state_no_strategies(findings)
        suggestions = _build_verify_spawn_plan(state, [])
        assert len(suggestions) == 1

    def test_supplements_from_verification_strategies(self):
        findings = []
        hints = MagicMock()
        hints.is_empty.return_value = False
        hints.focus_dimensions = []
        hints.typical_weaknesses = []
        hints.verification_strategies = [
            "逐表对比baseline和robustness的系数变化幅度是否超过合理范围",
            "交叉验证各表的标准误聚类层级是否一致",
        ]
        state = make_state(findings=findings, cognitive_hints=hints)
        suggestions = _build_verify_spawn_plan(state, [])
        # verification_strategies → strategy_verifier 建议
        assert len(suggestions) >= 1
        assert any("strategy_verifier" in s for s in suggestions)

    def test_max_eight_suggestions(self):
        findings = [
            {"finding": f"suspicion number {i} needs verification now", "status": "needs_verification", "priority": "high", "section": f"sec_{i}"}
            for i in range(10)
        ]
        state = make_state(findings=findings)
        suggestions = _build_verify_spawn_plan(state, [])
        assert len(suggestions) <= 8

    def test_dedup_same_section_same_finding_prefix(self):
        """相同 section + 相同 finding 前 30 字符应去重。"""
        # 确保前 30 字符完全相同（用 ASCII 避免中文字符计数差异）
        prefix = "The DID coefficient in Table 1"
        findings = [
            {"finding": prefix + " differs from Table 3 (first observation)", "status": "needs_verification", "priority": "high", "section": "results"},
            {"finding": prefix + " differs from Table 3 (second observation)", "status": "needs_verification", "priority": "high", "section": "results"},
        ]
        state = self._make_state_no_strategies(findings)
        suggestions = _build_verify_spawn_plan(state, [])
        # 前 30 字符相同 + 同 section → dedup_key 相同 → 去重
        assert len(suggestions) == 1


# ============================================================
# 6. 两阶段协同
# ============================================================

class TestTwoPhaseCoordination:
    """验证两阶段按正确顺序触发。"""

    def test_full_lifecycle(self):
        """模拟完整生命周期: phase1 → spawn → findings 产出 → phase2。"""
        state = make_state(loop_turns=0, max_loop_turns=50)
        tool_history = []

        # 进度 10%: 无触发
        state.loop_turns = 5
        assert check_auto_spawn_needed(state, "deep_review", tool_history) is None

        # 进度 16%: Phase 1 触发
        state.loop_turns = 8
        r1 = check_auto_spawn_needed(state, "deep_review", tool_history)
        assert r1 is not None
        assert "多视角" in r1

        # Agent 执行了 spawn
        tool_history.append({"name": "spawn_parallel_readers"})

        # 进度 30%: 已 spawn，Phase 1 不再触发
        state.loop_turns = 15
        assert check_auto_spawn_needed(state, "deep_review", tool_history) is None

        # spawn 的子视角产出了 findings
        state.findings = [
            {"finding": "DID 系数在 Table 1 和 Table 3 有微小差异", "status": "needs_verification", "priority": "high", "section": "results"},
            {"finding": "pre-trend test 的 p-value 在 footnote 中与正文不同", "status": "needs_verification", "priority": "medium", "section": "methodology"},
            {"finding": "样本量描述与实际 N 不匹配", "status": "needs_verification", "priority": "high", "section": "results"},
        ]

        # 进度 46%: Phase 2 触发
        state.loop_turns = 23
        r2 = check_auto_spawn_needed(state, "deep_review", tool_history)
        assert r2 is not None
        assert "逐行验证" in r2

        # 再次调用不触发
        state.loop_turns = 30
        assert check_auto_spawn_needed(state, "deep_review", tool_history) is None


# ============================================================
# 7. 增强测试：精确阈值边界、Phase 2 flag 边界、格式验证
# ============================================================

class TestPreciseThresholds:
    """精确验证各阈值边界条件。"""

    def test_phase1_exact_boundary_below(self):
        """进度恰好 14.9% 时不触发 Phase 1。"""
        # 7/50 = 14% < 15%
        state = make_state(loop_turns=7, max_loop_turns=50)
        result = check_auto_spawn_needed(state, "deep_review", [])
        assert result is None
        assert state._role_spawn_nudge_fired is False

    def test_phase1_exact_boundary_at(self):
        """进度恰好 15% 时触发 Phase 1。"""
        # 15/100 = 15% == 15%
        state = make_state(loop_turns=15, max_loop_turns=100)
        result = check_auto_spawn_needed(state, "deep_review", [])
        assert result is not None
        assert state._role_spawn_nudge_fired is True

    def test_phase2_exact_boundary_below(self):
        """进度恰好 44% 时不触发 Phase 2。"""
        findings = [
            {"finding": "f" * 20, "status": "needs_verification", "priority": "high", "section": "a"},
            {"finding": "g" * 20, "status": "needs_verification", "priority": "medium", "section": "b"},
        ]
        # 22/50 = 44% < 45%
        state = make_state(loop_turns=22, max_loop_turns=50, findings=findings)
        state._role_spawn_nudge_fired = True
        tool_history = [{"name": "spawn_parallel_readers"}]
        result = check_auto_spawn_needed(state, "deep_review", tool_history)
        assert result is None

    def test_phase2_exact_boundary_at(self):
        """进度恰好 45% 时触发 Phase 2。"""
        findings = [
            {"finding": "f" * 20, "status": "needs_verification", "priority": "high", "section": "a"},
            {"finding": "g" * 20, "status": "needs_verification", "priority": "medium", "section": "b"},
        ]
        # 45/100 = 45% == 45%
        state = make_state(loop_turns=45, max_loop_turns=100, findings=findings)
        state._role_spawn_nudge_fired = True
        tool_history = [{"name": "spawn_parallel_readers"}]
        result = check_auto_spawn_needed(state, "deep_review", tool_history)
        assert result is not None
        assert "逐行验证" in result

    def test_fallback_exact_boundary_below(self):
        """进度恰好 29% 时不触发 Fallback。"""
        empty_hints = MagicMock()
        empty_hints.is_empty.return_value = True
        empty_hints.focus_dimensions = []
        empty_hints.typical_weaknesses = []
        empty_hints.verification_strategies = []

        # 14/50 = 28% < 30%
        state = make_state(loop_turns=14, max_loop_turns=50, cognitive_hints=empty_hints)
        state._role_spawn_nudge_fired = True
        state._fallback_spawn_nudge_fired = False
        result = check_auto_spawn_needed(state, "deep_review", [])
        assert result is None

    def test_fallback_exact_boundary_at(self):
        """进度恰好 30% 时触发 Fallback。"""
        empty_hints = MagicMock()
        empty_hints.is_empty.return_value = True
        empty_hints.focus_dimensions = []
        empty_hints.typical_weaknesses = []
        empty_hints.verification_strategies = []

        # 30/100 = 30% == 30%
        state = make_state(loop_turns=30, max_loop_turns=100, cognitive_hints=empty_hints)
        state._role_spawn_nudge_fired = True
        state._fallback_spawn_nudge_fired = False
        result = check_auto_spawn_needed(state, "deep_review", [])
        assert result is not None
        assert "Spawn 时机提示" in result


class TestPhase2FlagBoundary:
    """Phase 2 flag 标记的边界情况（H2 修复验证）。"""

    def test_phase2_marks_fired_even_without_suggestions(self):
        """Phase 2 条件满足但 findings 不产生 suggestions 时仍标记 fired。"""
        # 构造 findings 满足 unverified >= 2 但 finding_text < 15 字符
        findings = [
            {"finding": "short text", "status": "needs_verification", "priority": "high", "section": "a"},
            {"finding": "also short!", "status": "needs_verification", "priority": "high", "section": "b"},
        ]
        state = make_state(loop_turns=25, max_loop_turns=50, findings=findings)
        state._role_spawn_nudge_fired = True
        tool_history = [{"name": "spawn_parallel_readers"}]

        result = check_auto_spawn_needed(state, "deep_review", tool_history)
        # findings 文本 < 15 字符 → _build_verify_spawn_plan 返回空
        # 但 _verify_spawn_nudge_fired 应该已标记为 True
        assert state._verify_spawn_nudge_fired is True

    def test_phase2_not_mark_fired_if_unverified_below_threshold(self):
        """unverified < 2 时 Phase 2 不标记 fired（条件不满足）。"""
        findings = [
            {"finding": "only one unverified finding here", "status": "needs_verification", "priority": "high", "section": "a"},
        ]
        state = make_state(loop_turns=25, max_loop_turns=50, findings=findings)
        state._role_spawn_nudge_fired = True
        tool_history = [{"name": "spawn_parallel_readers"}]

        check_auto_spawn_needed(state, "deep_review", tool_history)
        # unverified < 2 → 不进入 Phase 2 内部 → 不标记
        assert state._verify_spawn_nudge_fired is False


class TestSpawnSuggestionFormat:
    """验证 spawn 建议的格式正确性。"""

    def test_phase1_suggestion_has_lens_focus_question(self):
        """Phase 1 建议包含 lens=, focus=, question= 三个字段。"""
        state = make_state(loop_turns=8, max_loop_turns=50)
        result = check_auto_spawn_needed(state, "deep_review", [])
        assert result is not None
        # 检查建议格式
        assert 'lens="' in result
        assert 'focus="' in result
        assert 'question="' in result

    def test_phase2_suggestion_has_verifier_lens(self):
        """Phase 2 建议使用 verifier lens。"""
        findings = [
            {"finding": "表1中的DID系数0.032与表3的0.031不一致", "status": "needs_verification", "priority": "high", "section": "results"},
            {"finding": "标准误聚类层级在表1和表2中不同", "status": "needs_verification", "priority": "medium", "section": "methodology"},
        ]
        state = make_state(loop_turns=23, max_loop_turns=50, findings=findings)
        state._role_spawn_nudge_fired = True
        tool_history = [{"name": "spawn_parallel_readers"}]

        result = check_auto_spawn_needed(state, "deep_review", tool_history)
        assert result is not None
        assert 'lens="verifier"' in result

    def test_spawn_perspective_recognized(self):
        """spawn_perspective 也被识别为有效的 spawn 工具。"""
        state = make_state(loop_turns=8, max_loop_turns=50)
        tool_history = [{"name": "spawn_perspective"}]  # 用 spawn_perspective 而非 spawn_parallel_readers
        result = check_auto_spawn_needed(state, "deep_review", tool_history)
        # spawn_count >= 1 → Phase 1 不触发
        assert result is None


class TestFullLifecycleWithFallback:
    """完整生命周期测试：包含 Fallback 路径。"""

    def test_lifecycle_with_empty_hints_triggers_fallback(self):
        """CognitiveHints 为空时的完整流程: Phase 1 标记 → Fallback 触发。"""
        empty_hints = MagicMock()
        empty_hints.is_empty.return_value = True
        empty_hints.focus_dimensions = []
        empty_hints.typical_weaknesses = []
        empty_hints.verification_strategies = []

        state = make_state(
            loop_turns=0,
            max_loop_turns=50,
            cognitive_hints=empty_hints,
        )
        tool_history = []

        # 进度 10%: 无触发
        state.loop_turns = 5
        assert check_auto_spawn_needed(state, "deep_review", tool_history) is None

        # 进度 16%: Phase 1 触发但 hints 为空 → 标记 fired，无消息
        state.loop_turns = 8
        r1 = check_auto_spawn_needed(state, "deep_review", tool_history)
        assert r1 is None  # hints 为空，无具体建议
        assert state._role_spawn_nudge_fired is True

        # 进度 32%: Fallback 触发
        state.loop_turns = 16
        r2 = check_auto_spawn_needed(state, "deep_review", tool_history)
        assert r2 is not None
        assert "Spawn 时机提示" in r2
        assert state._fallback_spawn_nudge_fired is True

        # 再次调用不触发
        state.loop_turns = 20
        assert check_auto_spawn_needed(state, "deep_review", tool_history) is None


# ============================================================
# Spawn Gate in Completion Check (MCL Phase 1)
# NOTE: spawn_gate in completion_gate 不在当前 REPAIR_PLAN 范围内
# 参见 REPAIR_PLAN.md "不纳入本次修复" 第 3 条
# ============================================================

from core.boundary_guard import check_completion_gate
from core.state import WorkspaceState
from core.gate_config import CompletionGateConfig
from core.finding_quality import FindingQualityGate


class TestSpawnGate:
    """测试 mark_complete 的 spawn 门控。"""

    @pytest.fixture
    def gate_deps(self):
        gate_config = CompletionGateConfig()
        finding_quality_gate = FindingQualityGate()
        return gate_config, finding_quality_gate

    def _make_state_with_findings(self, n_findings=5, spawn_in_history=False):
        """创建一个有 N 条 findings、4+ sections、但未 spawn 的 state。"""
        state = WorkspaceState()
        state.paper_sections = {
            "introduction": "text...",
            "methodology": "text...",
            "results": "text...",
            "conclusion": "text...",
            "appendix": "text...",
        }
        state.sections_read = ["introduction", "methodology", "results"]
        state.findings = [
            {
                "finding": f"Finding #{i}: The coefficient in Table {i+1} shows inconsistency",
                "priority": "medium",
                "status": "verified",
                "evidence": f"Table {i+1}, Column (2), Row 3 reports 0.03{i} while text states 0.04{i}",
                "section": "results",
            }
            for i in range(n_findings)
        ]
        state.tool_call_history = []
        if spawn_in_history:
            state.tool_call_history.append({"name": "spawn_parallel_readers", "arguments": {}})
        state.edits = []
        state.deai_check_count = 0
        return state

    def test_spawn_gate_fires_when_never_spawned(self, gate_deps):
        """从未 spawn 过时，spawn gate 应触发。"""
        gate_config, fqg = gate_deps
        state = self._make_state_with_findings(5, spawn_in_history=False)
        nudges_fired: set[str] = set()

        msg, nudges_fired = check_completion_gate(
            state=state,
            gate_config=gate_config,
            hypothesis_module=None,
            finding_quality_gate=fqg,
            completion_nudges_fired=nudges_fired,
        )
        assert msg is not None
        assert "spawn_gate" in nudges_fired
        assert "spawn_parallel_readers" in msg
        assert "交叉审视" in msg

    def test_spawn_gate_not_fired_when_already_spawned(self, gate_deps):
        """已经 spawn 过时，spawn gate 不应触发。"""
        gate_config, fqg = gate_deps
        state = self._make_state_with_findings(5, spawn_in_history=True)
        nudges_fired: set[str] = set()

        msg, nudges_fired = check_completion_gate(
            state=state,
            gate_config=gate_config,
            hypothesis_module=None,
            finding_quality_gate=fqg,
            completion_nudges_fired=nudges_fired,
        )
        assert "spawn_gate" not in nudges_fired

    def test_spawn_gate_not_fired_when_too_few_findings(self, gate_deps):
        """findings 不足 3 条时不触发。"""
        gate_config, fqg = gate_deps
        state = self._make_state_with_findings(2, spawn_in_history=False)
        nudges_fired: set[str] = set()

        msg, nudges_fired = check_completion_gate(
            state=state,
            gate_config=gate_config,
            hypothesis_module=None,
            finding_quality_gate=fqg,
            completion_nudges_fired=nudges_fired,
        )
        assert "spawn_gate" not in nudges_fired

    def test_spawn_gate_fires_only_once(self, gate_deps):
        """spawn gate 只触发一次，第二次 mark_complete 放行。"""
        gate_config, fqg = gate_deps
        state = self._make_state_with_findings(5, spawn_in_history=False)
        nudges_fired: set[str] = set()

        # 第一次: 触发 spawn gate
        msg1, nudges_fired = check_completion_gate(
            state=state,
            gate_config=gate_config,
            hypothesis_module=None,
            finding_quality_gate=fqg,
            completion_nudges_fired=nudges_fired,
        )
        assert msg1 is not None
        assert "spawn_gate" in nudges_fired

        # 第二次: spawn gate 不再触发
        msg2, nudges_fired = check_completion_gate(
            state=state,
            gate_config=gate_config,
            hypothesis_module=None,
            finding_quality_gate=fqg,
            completion_nudges_fired=nudges_fired,
        )
        # msg2 可能触发其他 nudge，但不是 spawn_gate
        if msg2 is not None:
            assert "交叉审视" not in msg2

    def test_spawn_gate_includes_unread_sections(self, gate_deps):
        """spawn gate 应包含未读 sections 信息。"""
        gate_config, fqg = gate_deps
        state = self._make_state_with_findings(5, spawn_in_history=False)
        # 只读了 introduction，留下多个未读
        state.sections_read = ["introduction"]
        nudges_fired: set[str] = set()

        msg, _ = check_completion_gate(
            state=state,
            gate_config=gate_config,
            hypothesis_module=None,
            finding_quality_gate=fqg,
            completion_nudges_fired=nudges_fired,
        )
        assert msg is not None
        assert "尚未阅读" in msg
