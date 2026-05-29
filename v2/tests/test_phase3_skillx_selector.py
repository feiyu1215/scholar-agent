"""
Phase 3 SkillX 动态选择器测试。

覆盖：
  - 基本选择流程
  - Phase 过滤
  - Level 过滤
  - Score 阈值过滤
  - Token 预算约束
  - 前置依赖检查
  - 强制启用/禁用
  - 选择可解释性（reasons）
"""

import pytest
from core.skills.base import (
    Skill,
    SkillContext,
    SkillDescriptor,
    SkillLevel,
    SkillResult,
)
from core.skills.selector import SkillSelector, SelectionResult


# ==============================================================
# Test Skills
# ==============================================================

class HighScoreSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="high_score",
        level=SkillLevel.FUNCTIONAL,
        description="Always high score",
        applicable_phases=("deep_review",),
        token_cost_estimate=200,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, context):
        return 0.9

    def execute(self, context):
        return SkillResult(success=True)


class MediumScoreSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="medium_score",
        level=SkillLevel.FUNCTIONAL,
        description="Medium score",
        applicable_phases=("deep_review", "synthesis"),
        token_cost_estimate=300,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, context):
        return 0.5

    def execute(self, context):
        return SkillResult(success=True)


class LowScoreSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="low_score",
        level=SkillLevel.ATOMIC,
        description="Low score",
        applicable_phases=("editing",),
        token_cost_estimate=100,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, context):
        return 0.1

    def execute(self, context):
        return SkillResult(success=True)


class ExpensiveSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="expensive",
        level=SkillLevel.FUNCTIONAL,
        description="Very expensive",
        applicable_phases=("deep_review",),
        token_cost_estimate=5000,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, context):
        return 0.8

    def execute(self, context):
        return SkillResult(success=True)


class DependentSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="dependent",
        level=SkillLevel.FUNCTIONAL,
        description="Depends on high_score",
        prerequisites=("high_score",),
        applicable_phases=("deep_review",),
        token_cost_estimate=200,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, context):
        return 0.7

    def execute(self, context):
        return SkillResult(success=True)


class PlanningSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="planner",
        level=SkillLevel.PLANNING,
        description="Planning skill",
        applicable_phases=("orientation",),
        token_cost_estimate=150,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, context):
        return 0.8

    def execute(self, context):
        return SkillResult(success=True)


# ==============================================================
# Fixtures
# ==============================================================

@pytest.fixture
def all_skills():
    return [
        HighScoreSkill(),
        MediumScoreSkill(),
        LowScoreSkill(),
        ExpensiveSkill(),
        DependentSkill(),
        PlanningSkill(),
    ]


@pytest.fixture
def selector(all_skills):
    return SkillSelector(all_skills, score_threshold=0.3)


@pytest.fixture
def deep_review_context():
    return SkillContext(
        paper_text="regression analysis with DID methodology",
        current_phase="deep_review",
    )


# ==============================================================
# Tests
# ==============================================================

class TestSkillSelectorBasic:
    def test_select_returns_result(self, selector, deep_review_context):
        result = selector.select(deep_review_context)
        assert isinstance(result, SelectionResult)
        assert isinstance(result.selected_skills, list)
        assert isinstance(result.reasons, list)

    def test_high_score_selected(self, selector, deep_review_context):
        result = selector.select(deep_review_context, token_budget=10000)
        selected_names = [s.descriptor.name for s in result.selected_skills]
        assert "high_score" in selected_names

    def test_low_score_filtered(self, selector, deep_review_context):
        """Low score (0.1) below threshold (0.3) should be excluded."""
        result = selector.select(deep_review_context, token_budget=10000)
        selected_names = [s.descriptor.name for s in result.selected_skills]
        assert "low_score" not in selected_names


class TestPhaseFiltering:
    def test_phase_mismatch_excluded(self, selector, deep_review_context):
        """Skills with non-matching phases should be excluded."""
        result = selector.select(deep_review_context, token_budget=10000)
        selected_names = [s.descriptor.name for s in result.selected_skills]
        # low_score only applies to "editing" phase
        assert "low_score" not in selected_names
        # planner only applies to "orientation" phase
        assert "planner" not in selected_names

    def test_orientation_phase(self, selector):
        ctx = SkillContext(
            paper_text="some paper text",
            current_phase="orientation",
        )
        result = selector.select(ctx, token_budget=10000)
        selected_names = [s.descriptor.name for s in result.selected_skills]
        assert "planner" in selected_names

    def test_no_phase_filter(self, all_skills):
        """Skills with empty applicable_phases should pass any phase."""
        class NoPhaseSkill(Skill):
            _DESCRIPTOR = SkillDescriptor(
                name="no_phase",
                level=SkillLevel.ATOMIC,
                description="No phase restriction",
                applicable_phases=(),  # empty = all phases
                token_cost_estimate=50,
            )

            @property
            def descriptor(self):
                return self._DESCRIPTOR

            def can_apply(self, context):
                return 0.6

            def execute(self, context):
                return SkillResult(success=True)

        selector = SkillSelector([NoPhaseSkill()], score_threshold=0.3)
        ctx = SkillContext(current_phase="deep_review")
        result = selector.select(ctx)
        assert len(result.selected_skills) == 1


