"""
Phase 3 SkillX ToolGroup 管理器测试。

覆盖：
  - 组的创建与注册
  - 组的激活与去激活
  - Phase 感知自动切换
  - 基础组（always active）
  - 自动分配
"""

import pytest
from core.skills.base import (
    Skill,
    SkillContext,
    SkillDescriptor,
    SkillLevel,
    SkillResult,
)
from core.skills.tool_group import ToolGroupManager, ToolGroup


# ==============================================================
# Test Skills
# ==============================================================

class OrientationSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="orientation_skill",
        level=SkillLevel.PLANNING,
        description="For orientation",
        applicable_phases=("orientation",),
        token_cost_estimate=100,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, ctx):
        return 0.8

    def execute(self, ctx):
        return SkillResult(success=True)


class DeepReviewSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="deep_review_skill",
        level=SkillLevel.FUNCTIONAL,
        description="For deep review",
        applicable_phases=("deep_review",),
        token_cost_estimate=200,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, ctx):
        return 0.7

    def execute(self, ctx):
        return SkillResult(success=True)


class SynthesisSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="synthesis_skill",
        level=SkillLevel.FUNCTIONAL,
        description="For synthesis",
        applicable_phases=("synthesis",),
        token_cost_estimate=250,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, ctx):
        return 0.6

    def execute(self, ctx):
        return SkillResult(success=True)


class BasicUtilSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="basic_util",
        level=SkillLevel.ATOMIC,
        description="Always available",
        applicable_phases=(),  # all phases
        token_cost_estimate=50,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, ctx):
        return 0.9

    def execute(self, ctx):
        return SkillResult(success=True)


# ==============================================================
# Fixtures
# ==============================================================

@pytest.fixture
def manager():
    return ToolGroupManager()


@pytest.fixture
def skills():
    return {
        "orientation": OrientationSkill(),
        "deep_review": DeepReviewSkill(),
        "synthesis": SynthesisSkill(),
        "basic": BasicUtilSkill(),
    }


# ==============================================================
# Tests: Group Creation
# ==============================================================

class TestGroupCreation:
    def test_create_group(self, manager, skills):
        group = manager.create_group("review_group", skills=[skills["deep_review"]])
        assert isinstance(group, ToolGroup)
        assert "review_group" in manager.all_group_names

    def test_create_group_with_multiple_skills(self, manager, skills):
        group = manager.create_group(
            "combo_group",
            skills=[skills["deep_review"], skills["synthesis"]],
        )
        assert len(group.skills) == 2

    def test_create_group_overwrites(self, manager, skills):
        """Creating group with same name overwrites (dict assignment)."""
        manager.create_group("grp", skills=[skills["basic"]])
        manager.create_group("grp", skills=[skills["deep_review"]])
        # The group is simply replaced
        group = manager.get_group("grp")
        assert group is not None
        assert len(group.skills) == 1
        assert group.skills[0].descriptor.name == "deep_review_skill"

    def test_delete_group(self, manager, skills):
        manager.create_group("temp", skills=[skills["basic"]])
        result = manager.delete_group("temp")
        assert result is True
        assert "temp" not in manager.all_group_names

    def test_delete_nonexistent_group(self, manager):
        result = manager.delete_group("nonexistent")
        assert result is False

    def test_delete_basic_group_fails(self, manager, skills):
        """Cannot delete a basic (always-active) group."""
        manager.create_group("basic_grp", skills=[skills["basic"]], is_basic=True)
        result = manager.delete_group("basic_grp")
        assert result is False
        assert "basic_grp" in manager.all_group_names


# ==============================================================
# Tests: Activation / Deactivation
# ==============================================================

class TestActivation:
    def test_activate_group(self, manager, skills):
        manager.create_group("grp", skills=[skills["deep_review"]])
        result = manager.activate_group("grp")
        assert result is True
        assert "grp" in manager.active_group_names

    def test_deactivate_group(self, manager, skills):
        manager.create_group("grp", skills=[skills["deep_review"]])
        manager.activate_group("grp")
        result = manager.deactivate_group("grp")
        assert result is True
        assert "grp" not in manager.active_group_names

    def test_activate_nonexistent_returns_false(self, manager):
        result = manager.activate_group("nonexistent")
        assert result is False

    def test_deactivate_nonexistent_returns_false(self, manager):
        result = manager.deactivate_group("nonexistent")
        assert result is False

    def test_active_skills(self, manager, skills):
        """Active skills should include all skills from active groups."""
        manager.create_group("grp", skills=[skills["deep_review"], skills["synthesis"]])
        manager.activate_group("grp")
        active = manager.get_active_skills()
        names = [s.descriptor.name for s in active]
        assert "deep_review_skill" in names
        assert "synthesis_skill" in names

    def test_active_skills_deduplication(self, manager, skills):
        """Same skill in multiple active groups should appear only once."""
        manager.create_group("grp1", skills=[skills["deep_review"]])
        manager.create_group("grp2", skills=[skills["deep_review"]])
        manager.activate_group("grp1")
        manager.activate_group("grp2")
        active = manager.get_active_skills()
        names = [s.descriptor.name for s in active]
        assert names.count("deep_review_skill") == 1


