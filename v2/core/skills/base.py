"""
core/skills/base.py — SkillX 基础类型与抽象基类

定义 Skill 体系的核心协议：
  - SkillLevel: 三层层次枚举
  - SkillDescriptor: Skill 元数据（注册时加载，~100 tokens/skill）
  - SkillContext: 执行时传入的上下文
  - SkillResult: 执行结果
  - Finding: 审稿发现的结构化表示
  - Skill: 所有 Skill 的抽象基类
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ==============================================================
# 层次枚举
# ==============================================================

class SkillLevel(Enum):
    """SkillX 三层层次。

    - PLANNING: Phase 级别的策略决策（决定审稿侧重点、资源分配）
    - FUNCTIONAL: 子任务级别的完整能力（方法论分析、统计验证）
    - ATOMIC: 单次操作的封装（提取数值、格式检查）
    """
    PLANNING = "planning"
    FUNCTIONAL = "functional"
    ATOMIC = "atomic"


# ==============================================================
# 审稿发现
# ==============================================================

@dataclass
class Finding:
    """审稿发现的结构化表示。

    Attributes:
        category: 问题类别（methodology / statistics / logic / clarity / citation）
        severity: 严重程度（critical / major / minor / suggestion）
        description: 问题描述
        evidence: 具体证据（引用原文）
        suggestion: 修改建议
        location: 位置信息（section/paragraph/table）
        confidence: 置信度 0.0-1.0
        skill_source: 产出该 Finding 的 Skill 名称
    """
    category: str
    severity: str
    description: str
    evidence: str = ""
    suggestion: str = ""
    location: str = ""
    confidence: float = 0.8
    skill_source: str = ""


# ==============================================================
# Skill 描述符
# ==============================================================

@dataclass(frozen=True)
class SkillDescriptor:
    """Skill 元数据 — 注册时加载到上下文，占用 ~100 tokens。

    这是渐进式披露的第 1 层：只暴露名称和描述供 LLM/Selector 选择。

    Attributes:
        name: 技能唯一名称（snake_case）
        level: 技能层次
        description: 一句话描述（中文，用于选择器判断适用性）
        prerequisites: 依赖的其他 Skill 名称
        input_schema: 输入数据的字段描述（轻量 dict，非 JSON Schema）
        output_schema: 输出数据的字段描述
        applicable_phases: 适用的审稿阶段列表
        tags: 标签（用于分组和搜索）
        token_cost_estimate: 执行该 Skill 的预估 token 消耗
        version: 版本号（支持 A/B 比较）
    """
    name: str
    level: SkillLevel
    description: str
    prerequisites: tuple[str, ...] = ()
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    applicable_phases: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    token_cost_estimate: int = 0
    version: str = "1.0"


# ==============================================================
# Skill 上下文
# ==============================================================

@dataclass
class SkillContext:
    """执行 Skill 时的输入上下文。

    包含 Skill 执行所需的所有信息——论文内容、当前 Phase 状态、
    已有 Findings、以及可选的参数。

    Attributes:
        paper_text: 论文全文或当前段落
        paper_metadata: 论文元数据（标题、作者、领域、方法类型等）
        current_phase: 当前审稿阶段
        current_section: 当前正在分析的 section
        existing_findings: 已积累的 Findings（供跨 Skill 参考）
        text_claims: 文中的数值/因果声明列表
        parameters: Skill 特定参数
        token_budget: 该 Skill 执行的 token 预算上限
        session_id: 审稿会话 ID
    """
    paper_text: str = ""
    paper_metadata: dict = field(default_factory=dict)
    current_phase: str = ""
    current_section: str = ""
    existing_findings: list[Finding] = field(default_factory=list)
    text_claims: list[str] = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    token_budget: int = 2000
    session_id: str = ""


# ==============================================================
# Skill 执行结果
# ==============================================================

@dataclass
class SkillResult:
    """Skill 执行的返回结果。

    Attributes:
        findings: 产出的审稿发现列表
        output_data: Skill 特定的输出数据（供下游 Skill 使用）
        success: 是否执行成功
        error_message: 失败时的错误信息
        tokens_used: 实际消耗的 token 数
        execution_time_ms: 执行耗时（毫秒）
        metadata: 额外元数据（供性能追踪使用）
    """
    findings: list[Finding] = field(default_factory=list)
    output_data: dict = field(default_factory=dict)
    success: bool = True
    error_message: str = ""
    tokens_used: int = 0
    execution_time_ms: float = 0.0
    metadata: dict = field(default_factory=dict)


# ==============================================================
# Skill 抽象基类
# ==============================================================

class Skill(ABC):
    """所有 Skill 的抽象基类。

    子类必须实现：
      - descriptor (类属性): SkillDescriptor 元数据
      - execute(): 核心执行逻辑
      - can_apply(): 适用度评分

    可选 override：
      - validate_context(): 执行前验证输入
      - get_instruction(): 返回完整 SOP（渐进式第 2 层）
    """

    @property
    @abstractmethod
    def descriptor(self) -> SkillDescriptor:
        """返回该 Skill 的描述符（元数据）。"""
        ...

    @abstractmethod
    def execute(self, context: SkillContext) -> SkillResult:
        """执行技能核心逻辑。

        Args:
            context: 执行上下文，包含论文内容和 Phase 状态

        Returns:
            SkillResult 包含 findings 和输出数据
        """
        ...

    @abstractmethod
    def can_apply(self, context: SkillContext) -> float:
        """评估该 Skill 对当前上下文的适用度。

        Returns:
            0.0-1.0 的适用度分数。
            0.0 表示完全不适用，1.0 表示高度适用。
            Selector 将基于此分数进行动态组合。
        """
        ...

    def validate_context(self, context: SkillContext) -> tuple[bool, str]:
        """执行前验证输入上下文是否满足 prerequisites。

        Returns:
            (is_valid, error_message) 元组。
        """
        return True, ""

    def get_instruction(self) -> str:
        """返回该 Skill 的完整 SOP 文本（渐进式披露第 2 层）。

        默认返回 description。子类可以 override 返回详细指令。
        约 ~2k tokens。
        """
        return self.descriptor.description

    def get_metadata_prompt(self) -> str:
        """返回适合注入 system prompt 的元数据行（第 1 层）。

        格式：`- skill_name: description (适用于: phases)`
        """
        phases = ", ".join(self.descriptor.applicable_phases) if self.descriptor.applicable_phases else "all"
        return f"- {self.descriptor.name}: {self.descriptor.description} (适用于: {phases})"

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.descriptor.name} level={self.descriptor.level.value}>"
