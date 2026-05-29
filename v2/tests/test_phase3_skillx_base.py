"""
Phase 3 SkillX 基础类型与 Skill ABC 测试。

覆盖：
  - SkillLevel 枚举
  - SkillDescriptor 不可变性与字段
  - SkillContext 默认值与可变性
  - SkillResult 结构
  - Finding 数据类
  - Skill ABC 协议（can_apply / execute / validate_context / get_metadata_prompt）
"""

import pytest
from core.skills.base import (
    Finding,
    Skill,
    SkillContext,
    SkillDescriptor,
    SkillLevel,
    SkillResult,
)


# ==============================================================
# Fixtures
# ==============================================================

class DummySkill(Skill):
    """测试用最小 Skill 实现。"""

    _DESCRIPTOR = SkillDescriptor(
        name="dummy_skill",
        level=SkillLevel.ATOMIC,
        description="A dummy skill for testing",
        applicable_phases=("deep_review", "synthesis"),
        tags=("test",),
        token_cost_estimate=100,
    )

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._DESCRIPTOR

    def can_apply(self, context: SkillContext) -> float:
        if "trigger" in context.paper_text:
            return 0.9
        return 0.1

    def execute(self, context: SkillContext) -> SkillResult:
        return SkillResult(
            findings=[
                Finding(
                    category="test",
                    severity="minor",
                    description="Test finding",
                    skill_source="dummy_skill",
                )
            ],
            output_data={"processed": True},
            success=True,
        )


class FailingSkill(Skill):
    """执行必定失败的 Skill。"""

    _DESCRIPTOR = SkillDescriptor(
        name="failing_skill",
        level=SkillLevel.FUNCTIONAL,
        description="Always fails",
    )

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._DESCRIPTOR

    def can_apply(self, context: SkillContext) -> float:
        return 0.5

    def execute(self, context: SkillContext) -> SkillResult:
        raise RuntimeError("Intentional failure")


# ==============================================================
# SkillLevel Tests
# ==============================================================

class TestSkillLevel:
    def test_three_levels_exist(self):
        assert SkillLevel.PLANNING.value == "planning"
        assert SkillLevel.FUNCTIONAL.value == "functional"
        assert SkillLevel.ATOMIC.value == "atomic"

    def test_all_levels(self):
        assert len(SkillLevel) == 3


# ==============================================================
# SkillDescriptor Tests
# ==============================================================

class TestSkillDescriptor:
    def test_creation(self):
        desc = SkillDescriptor(
            name="test_skill",
            level=SkillLevel.FUNCTIONAL,
            description="Test description",
        )
        assert desc.name == "test_skill"
        assert desc.level == SkillLevel.FUNCTIONAL
        assert desc.description == "Test description"

    def test_frozen_immutable(self):
        desc = SkillDescriptor(
            name="test",
            level=SkillLevel.ATOMIC,
            description="x",
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            desc.name = "changed"  # type: ignore

    def test_defaults(self):
        desc = SkillDescriptor(
            name="minimal",
            level=SkillLevel.ATOMIC,
            description="minimal skill",
        )
        assert desc.prerequisites == ()
        assert desc.input_schema == {}
        assert desc.output_schema == {}
        assert desc.applicable_phases == ()
        assert desc.tags == ()
        assert desc.token_cost_estimate == 0
        assert desc.version == "1.0"

    def test_full_creation(self):
        desc = SkillDescriptor(
            name="full_skill",
            level=SkillLevel.PLANNING,
            description="Full featured skill",
            prerequisites=("dep_a", "dep_b"),
            input_schema={"text": "str"},
            output_schema={"findings": "list"},
            applicable_phases=("deep_review",),
            tags=("economics", "methodology"),
            token_cost_estimate=500,
            version="2.1",
        )
        assert desc.prerequisites == ("dep_a", "dep_b")
        assert desc.tags == ("economics", "methodology")
        assert desc.version == "2.1"


# ==============================================================
# SkillContext Tests
# ==============================================================

class TestSkillContext:
    def test_default_context(self):
        ctx = SkillContext()
        assert ctx.paper_text == ""
        assert ctx.paper_metadata == {}
        assert ctx.current_phase == ""
        assert ctx.existing_findings == []
        assert ctx.token_budget == 2000

    def test_mutable(self):
        ctx = SkillContext(paper_text="hello")
        ctx.paper_text = "world"
        assert ctx.paper_text == "world"

    def test_with_findings(self):
        finding = Finding(category="test", severity="minor", description="x")
        ctx = SkillContext(existing_findings=[finding])
        assert len(ctx.existing_findings) == 1
        assert ctx.existing_findings[0].category == "test"


# ==============================================================
# SkillResult Tests
# ==============================================================

class TestSkillResult:
    def test_success_result(self):
        result = SkillResult(
            findings=[Finding(category="a", severity="major", description="b")],
            success=True,
        )
        assert result.success
        assert len(result.findings) == 1

    def test_failure_result(self):
        result = SkillResult(success=False, error_message="Something broke")
        assert not result.success
        assert result.error_message == "Something broke"
        assert result.findings == []

    def test_metadata(self):
        result = SkillResult(metadata={"custom_key": "value"})
        assert result.metadata["custom_key"] == "value"


# ==============================================================
# Finding Tests
# ==============================================================

class TestFinding:
    def test_minimal_finding(self):
        f = Finding(category="statistics", severity="major", description="issue")
        assert f.category == "statistics"
        assert f.severity == "major"
        assert f.confidence == 0.8  # default
        assert f.skill_source == ""

    def test_full_finding(self):
        f = Finding(
            category="methodology",
            severity="critical",
            description="Missing parallel trend test",
            evidence="No event study figure or pre-trend test found",
            suggestion="Add event study analysis",
            location="Section 4.1",
            confidence=0.9,
            skill_source="methodology_analysis",
        )
        assert f.location == "Section 4.1"
        assert f.skill_source == "methodology_analysis"


# ==============================================================
# Skill ABC Tests
# ==============================================================

class TestSkillABC:
    def test_dummy_skill_descriptor(self):
        skill = DummySkill()
        assert skill.descriptor.name == "dummy_skill"
        assert skill.descriptor.level == SkillLevel.ATOMIC

    def test_can_apply_high_score(self):
        skill = DummySkill()
        ctx = SkillContext(paper_text="This text has a trigger word")
        assert skill.can_apply(ctx) == 0.9

    def test_can_apply_low_score(self):
        skill = DummySkill()
        ctx = SkillContext(paper_text="No special words here")
        assert skill.can_apply(ctx) == 0.1

    def test_execute_produces_findings(self):
        skill = DummySkill()
        ctx = SkillContext(paper_text="test")
        result = skill.execute(ctx)
        assert result.success
        assert len(result.findings) == 1
        assert result.findings[0].category == "test"
        assert result.output_data["processed"] is True

    def test_validate_context_default(self):
        skill = DummySkill()
        ctx = SkillContext()
        is_valid, msg = skill.validate_context(ctx)
        assert is_valid
        assert msg == ""

    def test_get_instruction_default(self):
        skill = DummySkill()
        # Default returns description
        assert skill.get_instruction() == "A dummy skill for testing"

    def test_get_metadata_prompt(self):
        skill = DummySkill()
        prompt = skill.get_metadata_prompt()
        assert "dummy_skill" in prompt
        assert "deep_review" in prompt or "synthesis" in prompt

    def test_repr(self):
        skill = DummySkill()
        r = repr(skill)
        assert "DummySkill" in r
        assert "dummy_skill" in r
        assert "atomic" in r
