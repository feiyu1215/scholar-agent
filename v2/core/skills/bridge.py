"""
core/skills/bridge.py — 向后兼容桥接层

将现有 SkillRegistry (registry.json Knowledge/Action Skills) 桥接到
新的 SkillX 体系，确保：
  1. 现有 Knowledge Skills 可作为资源被 Functional Skills 引用
  2. 现有 Action Skills 继续通过原有 handler 机制工作
  3. 新旧系统可以共存，渐进迁移

桥接策略：
  - KnowledgeSkillAdapter: 将 SkillMeta (knowledge type) 包装为 SkillX Skill
  - ActionSkillAdapter: 将 SkillMeta (action type) 包装为 SkillX Atomic Skill
  - UnifiedRegistry: 合并新旧两个注册表的统一查询入口
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from core.skills.base import (
    Finding,
    Skill,
    SkillContext,
    SkillDescriptor,
    SkillLevel,
    SkillResult,
)

logger = logging.getLogger(__name__)


# ==============================================================
# Knowledge Skill 适配器
# ==============================================================

class KnowledgeSkillAdapter(Skill):
    """将现有 registry.json 中的 Knowledge Skill 适配为 SkillX Skill。

    Knowledge Skills 在 SkillX 中映射为 FUNCTIONAL 级别：
    - 它们提供领域知识（如 review_criteria, methodology_checklist）
    - execute() 返回知识内容作为 output_data
    - 不产出 Findings（知识本身不做判断）
    """

    def __init__(self, skill_meta, registry):
        """
        Args:
            skill_meta: 来自 SkillRegistry 的 SkillMeta 实例
            registry: SkillRegistry 实例（用于 load_content）
        """
        self._meta = skill_meta
        self._registry = registry
        self._descriptor = SkillDescriptor(
            name=f"knowledge_{skill_meta.id}",
            level=SkillLevel.FUNCTIONAL,
            description=skill_meta.description or f"Knowledge: {skill_meta.name}",
            prerequisites=(),
            input_schema={},
            output_schema={"content": "str"},
            applicable_phases=tuple(
                p.lower() for p in skill_meta.applicable_phases
            ),
            tags=tuple(skill_meta.tags) + ("knowledge", "legacy"),
            token_cost_estimate=skill_meta.token_estimate,
            version="1.0",
        )

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._descriptor

    def can_apply(self, context: SkillContext) -> float:
        """Knowledge Skills 在对应 Phase 始终适用。"""
        if not self._meta.applicable_phases:
            return 0.5  # 无 phase 限制时中等适用度

        if context.current_phase:
            phase_lower = context.current_phase.lower()
            applicable = [p.lower() for p in self._meta.applicable_phases]
            if phase_lower in applicable:
                return 0.8
        return 0.3

    def execute(self, context: SkillContext) -> SkillResult:
        """加载并返回 Knowledge 内容。"""
        content = self._registry.load_content(self._meta.id)
        if content is None:
            return SkillResult(
                success=False,
                error_message=f"Failed to load content for skill: {self._meta.id}",
            )
        return SkillResult(
            findings=[],
            output_data={"content": content, "source_skill_id": self._meta.id},
            success=True,
        )

    def get_instruction(self) -> str:
        """返回完整知识内容作为 Layer 2 指令。"""
        content = self._registry.load_content(self._meta.id)
        return content or self._descriptor.description


# ==============================================================
# Action Skill 适配器
# ==============================================================

class ActionSkillAdapter(Skill):
    """将现有 registry.json 中的 Action Skill 适配为 SkillX Atomic Skill。

    Action Skills 在 SkillX 中映射为 ATOMIC 级别：
    - 它们是单次工具调用的封装
    - execute() 委托给原有 handler 机制
    """

    def __init__(self, skill_meta, handler_loader):
        """
        Args:
            skill_meta: 来自 SkillRegistry 的 SkillMeta 实例
            handler_loader: SkillHandlerLoader 实例
        """
        self._meta = skill_meta
        self._handler_loader = handler_loader
        self._descriptor = SkillDescriptor(
            name=f"action_{skill_meta.id}",
            level=SkillLevel.ATOMIC,
            description=skill_meta.description or f"Action: {skill_meta.name}",
            prerequisites=(),
            input_schema={},
            output_schema={"result": "str"},
            applicable_phases=tuple(
                p.lower() for p in skill_meta.applicable_phases
            ),
            tags=tuple(skill_meta.tags) + ("action", "legacy"),
            token_cost_estimate=skill_meta.token_estimate,
            version="1.0",
        )

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._descriptor

    def can_apply(self, context: SkillContext) -> float:
        """Action Skills 按 phase 匹配。"""
        if not self._meta.applicable_phases:
            return 0.4

        if context.current_phase:
            phase_lower = context.current_phase.lower()
            applicable = [p.lower() for p in self._meta.applicable_phases]
            if phase_lower in applicable:
                return 0.7
        return 0.2

    def execute(self, context: SkillContext) -> SkillResult:
        """通过原有 handler 机制执行。"""
        if not self._meta.tools:
            return SkillResult(
                success=False,
                error_message="Action skill has no tools defined",
            )

        # 加载第一个 tool 的 handler
        tool_def = self._meta.tools[0]
        try:
            handler_fn = self._handler_loader.load(tool_def.handler)
        except Exception as exc:
            return SkillResult(
                success=False,
                error_message=f"Failed to load handler: {exc}",
            )

        # 执行 handler
        try:
            result_str = handler_fn(context.parameters, None)
            return SkillResult(
                findings=[],
                output_data={"result": result_str},
                success=True,
            )
        except Exception as exc:
            return SkillResult(
                success=False,
                error_message=f"Handler execution failed: {exc}",
            )


# ==============================================================
# 统一注册表
# ==============================================================

class UnifiedSkillRegistry:
    """合并 SkillX 新体系与旧 SkillRegistry 的统一入口。

    提供统一的查询接口，内部自动桥接：
    - 新 SkillX Skills（直接注册的 Skill 实例）
    - 旧 Knowledge Skills（通过 KnowledgeSkillAdapter 桥接）
    - 旧 Action Skills（通过 ActionSkillAdapter 桥接）

    Usage:
        unified = UnifiedSkillRegistry(
            skillx_skills=[MethodologyAnalysisSkill(), ...],
            legacy_registry=SkillRegistry(Path("v2/skills")),
            handler_loader=SkillHandlerLoader(Path("v2/skills/skill_handlers")),
        )
        all_skills = unified.all_skills()
    """

    def __init__(
        self,
        skillx_skills: Optional[list[Skill]] = None,
        legacy_registry=None,
        handler_loader=None,
    ):
        """
        Args:
            skillx_skills: 新 SkillX Skill 实例列表
            legacy_registry: 旧 SkillRegistry 实例（可选）
            handler_loader: 旧 SkillHandlerLoader 实例（可选）
        """
        self._native_skills: list[Skill] = list(skillx_skills) if skillx_skills else []
        self._adapted_skills: list[Skill] = []
        self._legacy_registry = legacy_registry
        self._handler_loader = handler_loader

        # 桥接旧 Skills
        if legacy_registry is not None:
            self._bridge_legacy_skills()

    def _bridge_legacy_skills(self) -> None:
        """将旧注册表中的 Skills 桥接为 SkillX Skills。"""
        if self._legacy_registry is None:
            return

        for meta in self._legacy_registry.all_skills:
            try:
                if meta.type == "knowledge":
                    adapter = KnowledgeSkillAdapter(meta, self._legacy_registry)
                    self._adapted_skills.append(adapter)
                elif meta.type == "action" and self._handler_loader is not None:
                    adapter = ActionSkillAdapter(meta, self._handler_loader)
                    self._adapted_skills.append(adapter)
            except Exception as exc:
                logger.warning(
                    "[UnifiedRegistry] Failed to bridge skill '%s': %s",
                    meta.id, exc,
                )

    def all_skills(self) -> list[Skill]:
        """返回所有 Skill（原生 + 桥接）。"""
        return self._native_skills + self._adapted_skills

    def native_skills(self) -> list[Skill]:
        """只返回原生 SkillX Skills。"""
        return list(self._native_skills)

    def adapted_skills(self) -> list[Skill]:
        """只返回桥接的旧 Skills。"""
        return list(self._adapted_skills)

    def get_by_name(self, name: str) -> Optional[Skill]:
        """按名称查找 Skill。"""
        for skill in self.all_skills():
            if skill.descriptor.name == name:
                return skill
        return None

    def get_by_level(self, level: SkillLevel) -> list[Skill]:
        """按层次过滤。"""
        return [s for s in self.all_skills() if s.descriptor.level == level]

    def register_native(self, skill: Skill) -> None:
        """注册新的原生 SkillX Skill。"""
        existing = {s.descriptor.name for s in self._native_skills}
        if skill.descriptor.name in existing:
            logger.warning(
                "[UnifiedRegistry] Skill '%s' already registered",
                skill.descriptor.name,
            )
            return
        self._native_skills.append(skill)
