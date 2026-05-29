"""
training/adversarial.py — 对抗样本生成器 (Adversarial Generator)

自动生成具有挑战性的学术论文片段，用于测试和提升 Agent 的审稿分析能力。

核心组件:
    1. AdversarialCase — 对抗样本数据结构（挑战 + gold label + 难度标签）
    2. ChallengeType — 挑战类型枚举（对应不同的缺陷模式）
    3. DifficultyLevel — 难度等级（从 TRIVIAL 到 EXPERT）
    4. DifficultyController — 难度梯度控制（Complete 层: 自适应调难度）
    5. MultiDimensionChallengeFactory — 多维度挑战生成工厂（Complete 层）
    6. AdversarialGenerator — 核心生成器（整合弱点画像 → 对抗样本）

设计原则:
    - LLM 驱动: 使用 LLM 生成逼真的学术论文片段，不是硬编码模板
    - Gold Label 必带: 每个对抗样本必须包含正确答案，否则无法评估
    - 难度渐进: 支持从简单到困难的梯度生成（课程学习的基础）
    - 多维度: 不仅针对内容，还针对格式/跨章节/数据一致性等
    - 变体生成: 从历史失败中生成"类似但不同"的挑战，防止过拟合
    - Kill Switch 守卫: OFF 时所有方法返回空/默认值

Kill Switch: SCHOLAR_GODEL_ADVERSARIAL_TRAINING
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from training.weakness_analyzer import (
    WeaknessDimension,
    WeaknessEntry,
    WeaknessProfile,
)

from core.godel_config import GODEL_ADVERSARIAL_TRAINING_ENABLED

logger = logging.getLogger(__name__)


# ==============================================================
# Kill Switch (delegate to godel_config — single source of truth)
# ==============================================================

ADVERSARIAL_TRAINING_ENABLED: bool = GODEL_ADVERSARIAL_TRAINING_ENABLED
"""Backward-compatible alias. Actual source of truth is core.godel_config."""


# ==============================================================
# 挑战类型
# ==============================================================

class ChallengeType(str, Enum):
    """对抗样本的挑战类型——对应不同的学术缺陷模式。"""

    # --- 方法论缺陷 ---
    HIDDEN_ENDOGENEITY = "hidden_endogeneity"
    """隐藏的内生性问题（表面看识别策略合理，实际有遗漏变量/反向因果）。"""

    WEAK_INSTRUMENT = "weak_instrument"
    """弱工具变量（F 统计量边缘、排除性约束可疑但表述模糊）。"""

    PARALLEL_TREND_VIOLATION = "parallel_trend_violation"
    """平行趋势违反（作者声称满足但图/数据暗示不满足）。"""

    MANIPULATION_NEAR_CUTOFF = "manipulation_near_cutoff"
    """断点附近操纵（density test 边缘通过，但分布可疑）。"""

    CHERRY_PICKED_SAMPLE = "cherry_picked_sample"
    """样本筛选偏差（排除条件合理化但暗藏偏见）。"""

    # --- 统计推断缺陷 ---
    MULTIPLE_TESTING = "multiple_testing"
    """多重检验问题（报告大量回归但未做校正）。"""

    P_HACKING_SIGNAL = "p_hacking_signal"
    """p-hacking 信号（系数恰好在 0.05 边界，敏感性分析缺失）。"""

    MISINTERPRETED_SIGNIFICANCE = "misinterpreted_significance"
    """统计显著性误读（经济显著性 vs 统计显著性混淆）。"""

    HETEROSCEDASTICITY_IGNORED = "heteroscedasticity_ignored"
    """异方差被忽略（标准误类型选择不当）。"""

    # --- 数据一致性缺陷 ---
    TABLE_TEXT_CONTRADICTION = "table_text_contradiction"
    """表格与文本描述矛盾（数字对不上但差异微妙）。"""

    FIGURE_CLAIM_MISMATCH = "figure_claim_mismatch"
    """图表与论文声明不匹配（图暗示一个方向，文字声明另一个）。"""

    SAMPLE_SIZE_INCONSISTENCY = "sample_size_inconsistency"
    """样本量在不同位置不一致。"""

    # --- 逻辑缺陷 ---
    CIRCULAR_REASONING = "circular_reasoning"
    """循环论证（结论已预设在假设中但包装得隐蔽）。"""

    NON_SEQUITUR_CONCLUSION = "non_sequitur_conclusion"
    """结论不由前提推出（跳跃过大但学术语言掩盖了逻辑断裂）。"""

    ECOLOGICAL_FALLACY = "ecological_fallacy"
    """生态谬误（群体层面推断个体行为）。"""

    # --- 格式/引用缺陷 ---
    MISSING_KEY_CITATION = "missing_key_citation"
    """遗漏关键引文（该领域最重要的先驱工作未引用）。"""

    SELF_PLAGIARISM = "self_plagiarism"
    """自我抄袭（大段与作者前作雷同但未标注）。"""

    # --- 跨章节推理缺陷 ---
    INTRODUCTION_RESULTS_DISCONNECT = "introduction_results_disconnect"
    """引言与结果脱节（引言承诺的贡献在结果中未兑现）。"""

    ROBUSTNESS_CONTRADICTS_MAIN = "robustness_contradicts_main"
    """稳健性检验实际推翻了主结论但作者轻描淡写。"""


# ==============================================================
# 难度等级
# ==============================================================

class DifficultyLevel(str, Enum):
    """对抗样本的难度等级。"""

    TRIVIAL = "trivial"
    """显而易见的问题（新手也能发现）。用于基线验证。
    特征: 错误明确、证据直接、无需推理。"""

    EASY = "easy"
    """容易发现的问题（有经验的研究者快速发现）。
    特征: 错误较明确，但需要一定领域知识。"""

    MEDIUM = "medium"
    """中等难度（需要仔细阅读和分析才能发现）。
    特征: 错误隐蔽，需要跨段落推理或领域知识。"""

    HARD = "hard"
    """困难（需要深度分析和专业知识）。
    特征: 错误被合理化语言掩盖，需要专家级判断。"""

    EXPERT = "expert"
    """专家级（资深审稿人才可能发现的微妙问题）。
    特征: 错误极其隐蔽，论文整体看似完美，只有细微不一致暴露问题。"""


# ==============================================================
# 对抗样本
# ==============================================================

@dataclass
class AdversarialCase:
    """一个对抗性测试用例。

    包含：挑战论文片段 + 正确答案（gold label）+ 元数据。
    设计为可直接转为 EvalPaper 用于 AgentRunner.run()。
    """

    # --- 核心内容 ---
    case_id: str = ""
    paper_snippet: str = ""
    """生成的学术论文片段（可能包含多个 section）。"""

    gold_findings: list[dict] = field(default_factory=list)
    """正确答案：Agent 应该发现的问题。每项格式与 Finding.to_dict() 一致。"""

    gold_explanation: str = ""
    """详细解释：为什么这是一个问题、正确的分析是什么。"""

    # --- 元数据 ---
    challenge_type: ChallengeType = ChallengeType.HIDDEN_ENDOGENEITY
    difficulty: DifficultyLevel = DifficultyLevel.MEDIUM
    target_dimension: WeaknessDimension = WeaknessDimension.METHODOLOGY_ANALYSIS

    # 生成上下文
    source_weakness_id: str = ""
    """触发生成此样本的弱点 ID。"""

    generated_from_failure: bool = False
    """是否从历史失败变体生成。"""

    parent_case_id: str = ""
    """如果是变体，指向原始 case。"""

    # --- 质量标注 ---
    quality_verified: bool = False
    """是否经过质量验证（人工或自动验证 gold label 正确性）。"""

    agent_pass_rate: float = -1.0
    """Agent 在此样本上的通过率（-1 = 未评估）。"""

    # --- 时间信息 ---
    created_at: float = field(default_factory=time.time)
    last_used: float = 0.0
    use_count: int = 0

    def __post_init__(self):
        if not self.case_id:
            content = f"{self.challenge_type.value}:{self.paper_snippet[:50]}:{self.created_at}"
            self.case_id = hashlib.md5(content.encode()).hexdigest()[:16]

    @property
    def is_evaluated(self) -> bool:
        return self.agent_pass_rate >= 0.0

    @property
    def is_effective_challenge(self) -> bool:
        """是否是有效挑战（Agent 通过率 < 70%）。"""
        return 0.0 <= self.agent_pass_rate < 0.7

    def record_usage(self, passed: bool) -> None:
        """记录一次使用。"""
        self.use_count += 1
        self.last_used = time.time()
        if self.agent_pass_rate < 0:
            self.agent_pass_rate = 1.0 if passed else 0.0
        else:
            # 指数移动平均
            alpha = 0.3
            self.agent_pass_rate = alpha * (1.0 if passed else 0.0) + (1 - alpha) * self.agent_pass_rate

    def to_eval_paper_dict(self) -> dict:
        """转换为 EvalPaper 兼容格式，可直接用于 AgentRunner。"""
        from core.skills.base import Finding

        findings = []
        for gf in self.gold_findings:
            findings.append(gf)

        return {
            "paper_id": f"adversarial_{self.case_id}",
            "title": f"[Adversarial:{self.challenge_type.value}] Difficulty={self.difficulty.value}",
            "sections": [self.paper_snippet],
            "gold_findings": findings,
            "metadata": {
                "is_adversarial": True,
                "challenge_type": self.challenge_type.value,
                "difficulty": self.difficulty.value,
                "target_dimension": self.target_dimension.value,
                "gold_explanation": self.gold_explanation,
            },
        }

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "paper_snippet": self.paper_snippet,
            "gold_findings": self.gold_findings,
            "gold_explanation": self.gold_explanation,
            "challenge_type": self.challenge_type.value,
            "difficulty": self.difficulty.value,
            "target_dimension": self.target_dimension.value,
            "source_weakness_id": self.source_weakness_id,
            "generated_from_failure": self.generated_from_failure,
            "parent_case_id": self.parent_case_id,
            "quality_verified": self.quality_verified,
            "agent_pass_rate": self.agent_pass_rate,
            "created_at": self.created_at,
            "last_used": self.last_used,
            "use_count": self.use_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AdversarialCase":
        try:
            challenge_type = ChallengeType(data.get("challenge_type", "hidden_endogeneity"))
        except ValueError:
            challenge_type = ChallengeType.HIDDEN_ENDOGENEITY

        try:
            difficulty = DifficultyLevel(data.get("difficulty", "medium"))
        except ValueError:
            difficulty = DifficultyLevel.MEDIUM

        try:
            target_dim = WeaknessDimension(data.get("target_dimension", "methodology_analysis"))
        except ValueError:
            target_dim = WeaknessDimension.METHODOLOGY_ANALYSIS

        return cls(
            case_id=data.get("case_id", ""),
            paper_snippet=data.get("paper_snippet", ""),
            gold_findings=data.get("gold_findings", []),
            gold_explanation=data.get("gold_explanation", ""),
            challenge_type=challenge_type,
            difficulty=difficulty,
            target_dimension=target_dim,
            source_weakness_id=data.get("source_weakness_id", ""),
            generated_from_failure=data.get("generated_from_failure", False),
            parent_case_id=data.get("parent_case_id", ""),
            quality_verified=data.get("quality_verified", False),
            agent_pass_rate=data.get("agent_pass_rate", -1.0),
            created_at=data.get("created_at", time.time()),
            last_used=data.get("last_used", 0.0),
            use_count=data.get("use_count", 0),
        )


# ==============================================================
# LLM 协议（面向 LLM 调用的松耦合接口）
# ==============================================================

@runtime_checkable
class LLMGenerator(Protocol):
    """LLM 生成器协议 — 用于生成对抗样本内容。"""

    async def generate(self, prompt: str, schema: Optional[dict] = None) -> dict:
        """调用 LLM 生成结构化内容。

        Args:
            prompt: 生成提示词
            schema: 期望的输出 JSON schema（可选）

        Returns:
            生成的结构化数据
        """
        ...


# ==============================================================
# 难度控制器（Complete 层）
# ==============================================================

@dataclass
class DifficultyState:
    """难度控制器的内部状态。"""
    current_level: DifficultyLevel = DifficultyLevel.EASY
    consecutive_passes: int = 0
    consecutive_fails: int = 0
    level_history: list[tuple[DifficultyLevel, float]] = field(default_factory=list)
    # 每个难度等级的 pass rate 统计
    level_stats: dict[str, dict[str, float]] = field(default_factory=lambda: {
        level.value: {"attempts": 0, "passes": 0}
        for level in DifficultyLevel
    })


class DifficultyController:
    """难度梯度控制器（Complete 层）。

    自适应调整对抗样本的难度，确保训练在 "挑战但不崩溃" 的最优区间。

    策略:
        - Zone of Proximal Development (ZPD): 维持 30%-70% 的通过率
        - 连续通过 → 升级难度
        - 连续失败 → 降级难度
        - 学习曲线追踪: 记录各难度级别的历史 pass rate
    """

    # 难度等级顺序（从低到高）
    LEVEL_ORDER: list[DifficultyLevel] = [
        DifficultyLevel.TRIVIAL,
        DifficultyLevel.EASY,
        DifficultyLevel.MEDIUM,
        DifficultyLevel.HARD,
        DifficultyLevel.EXPERT,
    ]

    def __init__(
        self,
        initial_level: DifficultyLevel = DifficultyLevel.EASY,
        upgrade_threshold: int = 3,
        downgrade_threshold: int = 2,
        target_pass_rate_low: float = 0.3,
        target_pass_rate_high: float = 0.7,
    ):
        """
        Args:
            initial_level: 起始难度
            upgrade_threshold: 连续通过多少次后升级
            downgrade_threshold: 连续失败多少次后降级
            target_pass_rate_low: 目标通过率下限
            target_pass_rate_high: 目标通过率上限
        """
        self._state = DifficultyState(current_level=initial_level)
        self._upgrade_threshold = upgrade_threshold
        self._downgrade_threshold = downgrade_threshold
        self._target_low = target_pass_rate_low
        self._target_high = target_pass_rate_high

    @property
    def current_level(self) -> DifficultyLevel:
        return self._state.current_level

    @property
    def state(self) -> DifficultyState:
        return self._state

    def get_recommended_difficulty(self) -> DifficultyLevel:
        """获取推荐的难度等级。

        基于当前状态和历史统计综合决策。
        """
        if not ADVERSARIAL_TRAINING_ENABLED:
            return DifficultyLevel.MEDIUM

        return self._state.current_level

    def record_result(self, difficulty: DifficultyLevel, passed: bool) -> DifficultyLevel:
        """记录一次训练结果并可能调整难度。

        Args:
            difficulty: 该样本的难度
            passed: Agent 是否通过了挑战

        Returns:
            更新后的推荐难度等级
        """
        if not ADVERSARIAL_TRAINING_ENABLED:
            return DifficultyLevel.MEDIUM

        # 更新统计
        stats = self._state.level_stats[difficulty.value]
        stats["attempts"] += 1
        if passed:
            stats["passes"] += 1

        # 只有当结果难度 == 当前难度时才触发升降级
        if difficulty == self._state.current_level:
            if passed:
                self._state.consecutive_passes += 1
                self._state.consecutive_fails = 0
            else:
                self._state.consecutive_fails += 1
                self._state.consecutive_passes = 0

            # 升级判断
            if self._state.consecutive_passes >= self._upgrade_threshold:
                self._upgrade()

            # 降级判断
            elif self._state.consecutive_fails >= self._downgrade_threshold:
                self._downgrade()

        # 记录历史
        self._state.level_history.append((self._state.current_level, time.time()))

        return self._state.current_level

    def get_level_pass_rate(self, level: DifficultyLevel) -> float:
        """获取某个难度级别的历史通过率。"""
        stats = self._state.level_stats[level.value]
        if stats["attempts"] == 0:
            return -1.0  # 未评估
        return stats["passes"] / stats["attempts"]

    def get_mastery_profile(self) -> dict[str, float]:
        """获取各难度级别的掌握度画像。"""
        return {
            level.value: self.get_level_pass_rate(level)
            for level in DifficultyLevel
        }

    def is_in_zpd(self) -> bool:
        """当前难度是否在 ZPD（最优学习区间）内。"""
        rate = self.get_level_pass_rate(self._state.current_level)
        if rate < 0:
            return True  # 未评估，假设在 ZPD 内
        return self._target_low <= rate <= self._target_high

    def force_level(self, level: DifficultyLevel) -> None:
        """强制设置难度（用于外部干预或课程学习覆盖）。"""
        self._state.current_level = level
        self._state.consecutive_passes = 0
        self._state.consecutive_fails = 0

    def _upgrade(self) -> None:
        """升级难度。"""
        idx = self.LEVEL_ORDER.index(self._state.current_level)
        if idx < len(self.LEVEL_ORDER) - 1:
            self._state.current_level = self.LEVEL_ORDER[idx + 1]
            self._state.consecutive_passes = 0
            self._state.consecutive_fails = 0
            logger.info(
                "[DifficultyController] Upgraded to %s",
                self._state.current_level.value,
            )

    def _downgrade(self) -> None:
        """降级难度。"""
        idx = self.LEVEL_ORDER.index(self._state.current_level)
        if idx > 0:
            self._state.current_level = self.LEVEL_ORDER[idx - 1]
            self._state.consecutive_passes = 0
            self._state.consecutive_fails = 0
            logger.info(
                "[DifficultyController] Downgraded to %s",
                self._state.current_level.value,
            )

    def serialize(self) -> dict:
        return {
            "current_level": self._state.current_level.value,
            "consecutive_passes": self._state.consecutive_passes,
            "consecutive_fails": self._state.consecutive_fails,
            "level_stats": self._state.level_stats,
            "upgrade_threshold": self._upgrade_threshold,
            "downgrade_threshold": self._downgrade_threshold,
            "target_low": self._target_low,
            "target_high": self._target_high,
        }

    @classmethod
    def deserialize(cls, data: dict) -> "DifficultyController":
        try:
            initial = DifficultyLevel(data.get("current_level", "easy"))
        except ValueError:
            initial = DifficultyLevel.EASY

        ctrl = cls(
            initial_level=initial,
            upgrade_threshold=data.get("upgrade_threshold", 3),
            downgrade_threshold=data.get("downgrade_threshold", 2),
            target_pass_rate_low=data.get("target_low", 0.3),
            target_pass_rate_high=data.get("target_high", 0.7),
        )
        ctrl._state.consecutive_passes = data.get("consecutive_passes", 0)
        ctrl._state.consecutive_fails = data.get("consecutive_fails", 0)
        ctrl._state.level_stats = data.get("level_stats", ctrl._state.level_stats)
        return ctrl


# ==============================================================
# 多维度挑战工厂（Complete 层）
# ==============================================================

class MultiDimensionChallengeFactory:
    """多维度挑战生成工厂。

    不仅针对内容分析能力，还针对:
        - 格式理解（非标准论文结构）
        - 跨章节推理（需要连接多个 section 的信息）
        - 数据一致性（表格/图/文本之间的微妙矛盾）
        - 引用覆盖度（缺少关键引文）
        - 逻辑连贯性（论证链中的隐蔽断裂）

    工厂根据 WeaknessProfile 的维度分布，按比例分配各维度的挑战数量。
    """

    # 维度 → 适用的挑战类型映射
    DIMENSION_CHALLENGES: dict[WeaknessDimension, list[ChallengeType]] = {
        WeaknessDimension.METHODOLOGY_ANALYSIS: [
            ChallengeType.HIDDEN_ENDOGENEITY,
            ChallengeType.CHERRY_PICKED_SAMPLE,
        ],
        WeaknessDimension.STATISTICAL_REASONING: [
            ChallengeType.MULTIPLE_TESTING,
            ChallengeType.P_HACKING_SIGNAL,
            ChallengeType.MISINTERPRETED_SIGNIFICANCE,
            ChallengeType.HETEROSCEDASTICITY_IGNORED,
        ],
        WeaknessDimension.CAUSAL_INFERENCE: [
            ChallengeType.HIDDEN_ENDOGENEITY,
            ChallengeType.ECOLOGICAL_FALLACY,
        ],
        WeaknessDimension.DATA_CONSISTENCY: [
            ChallengeType.TABLE_TEXT_CONTRADICTION,
            ChallengeType.FIGURE_CLAIM_MISMATCH,
            ChallengeType.SAMPLE_SIZE_INCONSISTENCY,
        ],
        WeaknessDimension.LOGICAL_COHERENCE: [
            ChallengeType.CIRCULAR_REASONING,
            ChallengeType.NON_SEQUITUR_CONCLUSION,
            ChallengeType.ECOLOGICAL_FALLACY,
        ],
        WeaknessDimension.DID_ANALYSIS: [
            ChallengeType.PARALLEL_TREND_VIOLATION,
        ],
        WeaknessDimension.IV_ANALYSIS: [
            ChallengeType.WEAK_INSTRUMENT,
        ],
        WeaknessDimension.RDD_ANALYSIS: [
            ChallengeType.MANIPULATION_NEAR_CUTOFF,
        ],
        WeaknessDimension.FORMAT_UNDERSTANDING: [
            ChallengeType.SELF_PLAGIARISM,
        ],
        WeaknessDimension.CROSS_SECTION_REASONING: [
            ChallengeType.INTRODUCTION_RESULTS_DISCONNECT,
            ChallengeType.ROBUSTNESS_CONTRADICTS_MAIN,
        ],
        WeaknessDimension.LITERATURE_COVERAGE: [
            ChallengeType.MISSING_KEY_CITATION,
        ],
        WeaknessDimension.EVENT_STUDY: [
            ChallengeType.PARALLEL_TREND_VIOLATION,
        ],
        WeaknessDimension.PANEL_DATA: [
            ChallengeType.HETEROSCEDASTICITY_IGNORED,
            ChallengeType.CHERRY_PICKED_SAMPLE,
        ],
    }

    def __init__(self, profile: Optional[WeaknessProfile] = None):
        self._profile = profile

    def set_profile(self, profile: WeaknessProfile) -> None:
        """更新弱点画像。"""
        self._profile = profile

    def plan_challenges(
        self,
        total_count: int = 10,
        diversity_weight: float = 0.3,
    ) -> list[tuple[WeaknessDimension, ChallengeType, DifficultyLevel]]:
        """规划挑战生成计划。

        根据弱点画像的维度分布分配挑战数量，同时保留一定比例的随机探索。

        Args:
            total_count: 总挑战数
            diversity_weight: 随机探索比例（0.3 = 30% 的挑战随机选维度）

        Returns:
            计划列表: [(维度, 挑战类型, 难度)]
        """
        if not ADVERSARIAL_TRAINING_ENABLED or not self._profile:
            return []

        plan: list[tuple[WeaknessDimension, ChallengeType, DifficultyLevel]] = []

        # 按弱点优先级分配的挑战数
        targeted_count = int(total_count * (1.0 - diversity_weight))
        exploratory_count = total_count - targeted_count

        # Targeted: 从 top weaknesses 中按优先级分配
        top_entries = self._profile.get_trainable()
        if top_entries:
            # 按优先级加权分配
            total_priority = sum(e.priority for e in top_entries) or 1.0
            for entry in top_entries:
                alloc = max(1, int(targeted_count * entry.priority / total_priority))
                challenges = self.DIMENSION_CHALLENGES.get(entry.dimension, [])
                if challenges:
                    for _ in range(alloc):
                        ct = random.choice(challenges)
                        plan.append((entry.dimension, ct, DifficultyLevel.MEDIUM))
                if len(plan) >= targeted_count:
                    break

        # 如果 targeted 不够（没有足够的弱点），用 MEDIUM 填充
        while len(plan) < targeted_count:
            dim = random.choice(list(WeaknessDimension))
            challenges = self.DIMENSION_CHALLENGES.get(dim, list(ChallengeType))
            if challenges:
                ct = random.choice(challenges)
                plan.append((dim, ct, DifficultyLevel.MEDIUM))

        # Exploratory: 随机选维度和类型，探索未知弱点
        all_dimensions = list(WeaknessDimension)
        for _ in range(exploratory_count):
            dim = random.choice(all_dimensions)
            challenges = self.DIMENSION_CHALLENGES.get(dim, list(ChallengeType))
            if challenges:
                ct = random.choice(challenges)
            else:
                ct = random.choice(list(ChallengeType))
            # 探索性挑战用较低难度，降低成本
            plan.append((dim, ct, DifficultyLevel.EASY))

        return plan[:total_count]

    def get_challenge_types_for_dimension(
        self, dimension: WeaknessDimension
    ) -> list[ChallengeType]:
        """获取适用于某个维度的所有挑战类型。"""
        return self.DIMENSION_CHALLENGES.get(dimension, list(ChallengeType))


# ==============================================================
# Prompt 模板
# ==============================================================

class PromptTemplates:
    """对抗样本生成的 Prompt 模板集合。"""

    @staticmethod
    def generate_challenge_prompt(
        challenge_type: ChallengeType,
        difficulty: DifficultyLevel,
        dimension: WeaknessDimension,
        context: str = "",
    ) -> str:
        """生成对抗样本的主 Prompt。"""
        difficulty_instructions = {
            DifficultyLevel.TRIVIAL: "错误应该明显可见，任何有基本学术素养的人都能发现。",
            DifficultyLevel.EASY: "错误应该比较明显，但需要一定的领域知识才能准确识别。",
            DifficultyLevel.MEDIUM: "错误应该隐蔽，需要仔细阅读和专业分析才能发现。表面上论文看起来合理。",
            DifficultyLevel.HARD: "错误应该非常隐蔽，被合理化的学术语言巧妙掩盖。需要深度分析和专家级判断。",
            DifficultyLevel.EXPERT: "错误应该极其隐蔽，论文整体看似完美无瑕。只有最资深的审稿人通过精密的交叉验证才可能发现微妙的不一致。",
        }

        challenge_descriptions = {
            ChallengeType.HIDDEN_ENDOGENEITY: "包含隐藏的内生性问题——表面看识别策略合理，但存在遗漏变量或反向因果",
            ChallengeType.WEAK_INSTRUMENT: "使用了弱工具变量——F统计量边缘、排除性约束可疑但措辞模糊",
            ChallengeType.PARALLEL_TREND_VIOLATION: "声称满足平行趋势假设但数据/图暗示违反",
            ChallengeType.MANIPULATION_NEAR_CUTOFF: "断点回归中存在断点附近操纵迹象",
            ChallengeType.CHERRY_PICKED_SAMPLE: "样本筛选条件看似合理但暗藏选择偏差",
            ChallengeType.MULTIPLE_TESTING: "报告大量回归结果但未做多重检验校正",
            ChallengeType.P_HACKING_SIGNAL: "核心系数恰好在 0.05 边界附近，缺乏敏感性分析",
            ChallengeType.MISINTERPRETED_SIGNIFICANCE: "混淆统计显著性和经济显著性",
            ChallengeType.HETEROSCEDASTICITY_IGNORED: "标准误类型选择不当，忽略了异方差",
            ChallengeType.TABLE_TEXT_CONTRADICTION: "表格数据与文本描述存在微妙矛盾",
            ChallengeType.FIGURE_CLAIM_MISMATCH: "图表暗示的方向与文字声明不一致",
            ChallengeType.SAMPLE_SIZE_INCONSISTENCY: "不同位置报告的样本量不一致",
            ChallengeType.CIRCULAR_REASONING: "结论已预设在假设中，但被学术语言包装得隐蔽",
            ChallengeType.NON_SEQUITUR_CONCLUSION: "结论无法由前提推出，逻辑跳跃被学术语言掩盖",
            ChallengeType.ECOLOGICAL_FALLACY: "群体层面数据被不当推断到个体行为",
            ChallengeType.MISSING_KEY_CITATION: "遗漏了该领域最重要的先驱工作",
            ChallengeType.SELF_PLAGIARISM: "大段内容与作者前作雷同但未标注",
            ChallengeType.INTRODUCTION_RESULTS_DISCONNECT: "引言承诺的贡献在结果中未兑现",
            ChallengeType.ROBUSTNESS_CONTRADICTS_MAIN: "稳健性检验实际推翻了主结论但作者轻描淡写",
        }

        challenge_desc = challenge_descriptions.get(
            challenge_type, f"包含 {challenge_type.value} 类型的隐蔽错误"
        )
        diff_inst = difficulty_instructions.get(difficulty, difficulty_instructions[DifficultyLevel.MEDIUM])

        prompt = f"""你是一位经济学学术期刊的资深主编，现在需要你生成一个用于测试 AI 审稿系统的对抗性论文片段。

