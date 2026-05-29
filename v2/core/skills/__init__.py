"""
core/skills/ — SkillX 层次化技能体系 (Phase 3)

三层架构：
  - Planning Skill: Phase 级别的策略决策（审稿侧重点）
  - Functional Skill: 子任务级别的完整能力（方法论分析、统计验证等）
  - Atomic Skill: 单次操作的封装（提取数值、比较指标等）

设计原则：
  - 向后兼容：现有 SkillRegistry (registry.json) 继续工作
  - 渐进式披露：启动时仅加载元数据，触发时加载指令，执行时加载资源
  - Phase 感知：ToolGroup 按审稿阶段动态切换可用 Skill 集合
  - 独立可测：每个 Skill 可独立单元测试，不依赖完整 harness
"""

from core.skills.base import (
    Skill,
    SkillContext,
    SkillDescriptor,
    SkillLevel,
    SkillResult,
    Finding,
)

__all__ = [
    "Skill",
    "SkillContext",
    "SkillDescriptor",
    "SkillLevel",
    "SkillResult",
    "Finding",
]