# ==============================================================
# Tests: Basic Group (Always Active)
# ==============================================================

class TestBasicGroup:
    def test_basic_group_auto_activated(self, manager, skills):
        manager.create_group("basic_grp", skills=[skills["basic"]], is_basic=True)
        # Even without explicit activation, basic is active
        assert "basic_grp" in manager.active_group_names
        active = manager.get_active_skills()
        names = [s.descriptor.name for s in active]
        assert "basic_util" in names

    def test_basic_group_cannot_deactivate(self, manager, skills):
        manager.create_group("basic_grp", skills=[skills["basic"]], is_basic=True)
        result = manager.deactivate_group("basic_grp")
        assert result is False
        # Should still be active
        assert "basic_grp" in manager.active_group_names


# ==============================================================
# Tests: Phase-Aware Auto-Switching
# ==============================================================

class TestPhaseAutoSwitch:
    def test_activate_for_phase_with_custom_map(self, manager, skills):
        manager.create_group("orient_grp", skills=[skills["orientation"]])
        manager.create_group("review_grp", skills=[skills["deep_review"]])
        manager.create_group("basic_grp", skills=[skills["basic"]], is_basic=True)

        custom_map = {
            "orientation": ["orient_grp"],
            "deep_review": ["review_grp"],
        }

        activated = manager.activate_for_phase("orientation", phase_group_map=custom_map)
        active_names = [s.descriptor.name for s in manager.get_active_skills()]
        assert "orientation_skill" in active_names
        # basic still present
        assert "basic_util" in active_names
        # deep_review not active
        assert "deep_review_skill" not in active_names

    def test_phase_switch_deactivates_previous(self, manager, skills):
        manager.create_group("orient_grp", skills=[skills["orientation"]])
        manager.create_group("review_grp", skills=[skills["deep_review"]])
        manager.create_group("basic_grp", skills=[skills["basic"]], is_basic=True)

        custom_map = {
            "orientation": ["orient_grp"],
            "deep_review": ["review_grp"],
        }

        manager.activate_for_phase("orientation", phase_group_map=custom_map)
        manager.activate_for_phase("deep_review", phase_group_map=custom_map)

        active_names = [s.descriptor.name for s in manager.get_active_skills()]
        assert "deep_review_skill" in active_names
        assert "orientation_skill" not in active_names
        # basic still present
        assert "basic_util" in active_names

    def test_activate_for_phase_unknown_groups(self, manager, skills):
        """If phase maps to nonexistent groups, they are just skipped."""
        manager.create_group("basic_grp", skills=[skills["basic"]], is_basic=True)
        custom_map = {"deep_review": ["nonexistent_group"]}
        activated = manager.activate_for_phase("deep_review", phase_group_map=custom_map)
        # Only basic should be active
        active_names = [s.descriptor.name for s in manager.get_active_skills()]
        assert "basic_util" in active_names


# ==============================================================
# Tests: Auto-Assignment
# ==============================================================

class TestAutoAssign:
    def test_auto_assign_to_existing_phase_group(self, manager, skills):
        """auto_assign should add skill to group matching its phase."""
        manager.create_group("deep_review", skills=[])
        assigned = manager.auto_assign(skills["deep_review"])
        assert "deep_review" in assigned
        group = manager.get_group("deep_review")
        assert "deep_review_skill" in group.skill_names

    def test_auto_assign_no_phase_to_basic(self, manager, skills):
        """Skill with no applicable_phases should go to basic group."""
        manager.create_group("basic_grp", skills=[], is_basic=True)
        assigned = manager.auto_assign(skills["basic"])
        assert "basic_grp" in assigned
        group = manager.get_group("basic_grp")
        assert "basic_util" in group.skill_names

    def test_auto_assign_no_matching_group(self, manager, skills):
        """If no group matches, returns empty list."""
        # No groups created
        assigned = manager.auto_assign(skills["deep_review"])
        assert assigned == []


# ==============================================================
# Tests: ToolGroup dataclass
# ==============================================================

class TestToolGroupDataclass:
    def test_add_skill(self, skills):
        group = ToolGroup(name="test")
        group.add_skill(skills["deep_review"])
        assert "deep_review_skill" in group.skill_names

    def test_add_skill_dedup(self, skills):
        group = ToolGroup(name="test")
        group.add_skill(skills["deep_review"])
        group.add_skill(skills["deep_review"])
        assert len(group.skills) == 1

    def test_remove_skill(self, skills):
        group = ToolGroup(name="test", skills=[skills["deep_review"]])
        result = group.remove_skill("deep_review_skill")
        assert result is True
        assert len(group.skills) == 0

    def test_remove_skill_not_found(self, skills):
        group = ToolGroup(name="test", skills=[skills["deep_review"]])
        result = group.remove_skill("nonexistent")
        assert result is False
