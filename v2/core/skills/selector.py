"""
core/skills/selector.py — 动态 Skill 选择器

根据论文特征（领域、方法类型、结构）和当前审稿 Phase 动态选择
最相关的 Skill 组合。

选择策略：
  1. Phase 过滤：只考虑声明适用于当前 Phase 的 Skills
  2. 适用度评分：调用每个 Skill 的 can_apply() 获取 0-1 分数
  3. Token 预算约束：在预算内贪心选择得分最高的 Skill 组合
  4. 前置依赖检查：确保 prerequisites 已满足

设计原则：
  - 选择结果可解释（返回每个 Skill 被选中/排除的原因）
  - 支持手动 override（用户强制启用/禁用特定 Skill）
  - 与现有 SkillRegistry 的 query() 方法互补而非替代
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from core.skills.base import Skill, SkillContext, SkillLevel

logger = logging.getLogger(__name__)


# ==============================================================
# 选择结果
# ==============================================================

@dataclass
class SelectionReason:
    """Skill 被选中或排除的原因（可解释性）。"""
    skill_name: str
    selected: bool
    score: float
    reason: str  # e.g. "phase mismatch", "budget exceeded", "score=0.85 > threshold"


@dataclass
class SelectionResult:
    """选择器的完整返回结果。"""
    selected_skills: list[Skill] = field(default_factory=list)
    reasons: list[SelectionReason] = field(default_factory=list)
    total_token_cost: int = 0
    budget_remaining: int = 0


# ==============================================================
# Skill 选择器
# ==============================================================

class SkillSelector:
    """动态 Skill 选择器 — 基于上下文自动组合最优 Skill 集。

    Usage:
        selector = SkillSelector(all_skills)
        result = selector.select(context, token_budget=4000)
        for skill in result.selected_skills:
            output = skill.execute(context)
    """

    def __init__(
        self,
        skills: list[Skill],
        score_threshold: float = 0.3,
    ):
        """
        Args:
            skills: 可选的全部 Skill 实例列表
            score_threshold: can_apply() 的最低分数阈值
        """
        self._skills = list(skills)
        self._score_threshold = score_threshold
        # 手动 override
        self._force_enable: set[str] = set()
        self._force_disable: set[str] = set()

    @property
    def all_skills(self) -> list[Skill]:
        """返回注册的全部 Skill。"""
        return list(self._skills)

    def register(self, skill: Skill) -> None:
        """注册新 Skill（去重）。"""
        existing_names = {s.descriptor.name for s in self._skills}
        if skill.descriptor.name in existing_names:
            logger.warning(
                "[SkillSelector] Skill '%s' already registered, skipping",
                skill.descriptor.name,
            )
            return
        self._skills.append(skill)

    def unregister(self, skill_name: str) -> bool:
        """注销 Skill。"""
        for i, s in enumerate(self._skills):
            if s.descriptor.name == skill_name:
                self._skills.pop(i)
                return True
        return False

    def force_enable(self, skill_name: str) -> None:
        """强制启用某 Skill（跳过 can_apply 检查）。"""
        self._force_enable.add(skill_name)
        self._force_disable.discard(skill_name)

    def force_disable(self, skill_name: str) -> None:
        """强制禁用某 Skill。"""
        self._force_disable.add(skill_name)
        self._force_enable.discard(skill_name)

    def clear_overrides(self) -> None:
        """清除所有手动 override。"""
        self._force_enable.clear()
        self._force_disable.clear()

    def select(
        self,
        context: SkillContext,
        token_budget: int = 4000,
        level_filter: Optional[SkillLevel] = None,
    ) -> SelectionResult:
        """根据上下文选择最优 Skill 组合。

        选择流程：
          1. Phase 过滤 + Level 过滤
          2. 应用 force_enable / force_disable
          3. 调用 can_apply() 获取适用度分数
          4. 按分数降序排列
          5. 前置依赖检查
          6. Token 预算贪心填充

        Args:
            context: 当前执行上下文
            token_budget: token 预算上限
            level_filter: 只选择特定层次的 Skill（None 表示不限）

        Returns:
            SelectionResult
        """
        result = SelectionResult(budget_remaining=token_budget)
        scored: list[tuple[Skill, float]] = []

        for skill in self._skills:
            name = skill.descriptor.name

            # 1. 强制禁用
            if name in self._force_disable:
                result.reasons.append(SelectionReason(
                    skill_name=name, selected=False, score=0.0,
                    reason="force_disabled",
                ))
                continue

            # 2. Level 过滤
            if level_filter is not None and skill.descriptor.level != level_filter:
                result.reasons.append(SelectionReason(
                    skill_name=name, selected=False, score=0.0,
                    reason=f"level_mismatch: want {level_filter.value}, got {skill.descriptor.level.value}",
                ))
                continue

            # 3. Phase 过滤（空 applicable_phases 表示适用所有 Phase）
            if skill.descriptor.applicable_phases and context.current_phase:
                phase_lower = context.current_phase.lower()
                applicable = [p.lower() for p in skill.descriptor.applicable_phases]
                if phase_lower not in applicable:
                    result.reasons.append(SelectionReason(
                        skill_name=name, selected=False, score=0.0,
                        reason=f"phase_mismatch: current={context.current_phase}",
                    ))
                    continue

            # 4. 强制启用（跳过 can_apply）
            if name in self._force_enable:
                scored.append((skill, 1.0))
                continue

            # 5. 适用度评分
            try:
                score = skill.can_apply(context)
            except Exception as exc:
                logger.warning(
                    "[SkillSelector] can_apply() failed for '%s': %s", name, exc
                )
                score = 0.0

            if score < self._score_threshold:
                result.reasons.append(SelectionReason(
                    skill_name=name, selected=False, score=score,
                    reason=f"score_below_threshold: {score:.2f} < {self._score_threshold}",
                ))
                continue

            scored.append((skill, score))

        # 6. 按分数降序排列
        scored.sort(key=lambda x: x[1], reverse=True)

        # 7. 收集已选名称（用于依赖检查）
        selected_names: set[str] = set()
        remaining_budget = token_budget

        for skill, score in scored:
            name = skill.descriptor.name

            # 前置依赖检查
            unmet = [
                dep for dep in skill.descriptor.prerequisites
                if dep not in selected_names
            ]
            if unmet:
                result.reasons.append(SelectionReason(
                    skill_name=name, selected=False, score=score,
                    reason=f"unmet_prerequisites: {unmet}",
                ))
                continue

            # Token 预算检查
            cost = skill.descriptor.token_cost_estimate
            if cost > remaining_budget:
                result.reasons.append(SelectionReason(
                    skill_name=name, selected=False, score=score,
                    reason=f"budget_exceeded: need {cost}, remaining {remaining_budget}",
                ))
                continue

            # 选中
            result.selected_skills.append(skill)
            selected_names.add(name)
            remaining_budget -= cost
            result.total_token_cost += cost
            result.reasons.append(SelectionReason(
                skill_name=name, selected=True, score=score,
                reason=f"selected: score={score:.2f}, cost={cost}",
            ))

        result.budget_remaining = remaining_budget
        return result

    def select_by_level(
        self,
        context: SkillContext,
        level: SkillLevel,
        token_budget: int = 4000,
    ) -> SelectionResult:
        """便捷方法：只选择特定层次的 Skill。"""
        return self.select(context, token_budget=token_budget, level_filter=level)
