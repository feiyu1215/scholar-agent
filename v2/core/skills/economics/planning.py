"""
core/skills/economics/planning.py — ReviewPlanningSkill (Planning 层)

Phase 级别的策略决策 Skill：
  - 根据论文特征决定审稿侧重点
  - 分配各 Functional Skill 的优先级和 token 预算
  - 生成审稿策略计划
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.skills.base import (
    Finding,
    Skill,
    SkillContext,
    SkillDescriptor,
    SkillLevel,
    SkillResult,
)


# ==============================================================
# 审稿策略
# ==============================================================

@dataclass
class ReviewStrategy:
    """审稿策略规划结果。

    Attributes:
        focus_areas: 重点关注领域（按优先级降序）
        skill_priorities: 各 Skill 的建议优先级 (skill_name -> priority 1-10)
        budget_allocation: 各 Skill 的 token 预算分配 (skill_name -> tokens)
        skip_reasons: 建议跳过的 Skill 及原因
        notes: 策略说明/注意事项
    """
    focus_areas: list[str] = field(default_factory=list)
    skill_priorities: dict[str, int] = field(default_factory=dict)
    budget_allocation: dict[str, int] = field(default_factory=dict)
    skip_reasons: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ==============================================================
# ReviewPlanningSkill
# ==============================================================

class ReviewPlanningSkill(Skill):
    """审稿策略规划 — 决定审稿的侧重点和资源分配。

    根据论文类型、方法、领域特征，决定：
    1. 哪些方面需要深入审查
    2. 各 Functional Skill 的优先级
    3. Token 预算如何分配
    4. 哪些检查可以跳过

    场景示例：
    - 实证论文（DID）→ 重点: 平行趋势验证、稳健性
    - 理论论文 → 重点: 逻辑一致性、数学推导
    - 综述论文 → 重点: 文献覆盖、分类框架
    """

    _DESCRIPTOR = SkillDescriptor(
        name="review_planning",
        level=SkillLevel.PLANNING,
        description="审稿策略规划：根据论文特征决定审稿侧重点和资源分配",
        prerequisites=(),
        input_schema={"paper_text": "str", "paper_metadata": "dict"},
        output_schema={"strategy": "ReviewStrategy"},
        applicable_phases=("orientation", "initial_scan"),
        tags=("planning", "strategy", "resource_allocation"),
        token_cost_estimate=300,
        version="1.0",
    )

    # 论文类型 → 重点领域映射
    _TYPE_FOCUS_MAP: dict[str, list[str]] = {
        "empirical": [
            "methodology_analysis",
            "statistical_validation",
            "logic_coherence",
            "citation_verification",
        ],
        "theoretical": [
            "logic_coherence",
            "citation_verification",
            "methodology_analysis",
        ],
        "review": [
            "citation_verification",
            "logic_coherence",
        ],
        "experimental": [
            "methodology_analysis",
            "statistical_validation",
            "logic_coherence",
        ],
    }

    # 默认 token 预算分配比例
    _DEFAULT_BUDGET_RATIOS: dict[str, float] = {
        "methodology_analysis": 0.35,
        "statistical_validation": 0.25,
        "logic_coherence": 0.20,
        "citation_verification": 0.15,
        "extract_numeric_claim": 0.03,
        "compare_with_domain_norm": 0.02,
    }

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._DESCRIPTOR

    def can_apply(self, context: SkillContext) -> float:
        """Planning Skill 在审稿开始阶段高度适用。"""
        phase = context.current_phase.lower()
        if phase in ("orientation", "initial_scan"):
            return 1.0
        return 0.2  # 其他阶段低适用度

    def execute(self, context: SkillContext) -> SkillResult:
        """生成审稿策略。"""
        strategy = self._plan_strategy(context)

        return SkillResult(
            findings=[],  # Planning Skill 不直接产出 Findings
            output_data={
                "strategy": {
                    "focus_areas": strategy.focus_areas,
                    "skill_priorities": strategy.skill_priorities,
                    "budget_allocation": strategy.budget_allocation,
                    "skip_reasons": strategy.skip_reasons,
                    "notes": strategy.notes,
                }
            },
            success=True,
        )

    def get_instruction(self) -> str:
        return (
            "# 审稿策略规划 (Review Planning)\n\n"
            "## 目标\n"
            "根据论文特征确定审稿重点，合理分配有限的分析资源。\n\n"
            "## 决策维度\n"
            "1. 论文类型（实证/理论/综述/实验）\n"
            "2. 研究方法（DID/IV/RDD/理论建模/实验设计）\n"
            "3. 领域特征（宏观/微观/发展/金融/劳动）\n"
            "4. 论文质量信号（期刊/作者/写作水平）\n\n"
            "## 输出\n"
            "- 重点关注领域（按优先级排序）\n"
            "- 各 Skill 的建议优先级\n"
            "- Token 预算分配方案\n"
            "- 可跳过的检查及原因\n"
        )

    # --- 内部方法 ---

    def _plan_strategy(self, context: SkillContext) -> ReviewStrategy:
        """根据上下文生成策略。"""
        strategy = ReviewStrategy()
        text_lower = context.paper_text.lower()
        metadata = context.paper_metadata

        # 1. 判断论文类型
        paper_type = self._infer_paper_type(text_lower, metadata)

        # 2. 确定重点领域
        strategy.focus_areas = self._TYPE_FOCUS_MAP.get(
            paper_type, self._TYPE_FOCUS_MAP["empirical"]
        )

        # 3. 设置优先级
        for i, area in enumerate(strategy.focus_areas):
            strategy.skill_priorities[area] = 10 - i * 2

        # 4. 分配 token 预算
        total_budget = context.token_budget
        strategy.budget_allocation = self._allocate_budget(
            paper_type, total_budget
        )

        # 5. 确定跳过项
        strategy.skip_reasons = self._determine_skips(paper_type, text_lower)

        # 6. 策略说明
        strategy.notes = self._generate_notes(paper_type, text_lower)

        return strategy

    def _infer_paper_type(self, text_lower: str, metadata: dict) -> str:
        """推断论文类型。"""
        # 优先使用 metadata
        explicit_type = metadata.get("paper_type", "").lower()
        if explicit_type in self._TYPE_FOCUS_MAP:
            return explicit_type

        # 基于文本推断
        empirical_signals = [
            "regression", "coefficient", "standard error",
            "table", "estimation", "sample",
            "回归", "估计", "样本",
        ]
        theory_signals = [
            "proposition", "theorem", "proof", "lemma",
            "equilibrium", "model",
            "命题", "定理", "证明", "均衡",
        ]
        review_signals = [
            "literature review", "survey", "meta-analysis",
            "文献综述", "元分析",
        ]

        empirical_score = sum(1 for s in empirical_signals if s in text_lower)
        theory_score = sum(1 for s in theory_signals if s in text_lower)
        review_score = sum(1 for s in review_signals if s in text_lower)

        scores = {
            "empirical": empirical_score,
            "theoretical": theory_score,
            "review": review_score,
        }
        return max(scores, key=scores.get)  # type: ignore[arg-type]

    def _allocate_budget(self, paper_type: str, total_budget: int) -> dict[str, int]:
        """根据论文类型分配 token 预算。"""
        ratios = dict(self._DEFAULT_BUDGET_RATIOS)

        # 根据论文类型调整比例
        if paper_type == "theoretical":
            ratios["methodology_analysis"] = 0.15
            ratios["logic_coherence"] = 0.40
            ratios["statistical_validation"] = 0.10
            ratios["citation_verification"] = 0.30
        elif paper_type == "review":
            ratios["methodology_analysis"] = 0.10
            ratios["logic_coherence"] = 0.30
            ratios["statistical_validation"] = 0.05
            ratios["citation_verification"] = 0.50

        # 归一化
        total_ratio = sum(ratios.values())
        return {
            name: int(total_budget * ratio / total_ratio)
            for name, ratio in ratios.items()
        }

    def _determine_skips(
        self, paper_type: str, text_lower: str
    ) -> dict[str, str]:
        """确定可跳过的检查。"""
        skips: dict[str, str] = {}

        if paper_type == "theoretical":
            skips["statistical_validation"] = "理论论文无统计分析"
            skips["extract_numeric_claim"] = "理论论文数值较少"

        if paper_type == "review":
            skips["statistical_validation"] = "综述论文无原始统计分析"
            skips["methodology_analysis"] = "综述论文无原始研究方法"

        return skips

    def _generate_notes(self, paper_type: str, text_lower: str) -> list[str]:
        """生成策略说明。"""
        notes = [f"论文类型判断: {paper_type}"]

        # 特殊方法检测
        if "did" in text_lower or "difference-in-difference" in text_lower:
            notes.append("使用 DID 方法 → 重点检查平行趋势假设验证")
        if "iv" in text_lower or "instrumental" in text_lower:
            notes.append("使用 IV 方法 → 重点检查工具变量有效性")
        if "rdd" in text_lower or "discontinuity" in text_lower:
            notes.append("使用 RDD 方法 → 重点检查操纵检验和带宽选择")

        return notes
