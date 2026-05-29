"""
training/curriculum.py — 课程学习系统 (Curriculum Learning)

自动设计从易到难的训练课程，最大化学习效率。

核心思路（来自 Ideal 层设计）：
    - 不是随机生成对抗样本，而是有策略地安排训练顺序
    - 从简单对抗样本开始，逐步提升难度，确保学习曲线平滑
    - 追踪学习进展，动态调整课程内容
    - 维度轮转：确保各弱点维度都得到训练，不偏科

核心组件:
    1. DifficultyGradient — 难度梯度定义（每个维度的难度阶梯）
    2. CurriculumStage — 课程阶段（一组同难度/同维度的训练任务）
    3. TrainingCurriculum — 完整的训练课程（阶段序列 + 进度追踪）
    4. LearningCurveTracker — 学习曲线追踪器（记录各维度的进步轨迹）
    5. CurriculumDesigner — 课程设计器（从弱点画像自动生成课程）

设计原则:
    - 自适应: 根据 Agent 的实际表现动态调整后续课程
    - 防遗忘: 定期安排已掌握维度的回顾练习（间隔重复）
    - 效率优先: 优先训练 ROI 最高的弱点（高优先级 + 低训练次数）
    - 可暂停/恢复: 课程状态完全可序列化

Kill Switch: SCHOLAR_GODEL_ADVERSARIAL_TRAINING
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from training.weakness_analyzer import WeaknessDimension, WeaknessProfile, WeaknessEntry
from training.adversarial import (
    AdversarialCase,
    ChallengeType,
    DifficultyLevel,
    MultiDimensionChallengeFactory,
)

from core.godel_config import GODEL_ADVERSARIAL_TRAINING_ENABLED

logger = logging.getLogger(__name__)


# ==============================================================
# Kill Switch (delegate to godel_config — single source of truth)
# ==============================================================

ADVERSARIAL_TRAINING_ENABLED: bool = GODEL_ADVERSARIAL_TRAINING_ENABLED
"""Backward-compatible alias. Actual source of truth is core.godel_config."""


# ==============================================================
# 难度梯度
# ==============================================================

@dataclass
class DifficultyGradient:
    """难度梯度定义——某个维度上从易到难的阶梯。

    每个阶梯定义:
        - 难度等级
        - 该难度下推荐的挑战类型
        - 达到此阶梯所需的前置通过率
    """
    dimension: WeaknessDimension
    steps: list["GradientStep"] = field(default_factory=list)

    def get_current_step(self, pass_rates: dict[str, float]) -> "GradientStep":
        """根据当前通过率确定应该处于哪个阶梯。"""
        for step in reversed(self.steps):
            # 找到最高的已达到的阶梯
            prereq_met = True
            for level_val, required_rate in step.prerequisites.items():
                actual_rate = pass_rates.get(level_val, 0.0)
                if actual_rate < required_rate:
                    prereq_met = False
                    break
            if prereq_met:
                return step

        # 默认返回第一步
        return self.steps[0] if self.steps else GradientStep(
            difficulty=DifficultyLevel.TRIVIAL,
            challenge_types=[],
            prerequisites={},
        )

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension.value,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DifficultyGradient":
        return cls(
            dimension=WeaknessDimension(data["dimension"]),
            steps=[GradientStep.from_dict(s) for s in data.get("steps", [])],
        )


@dataclass
class GradientStep:
    """难度梯度中的一步。"""
    difficulty: DifficultyLevel
    challenge_types: list[ChallengeType] = field(default_factory=list)
    prerequisites: dict[str, float] = field(default_factory=dict)
    """前置条件: {难度等级值: 所需通过率}。例如 {"easy": 0.7} 表示 EASY 级需达 70%。"""

    description: str = ""
    target_pass_rate: float = 0.7
    """此阶梯的目标通过率。达到后可进入下一阶梯。"""

    min_attempts: int = 3
    """此阶梯最少尝试次数（防止偶然通过就跳级）。"""

    def to_dict(self) -> dict:
        return {
            "difficulty": self.difficulty.value,
            "challenge_types": [ct.value for ct in self.challenge_types],
            "prerequisites": self.prerequisites,
            "description": self.description,
            "target_pass_rate": self.target_pass_rate,
            "min_attempts": self.min_attempts,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GradientStep":
        return cls(
            difficulty=DifficultyLevel(data.get("difficulty", "medium")),
            challenge_types=[ChallengeType(ct) for ct in data.get("challenge_types", [])],
            prerequisites=data.get("prerequisites", {}),
            description=data.get("description", ""),
            target_pass_rate=data.get("target_pass_rate", 0.7),
            min_attempts=data.get("min_attempts", 3),
        )


# ==============================================================
# 课程阶段
# ==============================================================

class StageStatus(str, Enum):
    """阶段状态。"""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"  # 超过最大尝试次数仍未达标
    SKIPPED = "skipped"


@dataclass
class StageResult:
    """阶段内单次训练结果。"""
    case_id: str
    passed: bool
    score: float = 0.0
    timestamp: float = field(default_factory=time.time)
    details: dict = field(default_factory=dict)


@dataclass
class CurriculumStage:
    """课程的一个阶段。

    一个阶段包含同一维度、同一难度的一组训练任务。
    通过条件: pass_rate >= target_pass_rate 且 attempts >= min_attempts。
    """
    stage_id: str = ""
    dimension: WeaknessDimension = WeaknessDimension.METHODOLOGY_ANALYSIS
    difficulty: DifficultyLevel = DifficultyLevel.EASY
    challenge_types: list[ChallengeType] = field(default_factory=list)

    # 目标与约束
    target_pass_rate: float = 0.7
    min_attempts: int = 3
    max_attempts: int = 20

    # 进度
    status: StageStatus = StageStatus.NOT_STARTED
    results: list[StageResult] = field(default_factory=list)
    started_at: float = 0.0
    completed_at: float = 0.0

    # 排序
    order: int = 0
    """在课程中的排列顺序。"""

    @property
    def attempts(self) -> int:
        return len(self.results)

    @property
    def passes(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return self.passes / len(self.results)

    @property
    def is_passed(self) -> bool:
        return (
            self.pass_rate >= self.target_pass_rate
            and self.attempts >= self.min_attempts
        )

    @property
    def is_exhausted(self) -> bool:
        """是否已用完最大尝试次数。"""
        return self.attempts >= self.max_attempts

    @property
    def should_advance(self) -> bool:
        """是否应该推进到下一阶段。"""
        return self.is_passed or self.is_exhausted

    def record_result(self, result: StageResult) -> None:
        """记录训练结果。"""
        if self.status == StageStatus.NOT_STARTED:
            self.status = StageStatus.IN_PROGRESS
            self.started_at = time.time()

        self.results.append(result)

        # 检查是否完成
        if self.is_passed:
            self.status = StageStatus.PASSED
            self.completed_at = time.time()
        elif self.is_exhausted:
            self.status = StageStatus.FAILED
            self.completed_at = time.time()

    def recent_pass_rate(self, window: int = 5) -> float:
        """最近 N 次的通过率（用于趋势判断）。"""
        recent = self.results[-window:] if self.results else []
        if not recent:
            return 0.0
        return sum(1 for r in recent if r.passed) / len(recent)

    def to_dict(self) -> dict:
        return {
            "stage_id": self.stage_id,
            "dimension": self.dimension.value,
            "difficulty": self.difficulty.value,
            "challenge_types": [ct.value for ct in self.challenge_types],
            "target_pass_rate": self.target_pass_rate,
            "min_attempts": self.min_attempts,
            "max_attempts": self.max_attempts,
            "status": self.status.value,
            "results": [
                {"case_id": r.case_id, "passed": r.passed, "score": r.score, "timestamp": r.timestamp}
                for r in self.results
            ],
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "order": self.order,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CurriculumStage":
        results = [
            StageResult(
                case_id=r.get("case_id", ""),
                passed=r.get("passed", False),
                score=r.get("score", 0.0),
                timestamp=r.get("timestamp", 0.0),
            )
            for r in data.get("results", [])
        ]
        return cls(
            stage_id=data.get("stage_id", ""),
            dimension=WeaknessDimension(data.get("dimension", "methodology_analysis")),
            difficulty=DifficultyLevel(data.get("difficulty", "easy")),
            challenge_types=[ChallengeType(ct) for ct in data.get("challenge_types", [])],
            target_pass_rate=data.get("target_pass_rate", 0.7),
            min_attempts=data.get("min_attempts", 3),
            max_attempts=data.get("max_attempts", 20),
            status=StageStatus(data.get("status", "not_started")),
            results=results,
            started_at=data.get("started_at", 0.0),
            completed_at=data.get("completed_at", 0.0),
            order=data.get("order", 0),
        )


# ==============================================================
# 训练课程
# ==============================================================

@dataclass
class TrainingCurriculum:
    """完整的训练课程——一个有序的阶段序列。"""
    curriculum_id: str = ""
    name: str = ""
    description: str = ""
    stages: list[CurriculumStage] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # 课程配置
    review_interval: int = 5
    """每隔多少个新阶段安排一次回顾练习。"""

    max_stages: int = 100
    """最大阶段数（防止无限扩展）。"""

    @property
    def current_stage(self) -> Optional[CurriculumStage]:
        """获取当前正在进行的阶段。"""
        for stage in self.stages:
            if stage.status in (StageStatus.NOT_STARTED, StageStatus.IN_PROGRESS):
                return stage
        return None

    @property
    def progress(self) -> float:
        """整体进度 (0.0~1.0)。"""
        if not self.stages:
            return 0.0
        completed = sum(1 for s in self.stages if s.status in (StageStatus.PASSED, StageStatus.SKIPPED))
        return completed / len(self.stages)

    @property
    def total_attempts(self) -> int:
        return sum(s.attempts for s in self.stages)

    @property
    def overall_pass_rate(self) -> float:
        total_passes = sum(s.passes for s in self.stages)
        total_attempts = self.total_attempts
        if total_attempts == 0:
            return 0.0
        return total_passes / total_attempts

    def get_completed_stages(self) -> list[CurriculumStage]:
        return [s for s in self.stages if s.status == StageStatus.PASSED]

    def get_failed_stages(self) -> list[CurriculumStage]:
        return [s for s in self.stages if s.status == StageStatus.FAILED]

    def advance_to_next(self) -> Optional[CurriculumStage]:
        """推进到下一个阶段。返回新的当前阶段。"""
        current = self.current_stage
        if current and current.should_advance:
            # 当前阶段已完成（通过或用尽），推进
            pass
        return self.current_stage

    def add_stage(self, stage: CurriculumStage) -> None:
        """添加新阶段。"""
        if len(self.stages) >= self.max_stages:
            logger.warning("[Curriculum] Max stages reached, not adding more")
            return
        stage.order = len(self.stages)
        self.stages.append(stage)
        self.updated_at = time.time()

    def insert_review_stage(self, dimension: WeaknessDimension) -> None:
        """插入回顾练习阶段（间隔重复）。"""
        review_stage = CurriculumStage(
            stage_id=f"review_{dimension.value}_{int(time.time())}",
            dimension=dimension,
            difficulty=DifficultyLevel.MEDIUM,  # 回顾用中等难度
            target_pass_rate=0.8,  # 回顾需要更高通过率
            min_attempts=2,
            max_attempts=5,
        )
        self.add_stage(review_stage)

    def get_dimension_coverage(self) -> dict[WeaknessDimension, int]:
        """各维度被覆盖的阶段数。"""
        coverage: dict[WeaknessDimension, int] = defaultdict(int)
        for stage in self.stages:
            coverage[stage.dimension] += 1
        return dict(coverage)

    def to_dict(self) -> dict:
        return {
            "curriculum_id": self.curriculum_id,
            "name": self.name,
            "description": self.description,
            "stages": [s.to_dict() for s in self.stages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "review_interval": self.review_interval,
            "max_stages": self.max_stages,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrainingCurriculum":
        return cls(
            curriculum_id=data.get("curriculum_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            stages=[CurriculumStage.from_dict(s) for s in data.get("stages", [])],
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            review_interval=data.get("review_interval", 5),
            max_stages=data.get("max_stages", 100),
        )


# ==============================================================
# 学习曲线追踪器
# ==============================================================

@dataclass
class LearningPoint:
    """学习曲线上的一个数据点。"""
    timestamp: float
    dimension: WeaknessDimension
    difficulty: DifficultyLevel
    pass_rate: float
    cumulative_attempts: int


class LearningCurveTracker:
    """学习曲线追踪器。

    记录各维度在不同难度级别上的进步轨迹，支持:
        - 进步速度计算（学习速率）
        - 停滞检测（学习瓶颈）
        - 遗忘检测（掌握后退步）
        - 总体学习效率评估
    """

    def __init__(self):
        self._curves: dict[str, list[LearningPoint]] = defaultdict(list)
        """key = f"{dimension.value}:{difficulty.value}" """

        self._dimension_mastery: dict[str, float] = {}
        """各维度的掌握度。"""

    def record(
        self,
        dimension: WeaknessDimension,
        difficulty: DifficultyLevel,
        pass_rate: float,
        cumulative_attempts: int,
    ) -> None:
        """记录一个学习数据点。"""
        key = f"{dimension.value}:{difficulty.value}"
        point = LearningPoint(
            timestamp=time.time(),
            dimension=dimension,
            difficulty=difficulty,
            pass_rate=pass_rate,
            cumulative_attempts=cumulative_attempts,
        )
        self._curves[key].append(point)

        # 更新维度掌握度
        self._update_mastery(dimension)

    def get_curve(
        self,
        dimension: WeaknessDimension,
        difficulty: Optional[DifficultyLevel] = None,
    ) -> list[LearningPoint]:
        """获取某维度（某难度）的学习曲线。"""
        if difficulty:
            key = f"{dimension.value}:{difficulty.value}"
            return self._curves.get(key, [])
        else:
            # 返回该维度所有难度的数据点
            prefix = f"{dimension.value}:"
            points: list[LearningPoint] = []
            for key, curve in self._curves.items():
                if key.startswith(prefix):
                    points.extend(curve)
            return sorted(points, key=lambda p: p.timestamp)

    def get_learning_rate(
        self,
        dimension: WeaknessDimension,
        difficulty: DifficultyLevel,
        window: int = 5,
    ) -> float:
        """计算学习速率（最近窗口内 pass_rate 的增长斜率）。

        Returns:
            正值 = 进步，负值 = 退步，0 = 停滞。
            单位: pass_rate 变化/数据点
        """
        curve = self.get_curve(dimension, difficulty)
        if len(curve) < 2:
            return 0.0

        recent = curve[-window:]
        if len(recent) < 2:
            return 0.0

        # 线性回归斜率
        n = len(recent)
        x = list(range(n))
        y = [p.pass_rate for p in recent]

        x_mean = sum(x) / n
        y_mean = sum(y) / n

        numerator = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
        denominator = sum((xi - x_mean) ** 2 for xi in x)

        if denominator == 0:
            return 0.0

        return numerator / denominator

    def detect_plateau(
        self,
        dimension: WeaknessDimension,
        difficulty: DifficultyLevel,
        threshold: float = 0.02,
        min_points: int = 5,
    ) -> bool:
        """检测学习是否停滞（plateau）。

        如果最近 min_points 个数据点的学习速率 < threshold，判定为停滞。
        """
        rate = self.get_learning_rate(dimension, difficulty, window=min_points)
        return abs(rate) < threshold and len(self.get_curve(dimension, difficulty)) >= min_points

    def detect_forgetting(
        self,
        dimension: WeaknessDimension,
        difficulty: DifficultyLevel,
        decline_threshold: float = -0.1,
    ) -> bool:
        """检测是否出现遗忘（pass_rate 显著下降）。"""
        rate = self.get_learning_rate(dimension, difficulty)
        return rate < decline_threshold

    def get_mastery(self, dimension: WeaknessDimension) -> float:
        """获取某维度的综合掌握度 (0.0~1.0)。"""
        return self._dimension_mastery.get(dimension.value, 0.0)

    def get_all_mastery(self) -> dict[str, float]:
        """获取所有维度的掌握度。"""
        return dict(self._dimension_mastery)

    def get_weakest_dimensions(self, k: int = 3) -> list[tuple[WeaknessDimension, float]]:
        """获取掌握度最低的 K 个维度。"""
        all_dims = [
            (WeaknessDimension(dim), mastery)
            for dim, mastery in self._dimension_mastery.items()
        ]
        all_dims.sort(key=lambda x: x[1])
        return all_dims[:k]

    def get_improvement_since(self, since_timestamp: float) -> dict[str, float]:
        """计算自某时间点以来各维度的改善幅度。"""
        improvements: dict[str, float] = {}

        for key, curve in self._curves.items():
            if not curve:
                continue

            before = [p for p in curve if p.timestamp <= since_timestamp]
            after = [p for p in curve if p.timestamp > since_timestamp]

            if before and after:
                before_avg = sum(p.pass_rate for p in before[-3:]) / min(3, len(before))
                after_avg = sum(p.pass_rate for p in after[-3:]) / min(3, len(after))
                improvements[key] = after_avg - before_avg

        return improvements

    def compute_overall_efficiency(self) -> float:
        """计算总体学习效率。

        效率 = 平均学习速率 / 平均尝试次数（归一化到 0~1）。
        """
        if not self._curves:
            return 0.0

        total_rate = 0.0
        count = 0
        for key, curve in self._curves.items():
            if len(curve) >= 2:
                dim_str, diff_str = key.split(":")
                rate = self.get_learning_rate(
                    WeaknessDimension(dim_str),
                    DifficultyLevel(diff_str),
                )
                total_rate += max(0, rate)  # 只算正向学习
                count += 1

        if count == 0:
            return 0.0

        avg_rate = total_rate / count
        # 归一化：0.1/point 的学习速率 → 1.0 效率
        return min(1.0, avg_rate / 0.1)

    def _update_mastery(self, dimension: WeaknessDimension) -> None:
        """更新维度掌握度。

        掌握度 = 各难度级别最新 pass_rate 的加权平均。
        权重: TRIVIAL=0.1, EASY=0.15, MEDIUM=0.25, HARD=0.3, EXPERT=0.2
        """
        weights = {
            DifficultyLevel.TRIVIAL.value: 0.1,
            DifficultyLevel.EASY.value: 0.15,
            DifficultyLevel.MEDIUM.value: 0.25,
            DifficultyLevel.HARD.value: 0.3,
            DifficultyLevel.EXPERT.value: 0.2,
        }

        total_weight = 0.0
        total_score = 0.0

        for diff in DifficultyLevel:
            curve = self.get_curve(dimension, diff)
            if curve:
                latest_rate = curve[-1].pass_rate
                w = weights.get(diff.value, 0.2)
                total_score += latest_rate * w
                total_weight += w

        if total_weight > 0:
            self._dimension_mastery[dimension.value] = total_score / total_weight
        else:
            self._dimension_mastery[dimension.value] = 0.0

    def serialize(self) -> dict:
        curves_data: dict[str, list[dict]] = {}
        for key, points in self._curves.items():
            curves_data[key] = [
                {
                    "timestamp": p.timestamp,
                    "dimension": p.dimension.value,
                    "difficulty": p.difficulty.value,
                    "pass_rate": p.pass_rate,
                    "cumulative_attempts": p.cumulative_attempts,
                }
                for p in points[-200:]  # 每条曲线保留最近 200 个点
            ]

        return {
            "curves": curves_data,
            "dimension_mastery": self._dimension_mastery,
        }

    @classmethod
    def deserialize(cls, data: dict) -> "LearningCurveTracker":
        tracker = cls()
        tracker._dimension_mastery = data.get("dimension_mastery", {})

        for key, points_data in data.get("curves", {}).items():
            for pd in points_data:
                point = LearningPoint(
                    timestamp=pd.get("timestamp", 0.0),
                    dimension=WeaknessDimension(pd.get("dimension", "methodology_analysis")),
                    difficulty=DifficultyLevel(pd.get("difficulty", "medium")),
                    pass_rate=pd.get("pass_rate", 0.0),
                    cumulative_attempts=pd.get("cumulative_attempts", 0),
                )
                tracker._curves[key].append(point)

        return tracker


# ==============================================================
# 课程设计器
# ==============================================================

class CurriculumDesigner:
    """课程设计器——从弱点画像自动生成训练课程。

    设计策略:
        1. 提取 Top-K 弱点维度
        2. 为每个维度设计难度梯度
        3. 交错排列各维度的阶段（维度轮转，防止疲劳）
        4. 穿插回顾练习（间隔重复）
        5. 根据学习曲线动态调整后续阶段

    使用方式:
        designer = CurriculumDesigner(profile, tracker)
        curriculum = designer.design_curriculum(max_stages=30)
        
        # 动态调整
        designer.adapt_curriculum(curriculum)
    """

    # 默认难度梯度模板
    DEFAULT_GRADIENT_STEPS: list[tuple[DifficultyLevel, float, int]] = [
        # (难度, 前置要求通过率, 最少尝试)
        (DifficultyLevel.TRIVIAL, 0.0, 2),
        (DifficultyLevel.EASY, 0.7, 3),
        (DifficultyLevel.MEDIUM, 0.6, 4),
        (DifficultyLevel.HARD, 0.5, 5),
        (DifficultyLevel.EXPERT, 0.4, 5),
    ]

    def __init__(
        self,
        profile: Optional[WeaknessProfile] = None,
        tracker: Optional[LearningCurveTracker] = None,
        max_dimensions: int = 5,
        review_interval: int = 5,
    ):
        self._profile = profile
        self._tracker = tracker or LearningCurveTracker()
        self._max_dimensions = max_dimensions
        self._review_interval = review_interval
        self._challenge_factory = MultiDimensionChallengeFactory(profile)

    def set_profile(self, profile: WeaknessProfile) -> None:
        """更新弱点画像。"""
        self._profile = profile
        self._challenge_factory.set_profile(profile)

    @property
    def tracker(self) -> LearningCurveTracker:
        return self._tracker

    def design_curriculum(
        self,
        max_stages: int = 30,
        skip_mastered: bool = True,
    ) -> TrainingCurriculum:
        """设计完整的训练课程。

        Args:
            max_stages: 最大阶段数
            skip_mastered: 是否跳过已掌握的维度（mastery > 0.8）

        Returns:
            设计好的训练课程
        """
        if not ADVERSARIAL_TRAINING_ENABLED or not self._profile:
            return TrainingCurriculum(name="empty", description="Kill switch off or no profile")

        # 1. 选取目标维度
        target_dims = self._select_target_dimensions(skip_mastered)
        if not target_dims:
            return TrainingCurriculum(
                name="no_targets",
                description="No trainable weaknesses found",
            )

        # 2. 为每个维度生成难度梯度
        gradients = self._build_gradients(target_dims)

        # 3. 交错排列阶段
        stages = self._interleave_stages(gradients, max_stages)

        # 4. 插入回顾阶段
        stages = self._insert_reviews(stages)

        # 5. 构建课程
        curriculum = TrainingCurriculum(
            curriculum_id=f"curriculum_{int(time.time())}",
            name=f"Auto-designed for {len(target_dims)} dimensions",
            description=f"Target dimensions: {', '.join(d.value for d in target_dims)}",
            stages=stages,
            review_interval=self._review_interval,
            max_stages=max_stages,
        )

        logger.info(
            "[CurriculumDesigner] Designed curriculum: %d stages, %d dimensions",
            len(stages), len(target_dims),
        )

        return curriculum

    def adapt_curriculum(self, curriculum: TrainingCurriculum) -> list[CurriculumStage]:
        """根据学习进展动态调整课程。

        检测:
            - 停滞的维度 → 插入变体练习或降低难度
            - 快速掌握的维度 → 跳过低难度阶段
            - 遗忘的维度 → 插入回顾

        Returns:
            新增的调整阶段列表
        """
        if not ADVERSARIAL_TRAINING_ENABLED:
            return []

        adjustments: list[CurriculumStage] = []
        completed_stages = curriculum.get_completed_stages()

        for stage in completed_stages:
            # 检查停滞
            if self._tracker.detect_plateau(stage.dimension, stage.difficulty):
                # 停滞 → 插入更简单的变体练习
                recovery = CurriculumStage(
                    stage_id=f"recovery_{stage.dimension.value}_{int(time.time())}",
                    dimension=stage.dimension,
                    difficulty=self._get_easier_level(stage.difficulty),
                    target_pass_rate=0.8,
                    min_attempts=2,
                    max_attempts=5,
                )
                adjustments.append(recovery)

            # 检查遗忘
            if self._tracker.detect_forgetting(stage.dimension, stage.difficulty):
                # 遗忘 → 插入回顾
                review = CurriculumStage(
                    stage_id=f"anti_forget_{stage.dimension.value}_{int(time.time())}",
                    dimension=stage.dimension,
                    difficulty=stage.difficulty,
                    target_pass_rate=0.7,
                    min_attempts=3,
                    max_attempts=8,
                )
                adjustments.append(review)

        # 添加调整阶段到课程末尾
        for adj in adjustments:
            curriculum.add_stage(adj)

        return adjustments

    def recommend_next_focus(self) -> Optional[tuple[WeaknessDimension, DifficultyLevel]]:
        """基于学习曲线推荐下一个训练焦点。"""
        if not self._profile:
            return None

        # 找到 ROI 最高的维度：高优先级 × 低掌握度 × 高学习速率
        candidates: list[tuple[WeaknessDimension, float]] = []

        for entry in self._profile.get_trainable():
            dim = entry.dimension
            mastery = self._tracker.get_mastery(dim)
            priority = entry.priority

            # ROI = priority × (1 - mastery)
            roi = priority * (1.0 - mastery)
            candidates.append((dim, roi))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[1], reverse=True)
        best_dim = candidates[0][0]

        # 确定该维度的适当难度
        mastery = self._tracker.get_mastery(best_dim)
        if mastery < 0.3:
            diff = DifficultyLevel.EASY
        elif mastery < 0.5:
            diff = DifficultyLevel.MEDIUM
        elif mastery < 0.7:
            diff = DifficultyLevel.HARD
        else:
            diff = DifficultyLevel.EXPERT

        return (best_dim, diff)

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _select_target_dimensions(self, skip_mastered: bool) -> list[WeaknessDimension]:
        """选取目标维度。"""
        if not self._profile:
            return []

        trainable = self._profile.get_trainable()
        dims: list[WeaknessDimension] = []

        for entry in trainable:
            if skip_mastered:
                mastery = self._tracker.get_mastery(entry.dimension)
                if mastery > 0.8:
                    continue
            if entry.dimension not in dims:
                dims.append(entry.dimension)
            if len(dims) >= self._max_dimensions:
                break

        return dims

    def _build_gradients(
        self, dimensions: list[WeaknessDimension]
    ) -> dict[WeaknessDimension, list[GradientStep]]:
        """为每个维度构建难度梯度。"""
        gradients: dict[WeaknessDimension, list[GradientStep]] = {}

        for dim in dimensions:
            steps: list[GradientStep] = []
            challenges = self._challenge_factory.get_challenge_types_for_dimension(dim)

            for i, (diff, prereq_rate, min_att) in enumerate(self.DEFAULT_GRADIENT_STEPS):
                prerequisites = {}
                if i > 0:
                    prev_diff = self.DEFAULT_GRADIENT_STEPS[i - 1][0]
                    prerequisites[prev_diff.value] = prereq_rate

                step = GradientStep(
                    difficulty=diff,
                    challenge_types=challenges,
                    prerequisites=prerequisites,
                    target_pass_rate=0.7,
                    min_attempts=min_att,
                )
                steps.append(step)

            gradients[dim] = steps

        return gradients

    def _interleave_stages(
        self,
        gradients: dict[WeaknessDimension, list[GradientStep]],
        max_stages: int,
    ) -> list[CurriculumStage]:
        """交错排列各维度的阶段（Round-Robin）。"""
        stages: list[CurriculumStage] = []
        dims = list(gradients.keys())
        step_indices = {dim: 0 for dim in dims}

        order = 0
        while len(stages) < max_stages:
            added_any = False
            for dim in dims:
                idx = step_indices[dim]
                steps = gradients[dim]
                if idx >= len(steps):
                    continue

                step = steps[idx]
                stage = CurriculumStage(
                    stage_id=f"stage_{dim.value}_{step.difficulty.value}_{order}",
                    dimension=dim,
                    difficulty=step.difficulty,
                    challenge_types=step.challenge_types,
                    target_pass_rate=step.target_pass_rate,
                    min_attempts=step.min_attempts,
                    max_attempts=step.min_attempts * 4,
                    order=order,
                )
                stages.append(stage)
                step_indices[dim] = idx + 1
                order += 1
                added_any = True

                if len(stages) >= max_stages:
                    break

            if not added_any:
                break  # 所有维度的梯度都已遍历完

        return stages

    def _insert_reviews(self, stages: list[CurriculumStage]) -> list[CurriculumStage]:
        """在阶段序列中插入回顾练习。"""
        if not stages:
            return stages

        result: list[CurriculumStage] = []
        seen_dimensions: set[WeaknessDimension] = set()
        counter = 0

        for stage in stages:
            result.append(stage)
            seen_dimensions.add(stage.dimension)
            counter += 1

            if counter % self._review_interval == 0 and seen_dimensions:
                # 选一个之前练过的维度做回顾
                review_dim = list(seen_dimensions)[counter % len(seen_dimensions)]
                review_stage = CurriculumStage(
                    stage_id=f"review_{review_dim.value}_{counter}",
                    dimension=review_dim,
                    difficulty=DifficultyLevel.MEDIUM,
                    target_pass_rate=0.8,
                    min_attempts=2,
                    max_attempts=5,
                    order=len(result),
                )
                result.append(review_stage)

        return result

    def _get_easier_level(self, current: DifficultyLevel) -> DifficultyLevel:
        """获取比当前更容易一级的难度。"""
        order = [
            DifficultyLevel.TRIVIAL,
            DifficultyLevel.EASY,
            DifficultyLevel.MEDIUM,
            DifficultyLevel.HARD,
            DifficultyLevel.EXPERT,
        ]
        idx = order.index(current)
        return order[max(0, idx - 1)]

    def serialize(self) -> dict:
        return {
            "tracker": self._tracker.serialize(),
            "max_dimensions": self._max_dimensions,
            "review_interval": self._review_interval,
        }

    @classmethod
    def deserialize(cls, data: dict, profile: Optional[WeaknessProfile] = None) -> "CurriculumDesigner":
        tracker = LearningCurveTracker.deserialize(data.get("tracker", {}))
        return cls(
            profile=profile,
            tracker=tracker,
            max_dimensions=data.get("max_dimensions", 5),
            review_interval=data.get("review_interval", 5),
        )