## 生成要求

**缺陷类型**: {challenge_desc}
**难度要求**: {diff_inst}
**目标维度**: {dimension.value}

## 约束条件

1. 论文片段应是一段完整的学术论文节选（包含必要的 section 标题、方程/表格引用、引文格式）
2. 长度: 300-800 词的英文学术论文片段
3. 论文整体应看起来合理、专业，符合顶级经济学期刊的写作规范
4. 必须包含至少一个隐蔽错误/缺陷
5. 提供明确的正确答案（gold label）

{f"额外上下文: {context}" if context else ""}

## 输出格式（JSON）

请严格按以下 JSON 格式输出:
{{
    "paper_snippet": "生成的论文片段（英文）",
    "gold_findings": [
        {{
            "category": "methodology|statistics|logic|clarity|citation",
            "severity": "critical|major|minor",
            "description": "问题的简洁描述",
            "evidence": "指向论文中具体证据的引用",
            "suggestion": "修改建议"
        }}
    ],
    "gold_explanation": "详细解释: 为什么这是一个问题、隐蔽性在哪里、正确的分析应该是什么",
    "difficulty_justification": "解释为什么此样本符合要求的难度级别"
}}"""
        return prompt

    @staticmethod
    def generate_variant_prompt(
        original_case: AdversarialCase,
        variation_type: str = "surface",
    ) -> str:
        """生成变体的 Prompt（保持核心缺陷但改变表面特征）。

        Args:
            original_case: 原始对抗样本
            variation_type: 变体类型
                - "surface": 只改表面措辞，保持缺陷模式
                - "domain": 迁移到不同经济学子领域
                - "complexity": 增加/减少论文复杂度
                - "structure": 改变论文结构/章节安排
        """
        variation_instructions = {
            "surface": "保持相同的核心缺陷模式，但改变论文的具体主题、措辞、变量名、引用等表面特征。",
            "domain": "将相同的缺陷模式迁移到不同的经济学子领域（如从劳动经济学迁移到发展经济学）。",
            "complexity": "增加论文的复杂度（更多变量、更复杂的模型），使缺陷更难发现。",
            "structure": "改变论文的结构安排（如将关键信息分散到不同 section），增加跨章节推理难度。",
        }

        inst = variation_instructions.get(variation_type, variation_instructions["surface"])

        prompt = f"""基于以下对抗性论文片段，生成一个变体版本。

