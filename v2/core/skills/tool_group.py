"""
core/skills/tool_group.py — Phase 感知的 Skill 分组管理

按审稿阶段组织 Skill 集合，实现动态切换：
  - INITIAL_SCAN: 结构分析、快速浏览
  - DEEP_REVIEW: 方法论审查、统计检验、文献对比
  - EDITING: 语言润色、格式检查
  - SYNTHESIS: 综合评分、建议生成

设计原则：
  - Phase 转换时自动切换激活的 ToolGroup
  - 未激活的 Skill 对模型不可见，不占上下文
  - `basic` 组始终激活（基础工具如提取数值、格式检查）
  - 与 EventBus 集成：监听 PHASE_TRANSITION 自动切换
  - 用户可手动激活/停用组（支持探索性使用）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from core.skills.base import Skill, SkillLevel

logger = logging.getLogger(__name__)


# ==============================================================
# ToolGroup 定义
# ==============================================================

@dataclass
class ToolGroup:
    """一个 Skill 分组。

    Attributes:
        name: 组名称（唯一标识）
        description: 组描述
        skills: 属于该组的 Skill 列表
        is_basic: 是否为 basic 组（始终激活）
    """
    name: str
    description: str = ""
    skills: list[Skill] = field(default_factory=list)
    is_basic: bool = False

    def add_skill(self, skill: Skill) -> None:
        """添加 Skill 到组（去重）。"""
        existing_names = {s.descriptor.name for s in self.skills}
        if skill.descriptor.name not in existing_names:
            self.skills.append(skill)

    def remove_skill(self, skill_name: str) -> bool:
        """从组中移除 Skill。"""
        for i, s in enumerate(self.skills):
            if s.descriptor.name == skill_name:
                self.skills.pop(i)
                return True
        return False

    @property
    def skill_names(self) -> list[str]:
        """返回组内所有 Skill 名称。"""
        return [s.descriptor.name for s in self.skills]


# ==============================================================
# Phase -> ToolGroup 映射
# ==============================================================

# 默认审稿阶段分组策略
DEFAULT_PHASE_GROUPS: dict[str, list[str]] = {
    "initial_scan": ["structure_analysis", "quick_scan"],
    "orientation": ["structure_analysis", "quick_scan"],
    "deep_review": ["methodology_analysis", "statistical_validation",
                    "citation_verification", "logic_coherence",
                    "table_processing"],
    "editing": ["language_polish", "format_check"],
    "synthesis": ["synthesis_scoring", "recommendation_generation"],
}


# ==============================================================
# ToolGroup 管理器
# ==============================================================

class ToolGroupManager:
    """管理所有 ToolGroup，处理 Phase 转换时的动态切换。

    Usage:
        manager = ToolGroupManager()
        manager.create_group("deep_review", skills=[...])
        manager.create_group("basic", skills=[...], is_basic=True)

        # Phase 转换时切换
        manager.activate_for_phase("deep_review")

        # 获取当前激活的 Skills
        active_skills = manager.get_active_skills()
    """

    def __init__(self):
        self._groups: dict[str, ToolGroup] = {}
        self._active_groups: set[str] = set()  # 当前激活的组名

    # ----------------------------------------------------------
    # 组管理
    # ----------------------------------------------------------

    def create_group(
        self,
        name: str,
        skills: Optional[list[Skill]] = None,
        description: str = "",
        is_basic: bool = False,
    ) -> ToolGroup:
        """创建一个新的 ToolGroup。

        Args:
            name: 组名称
            skills: 初始 Skill 列表
            description: 组描述
            is_basic: 是否为 basic 组（始终激活）

        Returns:
            创建的 ToolGroup
        """
        group = ToolGroup(
            name=name,
            description=description,
            skills=list(skills) if skills else [],
            is_basic=is_basic,
        )
        self._groups[name] = group
        # basic 组自动激活
        if is_basic:
            self._active_groups.add(name)
        return group

    def get_group(self, name: str) -> Optional[ToolGroup]:
        """获取指定组。"""
        return self._groups.get(name)

    def delete_group(self, name: str) -> bool:
        """删除一个组（basic 组不可删除）。"""
        group = self._groups.get(name)
        if group is None:
            return False
        if group.is_basic:
            logger.warning("[ToolGroupManager] Cannot delete basic group: %s", name)
            return False
        del self._groups[name]
        self._active_groups.discard(name)
        return True

    @property
    def all_group_names(self) -> list[str]:
        """所有组名称。"""
        return list(self._groups.keys())

    # ----------------------------------------------------------
    # 激活/停用
    # ----------------------------------------------------------

    def activate_group(self, name: str) -> bool:
        """手动激活一个组。"""
        if name not in self._groups:
            return False
        self._active_groups.add(name)
        return True

    def deactivate_group(self, name: str) -> bool:
        """手动停用一个组（basic 组不可停用）。"""
        group = self._groups.get(name)
        if group is None:
            return False
        if group.is_basic:
            logger.warning("[ToolGroupManager] Cannot deactivate basic group: %s", name)
            return False
        self._active_groups.discard(name)
        return True

    def activate_for_phase(
        self,
        phase: str,
        phase_group_map: Optional[dict[str, list[str]]] = None,
    ) -> list[str]:
        """根据审稿 Phase 自动切换激活的组。

        1. 停用所有非 basic 组
        2. 查找 phase 对应的组名列表
        3. 激活这些组

        Args:
            phase: 当前审稿阶段名称
            phase_group_map: 自定义 Phase -> 组名映射（None 使用默认）

        Returns:
            激活的组名列表
        """
        mapping = phase_group_map or DEFAULT_PHASE_GROUPS

        # 1. 停用所有非 basic 组
        basic_names = {
            name for name, g in self._groups.items() if g.is_basic
        }
        self._active_groups = set(basic_names)

        # 2. 查找并激活 phase 对应的组
        phase_lower = phase.lower()
        target_groups = mapping.get(phase_lower, [])

        activated = list(basic_names)
        for group_name in target_groups:
            if group_name in self._groups:
                self._active_groups.add(group_name)
                activated.append(group_name)
            else:
                logger.debug(
                    "[ToolGroupManager] Phase '%s' references unknown group: %s",
                    phase, group_name,
                )

        logger.debug(
            "[ToolGroupManager] Phase '%s' -> active groups: %s",
            phase, activated,
        )
        return activated

    @property
    def active_group_names(self) -> list[str]:
        """当前激活的组名列表。"""
        return list(self._active_groups)

    # ----------------------------------------------------------
    # 获取激活的 Skills
    # ----------------------------------------------------------

    def get_active_skills(self) -> list[Skill]:
        """获取当前所有激活组中的 Skills（去重）。

        Returns:
            去重后的 Skill 列表
        """
        seen_names: set[str] = set()
        result: list[Skill] = []

        for group_name in self._active_groups:
            group = self._groups.get(group_name)
            if group is None:
                continue
            for skill in group.skills:
                name = skill.descriptor.name
                if name not in seen_names:
                    seen_names.add(name)
                    result.append(skill)

        return result

    def get_active_skill_names(self) -> list[str]:
        """获取当前激活 Skill 名称列表。"""
        return [s.descriptor.name for s in self.get_active_skills()]

    # ----------------------------------------------------------
    # Skill 自动分组
    # ----------------------------------------------------------

    def auto_assign(self, skill: Skill) -> list[str]:
        """根据 Skill 的 applicable_phases 自动分配到对应组。

        分配策略（按优先级）：
          1. 如果 applicable_phases 中某个 phase 有同名组，放入该组
          2. 否则查找 DEFAULT_PHASE_GROUPS 映射，把 Skill 放入该 phase
             对应的所有目标组
          3. 如果 applicable_phases 为空，放入 basic 组

        Returns:
            被分配到的组名列表
        """
        assigned: list[str] = []
        phases = skill.descriptor.applicable_phases

        if not phases:
            # 无 phase 限制 → 放入 basic 组
            for name, group in self._groups.items():
                if group.is_basic:
                    group.add_skill(skill)
                    assigned.append(name)
            return assigned

        # 查找每个 phase 对应的组
        seen: set[str] = set()
        for phase in phases:
            phase_lower = phase.lower()
            # 策略 1: 同名组直接放入
            if phase_lower in self._groups:
                self._groups[phase_lower].add_skill(skill)
                if phase_lower not in seen:
                    assigned.append(phase_lower)
                    seen.add(phase_lower)
            else:
                # 策略 2: 通过 DEFAULT_PHASE_GROUPS 映射查找目标组
                target_groups = DEFAULT_PHASE_GROUPS.get(phase_lower, [])
                for group_name in target_groups:
                    if group_name in self._groups and group_name not in seen:
                        self._groups[group_name].add_skill(skill)
                        assigned.append(group_name)
                        seen.add(group_name)

        return assigned