class TestLevelFiltering:
    def test_filter_functional_only(self, selector, deep_review_context):
        result = selector.select(
            deep_review_context,
            token_budget=10000,
            level_filter=SkillLevel.FUNCTIONAL,
        )
        for skill in result.selected_skills:
            assert skill.descriptor.level == SkillLevel.FUNCTIONAL

    def test_filter_planning_only(self, selector):
        ctx = SkillContext(current_phase="orientation")
        result = selector.select(ctx, level_filter=SkillLevel.PLANNING)
        for skill in result.selected_skills:
            assert skill.descriptor.level == SkillLevel.PLANNING


class TestTokenBudget:
    def test_budget_constraint(self, selector, deep_review_context):
        """Expensive skill (5000 tokens) should be excluded with small budget."""
        result = selector.select(deep_review_context, token_budget=1000)
        selected_names = [s.descriptor.name for s in result.selected_skills]
        assert "expensive" not in selected_names

    def test_budget_tracking(self, selector, deep_review_context):
        result = selector.select(deep_review_context, token_budget=1000)
        assert result.total_token_cost <= 1000
        assert result.budget_remaining >= 0
        assert result.total_token_cost + result.budget_remaining == 1000

    def test_greedy_fill_by_score(self, selector, deep_review_context):
        """Higher scoring skills should be selected first."""
        result = selector.select(deep_review_context, token_budget=500)
        if len(result.selected_skills) >= 2:
            # First selected should have higher can_apply score
            scores = [s.can_apply(deep_review_context) for s in result.selected_skills]
            assert scores == sorted(scores, reverse=True)


class TestPrerequisites:
    def test_dependency_satisfied(self, selector, deep_review_context):
        """DependentSkill requires high_score, which should be selected first."""
        result = selector.select(deep_review_context, token_budget=10000)
        selected_names = [s.descriptor.name for s in result.selected_skills]
        if "dependent" in selected_names:
            # high_score must appear before dependent
            assert selected_names.index("high_score") < selected_names.index("dependent")

    def test_dependency_unsatisfied(self):
        """If prerequisite not selected, dependent should be excluded."""
        dependent = DependentSkill()
        selector = SkillSelector([dependent], score_threshold=0.3)
        ctx = SkillContext(current_phase="deep_review")
        result = selector.select(ctx)
        selected_names = [s.descriptor.name for s in result.selected_skills]
        assert "dependent" not in selected_names


class TestForceOverrides:
    def test_force_enable(self, selector, deep_review_context):
        """Force-enabled skill should bypass can_apply check."""
        selector.force_enable("low_score")
        # low_score is in "editing" phase, not "deep_review" — but it's still phase-filtered
        # Let's use a context that matches
        ctx = SkillContext(current_phase="editing")
        result = selector.select(ctx, token_budget=10000)
        selected_names = [s.descriptor.name for s in result.selected_skills]
        assert "low_score" in selected_names

    def test_force_disable(self, selector, deep_review_context):
        """Force-disabled skill should be excluded regardless of score."""
        selector.force_disable("high_score")
        result = selector.select(deep_review_context, token_budget=10000)
        selected_names = [s.descriptor.name for s in result.selected_skills]
        assert "high_score" not in selected_names

    def test_clear_overrides(self, selector, deep_review_context):
        selector.force_disable("high_score")
        selector.clear_overrides()
        result = selector.select(deep_review_context, token_budget=10000)
        selected_names = [s.descriptor.name for s in result.selected_skills]
        assert "high_score" in selected_names


class TestSelectionReasons:
    def test_reasons_populated(self, selector, deep_review_context):
        result = selector.select(deep_review_context, token_budget=10000)
        assert len(result.reasons) > 0

    def test_phase_mismatch_reason(self, selector, deep_review_context):
        result = selector.select(deep_review_context, token_budget=10000)
        phase_mismatch_reasons = [
            r for r in result.reasons if "phase_mismatch" in r.reason
        ]
        assert len(phase_mismatch_reasons) > 0

    def test_selected_reason(self, selector, deep_review_context):
        result = selector.select(deep_review_context, token_budget=10000)
        selected_reasons = [r for r in result.reasons if r.selected]
        assert len(selected_reasons) > 0
        for r in selected_reasons:
            assert "selected" in r.reason


class TestRegistration:
    def test_register_new_skill(self, selector):
        class NewSkill(Skill):
            _DESCRIPTOR = SkillDescriptor(
                name="new_one", level=SkillLevel.ATOMIC, description="New",
                token_cost_estimate=50,
            )

            @property
            def descriptor(self):
                return self._DESCRIPTOR

            def can_apply(self, ctx):
                return 0.5

            def execute(self, ctx):
                return SkillResult(success=True)

        selector.register(NewSkill())
        names = [s.descriptor.name for s in selector.all_skills]
        assert "new_one" in names

    def test_register_duplicate_rejected(self, selector):
        """Duplicate name should be rejected."""
        initial_count = len(selector.all_skills)
        selector.register(HighScoreSkill())  # already registered
        assert len(selector.all_skills) == initial_count

    def test_unregister(self, selector):
        assert selector.unregister("high_score")
        names = [s.descriptor.name for s in selector.all_skills]
        assert "high_score" not in names

    def test_unregister_nonexistent(self, selector):
        assert not selector.unregister("nonexistent")