## 原始样本

**缺陷类型**: {original_case.challenge_type.value}
**原始片段**: {original_case.paper_snippet[:500]}...
**核心缺陷**: {original_case.gold_explanation[:200]}

## 变体要求

{inst}

## 关键约束

1. 核心缺陷的**模式**必须保留（即相同类型的错误）
2. 但表面特征必须足够不同，使得之前见过原始样本的系统不能简单模式匹配
3. 同样需要提供 gold label 和详细解释

## 输出格式（JSON）

{{
    "paper_snippet": "变体论文片段",
    "gold_findings": [...],
    "gold_explanation": "详细解释",
    "variation_note": "与原版的关键差异说明"
}}"""
        return prompt


# ==============================================================
# 对抗样本生成器
# ==============================================================

class AdversarialGenerator:
    """对抗性论文片段生成器。

    核心职责:
        1. 基于弱点画像选择挑战目标
        2. 调用 LLM 生成逼真的对抗样本
        3. 支持变体生成（从历史失败中创建类似挑战）
        4. 与难度控制器协同工作
        5. 质量检查生成的样本

    使用方式:
        generator = AdversarialGenerator(llm=my_llm)
        generator.set_weakness_profile(profile)

        # 生成单个挑战
        case = await generator.generate_challenge(
            weakness=top_weakness,
            difficulty=DifficultyLevel.MEDIUM,
        )

        # 批量生成
        cases = await generator.generate_batch(count=10)

        # 从失败生成变体
        variant = await generator.generate_from_failure(failure_context, original_case)
    """

    def __init__(
        self,
        llm: Optional[LLMGenerator] = None,
        difficulty_controller: Optional[DifficultyController] = None,
        challenge_factory: Optional[MultiDimensionChallengeFactory] = None,
    ):
        self._llm = llm
        self._difficulty_controller = difficulty_controller or DifficultyController()
        self._challenge_factory = challenge_factory or MultiDimensionChallengeFactory()
        self._profile: Optional[WeaknessProfile] = None
        self._generation_count: int = 0
        self._generation_history: list[str] = []  # case_ids

    @property
    def generation_count(self) -> int:
        return self._generation_count

    @property
    def difficulty_controller(self) -> DifficultyController:
        return self._difficulty_controller

    @property
    def challenge_factory(self) -> MultiDimensionChallengeFactory:
        return self._challenge_factory

    def set_weakness_profile(self, profile: WeaknessProfile) -> None:
        """设置弱点画像。"""
        self._profile = profile
        self._challenge_factory.set_profile(profile)

    def set_llm(self, llm: LLMGenerator) -> None:
        """设置 LLM 生成器。"""
        self._llm = llm

    async def generate_challenge(
        self,
        weakness: Optional[WeaknessEntry] = None,
        challenge_type: Optional[ChallengeType] = None,
        difficulty: Optional[DifficultyLevel] = None,
        context: str = "",
    ) -> AdversarialCase:
        """生成单个对抗样本。

        Args:
            weakness: 目标弱点（如果为 None，从画像中选取 top-1）
            challenge_type: 挑战类型（如果为 None，根据弱点维度选择）
            difficulty: 难度（如果为 None，使用难度控制器推荐）
            context: 额外上下文信息

        Returns:
            生成的对抗样本
        """
        if not ADVERSARIAL_TRAINING_ENABLED:
            return AdversarialCase()

        # 确定目标弱点
        if weakness is None and self._profile:
            top = self._profile.get_top_k(1)
            weakness = top[0] if top else None

        # 确定维度
        target_dim = weakness.dimension if weakness else WeaknessDimension.METHODOLOGY_ANALYSIS

        # 确定挑战类型
        if challenge_type is None:
            available = self._challenge_factory.get_challenge_types_for_dimension(target_dim)
            challenge_type = random.choice(available) if available else ChallengeType.HIDDEN_ENDOGENEITY

        # 确定难度
        if difficulty is None:
            difficulty = self._difficulty_controller.get_recommended_difficulty()

        # 生成 Prompt
        prompt = PromptTemplates.generate_challenge_prompt(
            challenge_type=challenge_type,
            difficulty=difficulty,
            dimension=target_dim,
            context=context,
        )

        # 调用 LLM 生成
        if self._llm:
            try:
                result = await self._llm.generate(prompt)
                case = self._parse_generated_result(
                    result, challenge_type, difficulty, target_dim, weakness
                )
            except Exception as e:
                logger.warning("[AdversarialGenerator] LLM generation failed: %s", e)
                case = self._create_fallback_case(challenge_type, difficulty, target_dim)
        else:
            # 无 LLM 时创建模板化案例（用于测试）
            case = self._create_fallback_case(challenge_type, difficulty, target_dim)

        # 记录
        self._generation_count += 1
        self._generation_history.append(case.case_id)
        if weakness:
            case.source_weakness_id = weakness.weakness_id

        return case

    async def generate_from_failure(
        self,
        failure_context: dict,
        original_case: Optional[AdversarialCase] = None,
        variation_type: str = "surface",
    ) -> AdversarialCase:
        """从历史失败中生成变体对抗样本。

        变体保持失败模式但改变表面特征，防止 Agent 通过记忆模式匹配而非真正理解来通过。

        Args:
            failure_context: 失败上下文（FailureContext.to_dict() 格式）
            original_case: 原始对抗样本（如果有）
            variation_type: 变体类型

        Returns:
            变体对抗样本
        """
        if not ADVERSARIAL_TRAINING_ENABLED:
            return AdversarialCase()

        if original_case and self._llm:
            prompt = PromptTemplates.generate_variant_prompt(original_case, variation_type)
            try:
                result = await self._llm.generate(prompt)
                case = self._parse_generated_result(
                    result,
                    original_case.challenge_type,
                    original_case.difficulty,
                    original_case.target_dimension,
                    None,
                )
                case.generated_from_failure = True
                case.parent_case_id = original_case.case_id
                return case
            except Exception as e:
                logger.warning("[AdversarialGenerator] Variant generation failed: %s", e)

        # Fallback: 基于失败信息创建新挑战
        failure_type = failure_context.get("failure_type", "logic_error")
        snippet = failure_context.get("paper_text_snippet", "")

        case = await self.generate_challenge(
            context=f"Similar to previous failure: {failure_type}. Context: {snippet[:200]}",
        )
        case.generated_from_failure = True
        return case

    async def generate_batch(
        self,
        count: int = 10,
        diversity_weight: float = 0.3,
    ) -> list[AdversarialCase]:
        """批量生成对抗样本。

        使用 MultiDimensionChallengeFactory 规划，然后逐个生成。

        Args:
            count: 生成数量
            diversity_weight: 随机探索比例

        Returns:
            生成的对抗样本列表
        """
        if not ADVERSARIAL_TRAINING_ENABLED:
            return []

        plan = self._challenge_factory.plan_challenges(count, diversity_weight)
        cases: list[AdversarialCase] = []

        for dim, ct, diff in plan:
            # 使用难度控制器可能覆盖计划中的难度
            actual_diff = self._difficulty_controller.get_recommended_difficulty()

            case = await self.generate_challenge(
                challenge_type=ct,
                difficulty=actual_diff,
            )
            # 覆盖维度（以计划为准）
            case.target_dimension = dim
            cases.append(case)

        return cases

    def validate_case(self, case: AdversarialCase) -> tuple[bool, list[str]]:
        """验证对抗样本的质量。

        检查:
            1. paper_snippet 非空且长度合理
            2. gold_findings 非空
            3. gold_explanation 非空
            4. challenge_type 与内容匹配

        Returns:
            (is_valid, issues_list)
        """
        issues: list[str] = []

        if not case.paper_snippet:
            issues.append("paper_snippet is empty")
        elif len(case.paper_snippet) < 50:
            issues.append("paper_snippet too short (< 50 chars)")

        if not case.gold_findings:
            issues.append("gold_findings is empty (no correct answer)")

        if not case.gold_explanation:
            issues.append("gold_explanation is empty")

        # 检查 gold_findings 格式
        for i, finding in enumerate(case.gold_findings):
            if not isinstance(finding, dict):
                issues.append(f"gold_findings[{i}] is not a dict")
                continue
            if not finding.get("category"):
                issues.append(f"gold_findings[{i}] missing 'category'")
            if not finding.get("description"):
                issues.append(f"gold_findings[{i}] missing 'description'")

        is_valid = len(issues) == 0
        if is_valid:
            case.quality_verified = True

        return is_valid, issues

    def get_stats(self) -> dict:
        """获取生成器统计信息。"""
        return {
            "total_generated": self._generation_count,
            "difficulty_level": self._difficulty_controller.current_level.value,
            "mastery_profile": self._difficulty_controller.get_mastery_profile(),
            "recent_cases": len(self._generation_history),
        }

    def serialize(self) -> dict:
        return {
            "generation_count": self._generation_count,
            "generation_history": self._generation_history[-100:],  # 保留最近 100 个
            "difficulty_controller": self._difficulty_controller.serialize(),
        }

    @classmethod
    def deserialize(cls, data: dict, llm: Optional[LLMGenerator] = None) -> "AdversarialGenerator":
        dc = DifficultyController.deserialize(data.get("difficulty_controller", {}))
        gen = cls(llm=llm, difficulty_controller=dc)
        gen._generation_count = data.get("generation_count", 0)
        gen._generation_history = data.get("generation_history", [])
        return gen

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _parse_generated_result(
        self,
        result: dict,
        challenge_type: ChallengeType,
        difficulty: DifficultyLevel,
        target_dim: WeaknessDimension,
        weakness: Optional[WeaknessEntry],
    ) -> AdversarialCase:
        """解析 LLM 生成的结果为 AdversarialCase。"""
        return AdversarialCase(
            paper_snippet=result.get("paper_snippet", ""),
            gold_findings=result.get("gold_findings", []),
            gold_explanation=result.get("gold_explanation", ""),
            challenge_type=challenge_type,
            difficulty=difficulty,
            target_dimension=target_dim,
            source_weakness_id=weakness.weakness_id if weakness else "",
        )

    def _create_fallback_case(
        self,
        challenge_type: ChallengeType,
        difficulty: DifficultyLevel,
        target_dim: WeaknessDimension,
    ) -> AdversarialCase:
        """创建模板化的对抗样本（当 LLM 不可用时的 fallback）。

        注：这些模板主要用于测试，真实训练应使用 LLM 生成。
        """
        templates = {
            ChallengeType.HIDDEN_ENDOGENEITY: {
                "paper_snippet": (
                    "## 4. Empirical Strategy\n\n"
                    "We estimate the effect of minimum wage increases on employment using "
                    "a difference-in-differences framework. Our treatment group consists of "
                    "counties that experienced a minimum wage increase in 2015, while control "
                    "counties maintained unchanged rates. We control for county-level fixed "
                    "effects, year fixed effects, and time-varying demographic characteristics.\n\n"
                    "The identifying assumption is that, absent the minimum wage change, "
                    "employment trends would have evolved similarly in treatment and control "
                    "counties. We provide evidence supporting this assumption in Figure 2."
                ),
                "gold_findings": [{
                    "category": "methodology",
                    "severity": "major",
                    "description": "Potential endogeneity: counties choosing to raise minimum wage may systematically differ in economic trajectory",
                    "evidence": "Treatment assignment is not random — counties that raise minimum wage likely face different economic conditions",
                    "suggestion": "Address selection into treatment; consider policy-induced variation or border-county designs",
                }],
                "gold_explanation": (
                    "The DID setup has a hidden endogeneity problem: counties that choose "
                    "to raise minimum wage are not randomly assigned. Their economic conditions "
                    "(growth trajectory, political climate, labor market tightness) likely differ "
                    "systematically from non-raising counties. This is a selection-into-treatment "
                    "problem that standard DID fixed effects cannot fully address."
                ),
            },
            ChallengeType.TABLE_TEXT_CONTRADICTION: {
                "paper_snippet": (
                    "## 5. Results\n\n"
                    "Table 3 presents our main regression results. The coefficient on the "
                    "treatment variable is 0.045 (s.e. = 0.018), statistically significant at "
                    "the 5% level. This represents a 4.5 percentage point increase in labor "
                    "force participation following the policy change.\n\n"
                    "| Variable | (1) | (2) | (3) |\n"
                    "|----------|-----|-----|-----|\n"
                    "| Treatment | 0.032** | 0.038** | 0.045** |\n"
                    "| | (0.014) | (0.016) | (0.019) |\n"
                    "| N | 12,450 | 11,890 | 10,234 |\n"
                ),
                "gold_findings": [{
                    "category": "statistics",
                    "severity": "minor",
                    "description": "Standard error inconsistency between text and table",
                    "evidence": "Text states s.e. = 0.018 for Column (3), but table shows (0.019)",
                    "suggestion": "Verify the correct standard error and ensure consistency between text and table",
                }],
                "gold_explanation": (
                    "There is a subtle inconsistency: the text reports s.e. = 0.018 for the "
                    "main coefficient, but Table 3 Column (3) shows the standard error as 0.019. "
                    "While the difference is small, it indicates either a transcription error or "
                    "that the text was not updated after a re-estimation."
                ),
            },
        }

        template = templates.get(challenge_type, {
            "paper_snippet": f"[Template for {challenge_type.value} — LLM not available]",
            "gold_findings": [{"category": "methodology", "severity": "major", "description": f"Template {challenge_type.value} finding"}],
            "gold_explanation": f"Template explanation for {challenge_type.value}",
        })

        return AdversarialCase(
            paper_snippet=template["paper_snippet"],
            gold_findings=template["gold_findings"],
            gold_explanation=template["gold_explanation"],
            challenge_type=challenge_type,
            difficulty=difficulty,
            target_dimension=target_dim,
        )
