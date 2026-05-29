"""
training/training_loop.py — 自我训练循环引擎 (Self-Training Loop Engine)

对抗式自我训练的核心协调器：编排"弱点分析 → 样本生成 → 课程执行 → 效果评估"
的完整闭环，并追踪训练收敛状态，在适当时机终止训练。

核心组件:
    1. TrainingConfig — 训练超参数配置（回合数、batch 大小、收敛阈值等）
    2. TrainingSession — 训练会话状态（可暂停/恢复的单次训练实例）
    3. TrainingResult — 训练结果摘要（各维度改善幅度、收敛状态、统计数据）
    4. ConvergenceDetector — 收敛检测器（多信号融合判定训练是否应该停止）
    5. TrainingLoop — 核心编排器（驱动整个训练流程 + 事件发布）

设计原则:
    - 可观测性: 每个关键步骤发布 EventBus 事件，外部可监听进展
    - 容错性: 单个 case 失败不中断整体训练，错误隔离并记录
    - 可暂停/恢复: TrainingSession 完全可序列化，支持断点续训
    - 自适应: 根据学习曲线动态调整训练策略（加速/降速/换方向）
    - 预算约束: 支持 token/时间/轮次硬上限，防止无限训练
    - 集成完整: 与 Phase 4 (Failure → SkillSynthesis)、Phase 6 (Reflection)、
      Phase 8 (DualLoop outer observation) 无缝衔接

Kill Switch: SCHOLAR_GODEL_ADVERSARIAL_TRAINING
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from training.weakness_analyzer import (
    WeaknessAnalyzer,
    WeaknessDimension,
    WeaknessProfile,
)
from training.adversarial import (
    AdversarialCase,
    AdversarialGenerator,
    ChallengeType,
    DifficultyLevel,
)
from training.curriculum import (
    CurriculumDesigner,
    CurriculumStage,
    LearningCurveTracker,
    StageResult,
    StageStatus,
    TrainingCurriculum,
)

from core.godel_config import GODEL_ADVERSARIAL_TRAINING_ENABLED

logger = logging.getLogger(__name__)


# ==============================================================
# Kill Switch (delegate to godel_config — single source of truth)
# ==============================================================

ADVERSARIAL_TRAINING_ENABLED: bool = GODEL_ADVERSARIAL_TRAINING_ENABLED
"""Backward-compatible alias. Actual source of truth is core.godel_config."""


# ==============================================================
# 协议接口
# ==============================================================

@runtime_checkable
class AgentExecutor(Protocol):
    """Agent 执行器协议——能对一个对抗样本执行审稿并返回结果。

    与 evaluation/eval_harness.py 中的 AgentRunner 兼容但更通用:
    AgentRunner.run(paper: TestPaper) → RunResult
    这里接受 AdversarialCase 并返回 CaseExecutionResult。
    """

    def execute_case(self, case: AdversarialCase) -> "CaseExecutionResult":
        """执行一个对抗样本并返回执行结果。"""
        ...


@runtime_checkable
class TrainingCallback(Protocol):
    """训练回调接口——用于外部系统（DualLoop/Reflection）监听训练进展。"""

    def on_round_complete(self, round_summary: "RoundSummary") -> None:
        """每轮训练完成后的回调。"""
        ...

    def on_session_complete(self, result: "TrainingResult") -> None:
        """训练会话完成后的回调。"""
        ...


# ==============================================================
# 执行结果
# ==============================================================

@dataclass
class CaseExecutionResult:
    """单个对抗样本的执行结果。"""
    case_id: str = ""
    passed: bool = False
    score: float = 0.0
    """0.0~1.0，0 = 完全失败，1 = 完美通过。"""

    predicted_findings: list[dict] = field(default_factory=list)
    """Agent 产出的 findings。"""

    matched_gold: int = 0
    total_gold: int = 0
    """匹配 gold standard 的数量。"""

    execution_time_seconds: float = 0.0
    error: Optional[str] = None
    """如果执行出错，记录错误信息。"""

    details: dict[str, Any] = field(default_factory=dict)
    """额外信息（如过程质量指标、LLM tokens consumed 等）。"""

    @property
    def is_error(self) -> bool:
        return self.error is not None

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "score": self.score,
            "predicted_findings_count": len(self.predicted_findings),
            "matched_gold": self.matched_gold,
            "total_gold": self.total_gold,
            "execution_time_seconds": round(self.execution_time_seconds, 3),
            "error": self.error,
        }


# ==============================================================
# 训练配置
# ==============================================================

class StopReason(str, Enum):
    """训练停止原因。"""
    CONVERGED = "converged"
    """所有维度达到目标掌握度。"""

    MAX_ROUNDS_REACHED = "max_rounds_reached"
    """达到最大训练轮次上限。"""

    TOKEN_BUDGET_EXHAUSTED = "token_budget_exhausted"
    """Token 预算耗尽。"""

    TIME_BUDGET_EXHAUSTED = "time_budget_exhausted"
    """时间预算耗尽。"""

    PLATEAU_DETECTED = "plateau_detected"
    """全局学习停滞（所有维度都无进步）。"""

    MANUAL_STOP = "manual_stop"
    """手动停止（外部调用 stop()）。"""

    DEGRADATION_DETECTED = "degradation_detected"
    """性能退化（训练导致已掌握维度恶化）。"""

    CURRICULUM_COMPLETE = "curriculum_complete"
    """课程完成（所有阶段已完成或跳过）。"""


@dataclass
class TrainingConfig:
    """训练超参数配置。

    所有超参数都有合理默认值，支持 YAML/JSON 序列化。
    """

    # --- 规模控制 ---
    max_rounds: int = 50
    """最大训练轮数。每轮 = 一批对抗样本的执行 + 评估。"""

    batch_size: int = 5
    """每轮生成的对抗样本数量。"""

    max_cases_per_session: int = 500
    """单次训练会话的对抗样本上限。"""

    # --- 收敛标准 ---
    target_mastery: float = 0.8
    """目标掌握度。所有维度达到此阈值 → 训练收敛。"""

    convergence_window: int = 5
    """收敛检测的滑动窗口大小（轮数）。"""

    convergence_threshold: float = 0.02
    """收敛阈值：窗口内 pass_rate 变化 < 此值视为收敛。"""

    min_rounds_before_convergence: int = 5
    """最少训练轮数（防止过早停止）。"""

    # --- 预算约束 ---
    token_budget: int = 0
    """Token 预算上限。0 = 无限制。"""

    time_budget_seconds: float = 0.0
    """时间预算上限（秒）。0 = 无限制。"""

    # --- 自适应策略 ---
    difficulty_ramp_speed: float = 1.0
    """难度提升速度。>1 = 激进，<1 = 保守。"""

    plateau_patience: int = 3
    """停滞容忍度: 连续多少轮无进步后触发策略调整。"""

    forgetting_check_interval: int = 5
    """遗忘检测频率（每隔多少轮检查一次）。"""

    degradation_threshold: float = -0.15
    """退化阈值: 已掌握维度 pass_rate 下降超过此值 → 触发修复训练。"""

    # --- 样本生成策略 ---
    exploration_ratio: float = 0.2
    """探索比例: 每轮中随机探索新维度的样本占比。"""

    variant_ratio: float = 0.3
    """变体比例: 从历史失败变体生成的样本占比。"""

    review_ratio: float = 0.1
    """回顾比例: 用于已掌握维度的回顾测试占比。"""

    # --- 质量控制 ---
    min_case_quality_score: float = 0.5
    """对抗样本最低质量分（低于此分的样本会被丢弃重生成）。"""

    max_generation_retries: int = 3
    """单个样本生成失败的最大重试次数。"""

    # --- 并发与性能 ---
    parallel_execution: bool = False
    """是否并行执行对抗样本（预留，当前版本为串行）。"""

    result_cache_size: int = 1000
    """结果缓存大小（防止重复执行相同 case）。"""

    def validate(self) -> list[str]:
        """验证配置合法性，返回错误列表。"""
        errors: list[str] = []
        if self.max_rounds < 1:
            errors.append("max_rounds must be >= 1")
        if self.batch_size < 1:
            errors.append("batch_size must be >= 1")
        if not (0.0 <= self.target_mastery <= 1.0):
            errors.append("target_mastery must be in [0, 1]")
        if self.convergence_window < 2:
            errors.append("convergence_window must be >= 2")
        if not (0.0 <= self.exploration_ratio <= 1.0):
            errors.append("exploration_ratio must be in [0, 1]")
        if not (0.0 <= self.variant_ratio <= 1.0):
            errors.append("variant_ratio must be in [0, 1]")
        if self.exploration_ratio + self.variant_ratio + self.review_ratio > 1.0:
            errors.append("exploration + variant + review ratios must sum to <= 1.0")
        return errors

    def to_dict(self) -> dict:
        return {
            "max_rounds": self.max_rounds,
            "batch_size": self.batch_size,
            "max_cases_per_session": self.max_cases_per_session,
            "target_mastery": self.target_mastery,
            "convergence_window": self.convergence_window,
            "convergence_threshold": self.convergence_threshold,
            "min_rounds_before_convergence": self.min_rounds_before_convergence,
            "token_budget": self.token_budget,
            "time_budget_seconds": self.time_budget_seconds,
            "difficulty_ramp_speed": self.difficulty_ramp_speed,
            "plateau_patience": self.plateau_patience,
            "forgetting_check_interval": self.forgetting_check_interval,
            "degradation_threshold": self.degradation_threshold,
            "exploration_ratio": self.exploration_ratio,
            "variant_ratio": self.variant_ratio,
            "review_ratio": self.review_ratio,
            "min_case_quality_score": self.min_case_quality_score,
            "max_generation_retries": self.max_generation_retries,
            "parallel_execution": self.parallel_execution,
            "result_cache_size": self.result_cache_size,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrainingConfig":
        config = cls()
        for key, val in data.items():
            if hasattr(config, key):
                setattr(config, key, val)
        return config


# ==============================================================
# 轮次摘要
# ==============================================================

@dataclass
class RoundSummary:
    """单轮训练结果摘要。"""
    round_number: int = 0
    timestamp: float = field(default_factory=time.time)

    # 执行统计
    cases_generated: int = 0
    cases_executed: int = 0
    cases_passed: int = 0
    cases_failed: int = 0
    cases_error: int = 0

    # 维度表现
    dimension_pass_rates: dict[str, float] = field(default_factory=dict)
    """各维度本轮的通过率。"""

    difficulty_pass_rates: dict[str, float] = field(default_factory=dict)
    """各难度等级本轮的通过率。"""

    # 趋势
    overall_pass_rate: float = 0.0
    improvement_from_last_round: float = 0.0
    """相比上一轮的改善幅度（正=进步，负=退步）。"""

    # 耗费
    tokens_consumed: int = 0
    time_elapsed_seconds: float = 0.0

    # 当前状态
    curriculum_progress: float = 0.0
    current_dimension: str = ""
    current_difficulty: str = ""

    # 策略调整
    strategy_adjustments: list[str] = field(default_factory=list)
    """本轮做出的策略调整说明。"""

    @property
    def pass_rate(self) -> float:
        if self.cases_executed == 0:
            return 0.0
        return self.cases_passed / self.cases_executed

    def to_dict(self) -> dict:
        return {
            "round_number": self.round_number,
            "timestamp": self.timestamp,
            "cases_generated": self.cases_generated,
            "cases_executed": self.cases_executed,
            "cases_passed": self.cases_passed,
            "cases_failed": self.cases_failed,
            "cases_error": self.cases_error,
            "overall_pass_rate": round(self.overall_pass_rate, 4),
            "improvement_from_last_round": round(self.improvement_from_last_round, 4),
            "dimension_pass_rates": self.dimension_pass_rates,
            "difficulty_pass_rates": self.difficulty_pass_rates,
            "tokens_consumed": self.tokens_consumed,
            "time_elapsed_seconds": round(self.time_elapsed_seconds, 3),
            "curriculum_progress": round(self.curriculum_progress, 4),
            "current_dimension": self.current_dimension,
            "current_difficulty": self.current_difficulty,
            "strategy_adjustments": self.strategy_adjustments,
        }


# ==============================================================
# 训练结果
# ==============================================================

@dataclass
class TrainingResult:
    """训练会话的最终结果。"""
    session_id: str = ""
    stop_reason: StopReason = StopReason.MAX_ROUNDS_REACHED

    # 总体统计
    total_rounds: int = 0
    total_cases_executed: int = 0
    total_cases_passed: int = 0
    total_tokens_consumed: int = 0
    total_time_seconds: float = 0.0

    # 掌握度
    initial_mastery: dict[str, float] = field(default_factory=dict)
    """训练开始时各维度的掌握度。"""

    final_mastery: dict[str, float] = field(default_factory=dict)
    """训练结束时各维度的掌握度。"""

    mastery_improvements: dict[str, float] = field(default_factory=dict)
    """各维度的改善幅度。"""

    # 学习效率
    overall_learning_efficiency: float = 0.0
    """总体学习效率 (0~1)。"""

    best_round: int = -1
    """表现最好的轮次。"""

    worst_round: int = -1
    """表现最差的轮次。"""

    # 详细历史
    round_summaries: list[RoundSummary] = field(default_factory=list)

    # 推荐
    recommendations: list[str] = field(default_factory=list)
    """基于训练结果的后续建议。"""

    @property
    def overall_pass_rate(self) -> float:
        if self.total_cases_executed == 0:
            return 0.0
        return self.total_cases_passed / self.total_cases_executed

    @property
    def avg_mastery_improvement(self) -> float:
        if not self.mastery_improvements:
            return 0.0
        return sum(self.mastery_improvements.values()) / len(self.mastery_improvements)

    @property
    def converged(self) -> bool:
        return self.stop_reason == StopReason.CONVERGED

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "stop_reason": self.stop_reason.value,
            "total_rounds": self.total_rounds,
            "total_cases_executed": self.total_cases_executed,
            "total_cases_passed": self.total_cases_passed,
            "total_tokens_consumed": self.total_tokens_consumed,
            "total_time_seconds": round(self.total_time_seconds, 2),
            "initial_mastery": self.initial_mastery,
            "final_mastery": self.final_mastery,
            "mastery_improvements": {
                k: round(v, 4) for k, v in self.mastery_improvements.items()
            },
            "overall_learning_efficiency": round(self.overall_learning_efficiency, 4),
            "overall_pass_rate": round(self.overall_pass_rate, 4),
            "best_round": self.best_round,
            "worst_round": self.worst_round,
            "recommendations": self.recommendations,
            "round_summaries": [rs.to_dict() for rs in self.round_summaries],
        }


# ==============================================================
# 收敛检测器
# ==============================================================

class ConvergenceDetector:
    """多信号融合的训练收敛检测器。

    收敛判定不是简单的 "pass_rate 不再变化"，而是综合多个信号:
        1. 全局停滞: 所有活跃维度的学习速率趋近 0
        2. 目标达成: 所有维度掌握度 >= target_mastery
        3. 边际递减: 每额外一轮训练带来的改善 < 阈值
        4. 退化风险: 继续训练导致已掌握维度退步

    还提供"是否应该调整策略"的建议（区分"该停止"和"该换方向"）。
    """

    def __init__(self, config: TrainingConfig):
        self._config = config
        self._round_pass_rates: list[float] = []
        self._dimension_trends: dict[str, list[float]] = defaultdict(list)
        self._plateau_counter: int = 0
        self._degradation_events: list[dict] = []

    def record_round(
        self,
        round_number: int,
        overall_pass_rate: float,
        dimension_mastery: dict[str, float],
        tracker: LearningCurveTracker,
    ) -> None:
        """记录一轮训练的数据。"""
        self._round_pass_rates.append(overall_pass_rate)
        for dim, mastery in dimension_mastery.items():
            self._dimension_trends[dim].append(mastery)

        # 检测退化
        self._check_degradation(dimension_mastery, tracker)

    def should_stop(self) -> tuple[bool, Optional[StopReason]]:
        """判断是否应该停止训练。

        Returns:
            (should_stop, reason) — reason 为 None 表示不应停止。
        """
        # 最小轮次保护
        if len(self._round_pass_rates) < self._config.min_rounds_before_convergence:
            return False, None

        # 检查 1: 目标达成
        if self._all_dimensions_mastered():
            return True, StopReason.CONVERGED

        # 检查 2: 全局停滞
        if self._is_globally_plateaued():
            self._plateau_counter += 1
            if self._plateau_counter >= self._config.plateau_patience:
                return True, StopReason.PLATEAU_DETECTED
        else:
            self._plateau_counter = 0

        # 检查 3: 退化风险
        if self._is_degrading():
            return True, StopReason.DEGRADATION_DETECTED

        # 检查 4: 课程完成
        # (由外部设置，这里只检查 pass_rate 趋势)

        return False, None

    def get_strategy_advice(self) -> list[str]:
        """获取策略调整建议（不是停止，而是换方向）。"""
        advice: list[str] = []

        # 某些维度停滞了但其他还在进步 → 建议换维度
        stalled_dims = self._get_stalled_dimensions()
        if stalled_dims:
            advice.append(
                f"维度 {stalled_dims} 学习停滞，建议暂时切换到其他维度或降低难度"
            )

        # 通过率太高 → 建议提升难度
        if self._recent_pass_rate() > 0.9:
            advice.append(
                "最近通过率过高(>90%)，建议提升难度以增加挑战性"
            )

        # 通过率太低 → 建议降低难度
        if self._recent_pass_rate() < 0.2:
            advice.append(
                "最近通过率过低(<20%)，建议降低难度或增加简单样本比例"
            )

        # 退化事件 → 建议回顾
        if self._degradation_events:
            recent_degraded = [
                e["dimension"] for e in self._degradation_events[-3:]
            ]
            advice.append(
                f"维度 {recent_degraded} 出现退化，建议插入回顾训练"
            )

        return advice

    def _all_dimensions_mastered(self) -> bool:
        """所有追踪维度是否都达到目标。"""
        if not self._dimension_trends:
            return False
        for dim, trend in self._dimension_trends.items():
            if not trend:
                return False
            if trend[-1] < self._config.target_mastery:
                return False
        return True

    def _is_globally_plateaued(self) -> bool:
        """全局是否停滞。"""
        window = self._config.convergence_window
        if len(self._round_pass_rates) < window:
            return False

        recent = self._round_pass_rates[-window:]
        if not recent:
            return False

        max_rate = max(recent)
        min_rate = min(recent)
        return (max_rate - min_rate) < self._config.convergence_threshold

    def _is_degrading(self) -> bool:
        """是否存在严重退化。"""
        for dim, trend in self._dimension_trends.items():
            if len(trend) < 3:
                continue
            # 与峰值相比
            peak = max(trend)
            current = trend[-1]
            if (current - peak) < self._config.degradation_threshold:
                return True
        return False

    def _get_stalled_dimensions(self) -> list[str]:
        """获取学习停滞的维度列表。"""
        stalled: list[str] = []
        window = self._config.convergence_window
        for dim, trend in self._dimension_trends.items():
            if len(trend) < window:
                continue
            recent = trend[-window:]
            variation = max(recent) - min(recent)
            if variation < self._config.convergence_threshold:
                stalled.append(dim)
        return stalled

    def _recent_pass_rate(self) -> float:
        """最近几轮的平均通过率。"""
        if not self._round_pass_rates:
            return 0.0
        window = min(3, len(self._round_pass_rates))
        return sum(self._round_pass_rates[-window:]) / window

    def _check_degradation(
        self,
        current_mastery: dict[str, float],
        tracker: LearningCurveTracker,
    ) -> None:
        """检查是否有维度发生退化。"""
        for dim_str, mastery in current_mastery.items():
            trend = self._dimension_trends.get(dim_str, [])
            if len(trend) < 3:
                continue
            peak = max(trend[:-1]) if len(trend) > 1 else mastery
            if (mastery - peak) < self._config.degradation_threshold:
                self._degradation_events.append({
                    "dimension": dim_str,
                    "peak_mastery": peak,
                    "current_mastery": mastery,
                    "round": len(self._round_pass_rates),
                    "timestamp": time.time(),
                })

    def serialize(self) -> dict:
        return {
            "round_pass_rates": self._round_pass_rates,
            "dimension_trends": dict(self._dimension_trends),
            "plateau_counter": self._plateau_counter,
            "degradation_events": self._degradation_events,
        }

    @classmethod
    def deserialize(cls, data: dict, config: TrainingConfig) -> "ConvergenceDetector":
        detector = cls(config)
        detector._round_pass_rates = data.get("round_pass_rates", [])
        detector._dimension_trends = defaultdict(list, data.get("dimension_trends", {}))
        detector._plateau_counter = data.get("plateau_counter", 0)
        detector._degradation_events = data.get("degradation_events", [])
        return detector


# ==============================================================
# 训练会话
# ==============================================================

class SessionStatus(str, Enum):
    """会话状态。"""
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TrainingSession:
    """训练会话——可暂停/恢复的训练实例。

    一个 Session 对应一次完整的训练流程（从弱点分析到收敛判定）。
    所有状态都可序列化，支持断点续训。
    """
    session_id: str = ""
    status: SessionStatus = SessionStatus.CREATED
    config: TrainingConfig = field(default_factory=TrainingConfig)

    # 训练进度
    current_round: int = 0
    total_cases_executed: int = 0
    total_cases_passed: int = 0
    total_tokens_consumed: int = 0

    # 时间
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    paused_at: float = 0.0
    completed_at: float = 0.0
    total_pause_duration: float = 0.0

    # 课程
    curriculum: Optional[TrainingCurriculum] = None
    weakness_profile: Optional[WeaknessProfile] = None

    # 历史
    round_summaries: list[RoundSummary] = field(default_factory=list)
    error_log: list[dict] = field(default_factory=list)

    # 初始基线
    initial_mastery: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        if not self.session_id:
            content = f"session_{time.time()}_{id(self)}"
            self.session_id = hashlib.md5(content.encode()).hexdigest()[:16]

    @property
    def elapsed_seconds(self) -> float:
        """实际训练耗时（排除暂停时间）。"""
        if self.status == SessionStatus.COMPLETED:
            return self.completed_at - self.started_at - self.total_pause_duration
        elif self.status == SessionStatus.PAUSED:
            return self.paused_at - self.started_at - self.total_pause_duration
        elif self.started_at > 0:
            return time.time() - self.started_at - self.total_pause_duration
        return 0.0

    @property
    def overall_pass_rate(self) -> float:
        if self.total_cases_executed == 0:
            return 0.0
        return self.total_cases_passed / self.total_cases_executed

    def start(self) -> None:
        """开始训练会话。"""
        if self.status not in (SessionStatus.CREATED, SessionStatus.PAUSED):
            raise ValueError(f"Cannot start session in status: {self.status.value}")

        if self.status == SessionStatus.PAUSED:
            self.total_pause_duration += time.time() - self.paused_at
        else:
            self.started_at = time.time()

        self.status = SessionStatus.RUNNING

    def pause(self) -> None:
        """暂停训练会话。"""
        if self.status != SessionStatus.RUNNING:
            raise ValueError(f"Cannot pause session in status: {self.status.value}")
        self.paused_at = time.time()
        self.status = SessionStatus.PAUSED

    def complete(self, stop_reason: StopReason) -> None:
        """完成训练会话。"""
        self.completed_at = time.time()
        self.status = SessionStatus.COMPLETED

    def fail(self, error: str) -> None:
        """标记会话失败。"""
        self.completed_at = time.time()
        self.status = SessionStatus.FAILED
        self.error_log.append({
            "type": "session_failure",
            "error": error,
            "timestamp": time.time(),
            "round": self.current_round,
        })

    def record_case_result(self, result: CaseExecutionResult) -> None:
        """记录一个 case 的执行结果。"""
        self.total_cases_executed += 1
        if result.passed:
            self.total_cases_passed += 1
        if result.details.get("tokens_consumed"):
            self.total_tokens_consumed += result.details["tokens_consumed"]

    def is_budget_exhausted(self) -> tuple[bool, Optional[StopReason]]:
        """检查预算是否耗尽。"""
        if self.config.token_budget > 0 and self.total_tokens_consumed >= self.config.token_budget:
            return True, StopReason.TOKEN_BUDGET_EXHAUSTED
        if self.config.time_budget_seconds > 0 and self.elapsed_seconds >= self.config.time_budget_seconds:
            return True, StopReason.TIME_BUDGET_EXHAUSTED
        if self.total_cases_executed >= self.config.max_cases_per_session:
            return True, StopReason.MAX_ROUNDS_REACHED
        return False, None

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "status": self.status.value,
            "config": self.config.to_dict(),
            "current_round": self.current_round,
            "total_cases_executed": self.total_cases_executed,
            "total_cases_passed": self.total_cases_passed,
            "total_tokens_consumed": self.total_tokens_consumed,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "paused_at": self.paused_at,
            "completed_at": self.completed_at,
            "total_pause_duration": self.total_pause_duration,
            "curriculum": self.curriculum.to_dict() if self.curriculum else None,
            "weakness_profile": self.weakness_profile.to_dict() if self.weakness_profile else None,
            "initial_mastery": self.initial_mastery,
            "round_summaries": [rs.to_dict() for rs in self.round_summaries],
            "error_log": self.error_log,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrainingSession":
        config = TrainingConfig.from_dict(data.get("config", {}))
        curriculum = None
        if data.get("curriculum"):
            curriculum = TrainingCurriculum.from_dict(data["curriculum"])
        weakness_profile = None
        if data.get("weakness_profile"):
            weakness_profile = WeaknessProfile.from_dict(data["weakness_profile"])

        session = cls(
            session_id=data.get("session_id", ""),
            status=SessionStatus(data.get("status", "created")),
            config=config,
            current_round=data.get("current_round", 0),
            total_cases_executed=data.get("total_cases_executed", 0),
            total_cases_passed=data.get("total_cases_passed", 0),
            total_tokens_consumed=data.get("total_tokens_consumed", 0),
            created_at=data.get("created_at", time.time()),
            started_at=data.get("started_at", 0.0),
            paused_at=data.get("paused_at", 0.0),
            completed_at=data.get("completed_at", 0.0),
            total_pause_duration=data.get("total_pause_duration", 0.0),
            curriculum=curriculum,
            weakness_profile=weakness_profile,
            initial_mastery=data.get("initial_mastery", {}),
            error_log=data.get("error_log", []),
        )
        # 恢复 round_summaries (需要简化重建)
        for rs_data in data.get("round_summaries", []):
            rs = RoundSummary(
                round_number=rs_data.get("round_number", 0),
                timestamp=rs_data.get("timestamp", 0.0),
                cases_generated=rs_data.get("cases_generated", 0),
                cases_executed=rs_data.get("cases_executed", 0),
                cases_passed=rs_data.get("cases_passed", 0),
                cases_failed=rs_data.get("cases_failed", 0),
                cases_error=rs_data.get("cases_error", 0),
                overall_pass_rate=rs_data.get("overall_pass_rate", 0.0),
                improvement_from_last_round=rs_data.get("improvement_from_last_round", 0.0),
                dimension_pass_rates=rs_data.get("dimension_pass_rates", {}),
                difficulty_pass_rates=rs_data.get("difficulty_pass_rates", {}),
                tokens_consumed=rs_data.get("tokens_consumed", 0),
                time_elapsed_seconds=rs_data.get("time_elapsed_seconds", 0.0),
                curriculum_progress=rs_data.get("curriculum_progress", 0.0),
                current_dimension=rs_data.get("current_dimension", ""),
                current_difficulty=rs_data.get("current_difficulty", ""),
                strategy_adjustments=rs_data.get("strategy_adjustments", []),
            )
            session.round_summaries.append(rs)

        return session


# ==============================================================
# 训练循环引擎
# ==============================================================

class TrainingLoop:
    """自我训练循环的核心编排器。

    协调弱点分析 → 课程设计 → 样本生成 → Agent 执行 → 结果评估 → 学习曲线更新
    的完整闭环。

    Usage:
        loop = TrainingLoop(
            executor=my_agent_executor,
            weakness_analyzer=analyzer,
            generator=generator,
            config=TrainingConfig(max_rounds=30),
        )
        result = loop.run()
        print(f"Converged: {result.converged}, Rounds: {result.total_rounds}")

    高级用法（暂停/恢复）:
        # 首次运行
        loop.start()
        while not loop.is_complete:
            loop.step()  # 执行一轮
            if need_pause:
                state = loop.pause()
                save_to_disk(state)
                break

        # 恢复
        state = load_from_disk()
        loop = TrainingLoop.resume(state, executor=my_agent_executor)
        result = loop.run()
    """

    def __init__(
        self,
        executor: AgentExecutor,
        weakness_analyzer: WeaknessAnalyzer,
        generator: AdversarialGenerator,
        config: Optional[TrainingConfig] = None,
        curriculum_designer: Optional[CurriculumDesigner] = None,
        callbacks: Optional[list[TrainingCallback]] = None,
        event_publisher: Optional[Callable[[str, dict], None]] = None,
    ):
        """初始化训练循环。

        Args:
            executor: Agent 执行器（实际运行 Agent 审稿）
            weakness_analyzer: 弱点分析器（提取 Agent 弱点画像）
            generator: 对抗样本生成器（根据弱点生成挑战）
            config: 训练配置
            curriculum_designer: 课程设计器（可选，不提供时自动创建）
            callbacks: 训练回调列表（外部监听器）
            event_publisher: 事件发布函数，签名 (event_name, payload)
        """
        self._executor = executor
        self._analyzer = weakness_analyzer
        self._generator = generator
        self._config = config or TrainingConfig()
        self._designer = curriculum_designer
        self._callbacks = callbacks or []
        self._publish_event = event_publisher or self._noop_publish

        # 内部状态
        self._session: Optional[TrainingSession] = None
        self._tracker: LearningCurveTracker = LearningCurveTracker()
        self._convergence: Optional[ConvergenceDetector] = None
        self._stop_requested: bool = False

        # 结果缓存（避免重复执行相同 case）
        self._result_cache: dict[str, CaseExecutionResult] = {}

        # 验证配置
        errors = self._config.validate()
        if errors:
            raise ValueError(f"Invalid TrainingConfig: {errors}")

    @property
    def session(self) -> Optional[TrainingSession]:
        return self._session

    @property
    def is_running(self) -> bool:
        return (
            self._session is not None
            and self._session.status == SessionStatus.RUNNING
        )

    @property
    def is_complete(self) -> bool:
        return (
            self._session is not None
            and self._session.status in (SessionStatus.COMPLETED, SessionStatus.FAILED)
        )

    # ----------------------------------------------------------
    # 主入口
    # ----------------------------------------------------------

    def run(self) -> TrainingResult:
        """执行完整的训练循环直到收敛或预算耗尽。

        这是最简单的入口——一次调用完成所有训练。

        Returns:
            TrainingResult 包含完整的训练结果和统计。
        """
        if not ADVERSARIAL_TRAINING_ENABLED:
            logger.info("Adversarial training disabled by Kill Switch.")
            return TrainingResult(
                session_id="disabled",
                stop_reason=StopReason.MANUAL_STOP,
                recommendations=["Kill Switch SCHOLAR_GODEL_ADVERSARIAL_TRAINING is OFF."],
            )

        self.start()

        while not self.is_complete:
            self.step()

        return self._finalize_result()

    def start(self) -> None:
        """初始化并开始一个新的训练会话。"""
        logger.info("=== Starting adversarial self-training session ===")

        # 1. 创建会话
        self._session = TrainingSession(config=self._config)
        self._convergence = ConvergenceDetector(self._config)

        # 2. 分析弱点
        self._publish_event("training.weakness_analysis_started", {
            "session_id": self._session.session_id,
        })
        profile = self._analyzer.analyze()
        self._session.weakness_profile = profile
        logger.info(
            "Weakness profile: %d entries across %d dimensions",
            len(profile.entries) if profile else 0,
            len(profile.dimension_distribution()) if profile else 0,
        )

        # 3. 设计课程
        if self._designer is None:
            self._designer = CurriculumDesigner(
                profile=profile,
                tracker=self._tracker,
            )
        curriculum = self._designer.design_curriculum()
        self._session.curriculum = curriculum
        logger.info(
            "Curriculum designed: %d stages, target mastery=%.2f",
            len(curriculum.stages) if curriculum else 0,
            self._config.target_mastery,
        )

        # 4. 记录初始基线
        self._session.initial_mastery = dict(self._tracker.get_all_mastery())

        # 5. 启动
        self._session.start()
        self._publish_event("training.session_started", {
            "session_id": self._session.session_id,
            "curriculum_stages": len(curriculum.stages) if curriculum else 0,
            "weakness_dimensions": len(profile.entries) if profile else 0,
            "config": self._config.to_dict(),
        })

    def step(self) -> Optional[RoundSummary]:
        """执行一轮训练（生成 → 执行 → 评估 → 更新）。

        Returns:
            本轮的 RoundSummary，如果会话已结束则返回 None。
        """
        if not self._session or self._session.status != SessionStatus.RUNNING:
            return None

        # 检查是否应该停止
        if self._should_stop_before_round():
            return None

        round_start = time.time()
        self._session.current_round += 1
        round_num = self._session.current_round

        logger.info("--- Training Round %d ---", round_num)
        self._publish_event("training.round_started", {
            "session_id": self._session.session_id,
            "round": round_num,
        })

        # 1. 确定当前阶段（从课程中获取）
        stage = self._get_current_stage()
        dimension = stage.dimension if stage else None
        difficulty = stage.difficulty if stage else DifficultyLevel.MEDIUM

        # 2. 生成对抗样本
        cases = self._generate_cases_for_round(stage)
        if not cases:
            logger.warning("No cases generated for round %d, skipping.", round_num)
            # 如果连续生成为空，可能课程已完成
            summary = self._create_empty_round_summary(round_num, round_start)
            return summary

        # 3. 执行对抗样本
        results = self._execute_cases(cases)

        # 4. 评估结果并更新学习曲线
        self._update_from_results(stage, cases, results)

        # 5. 构建轮次摘要
        summary = self._build_round_summary(
            round_num, cases, results, stage, round_start
        )
        self._session.round_summaries.append(summary)

        # 6. 收敛检测
        self._convergence.record_round(
            round_number=round_num,
            overall_pass_rate=summary.overall_pass_rate,
            dimension_mastery=self._tracker.get_all_mastery(),
            tracker=self._tracker,
        )

        # 7. 策略建议
        advice = self._convergence.get_strategy_advice()
        if advice:
            summary.strategy_adjustments = advice
            logger.info("Strategy advice: %s", advice)

        # 8. 发布事件 & 回调
        self._publish_event("training.round_completed", {
            "session_id": self._session.session_id,
            "round": round_num,
            "pass_rate": summary.overall_pass_rate,
            "cases_executed": summary.cases_executed,
        })
        for cb in self._callbacks:
            try:
                cb.on_round_complete(summary)
            except Exception as e:
                logger.error("Callback error: %s", e)

        # 9. 检查停止条件
        self._check_stop_conditions()

        return summary

    def pause(self) -> dict:
        """暂停训练会话，返回可序列化的完整状态。"""
        if self._session and self._session.status == SessionStatus.RUNNING:
            self._session.pause()
            self._publish_event("training.session_paused", {
                "session_id": self._session.session_id,
                "round": self._session.current_round,
            })
        return self.serialize()

    def stop(self) -> None:
        """手动停止训练。"""
        self._stop_requested = True
        if self._session and self._session.status == SessionStatus.RUNNING:
            self._session.complete(StopReason.MANUAL_STOP)
            self._publish_event("training.session_stopped", {
                "session_id": self._session.session_id,
                "reason": StopReason.MANUAL_STOP.value,
            })

    @classmethod
    def resume(
        cls,
        state: dict,
        executor: AgentExecutor,
        weakness_analyzer: WeaknessAnalyzer,
        generator: AdversarialGenerator,
        callbacks: Optional[list[TrainingCallback]] = None,
        event_publisher: Optional[Callable[[str, dict], None]] = None,
    ) -> "TrainingLoop":
        """从序列化状态恢复训练循环。"""
        config = TrainingConfig.from_dict(state.get("config", {}))
        loop = cls(
            executor=executor,
            weakness_analyzer=weakness_analyzer,
            generator=generator,
            config=config,
            callbacks=callbacks,
            event_publisher=event_publisher,
        )

        # 恢复内部状态
        loop._session = TrainingSession.from_dict(state.get("session", {}))
        loop._tracker = LearningCurveTracker.deserialize(state.get("tracker", {}))
        loop._convergence = ConvergenceDetector.deserialize(
            state.get("convergence", {}), config
        )

        # 如果之前是暂停状态，恢复为运行
        if loop._session.status == SessionStatus.PAUSED:
            loop._session.start()

        logger.info(
            "Resumed training session %s at round %d",
            loop._session.session_id,
            loop._session.current_round,
        )
        return loop

    # ----------------------------------------------------------
    # 内部方法: 样本生成
    # ----------------------------------------------------------

    def _generate_cases_for_round(
        self,
        stage: Optional[CurriculumStage],
    ) -> list[AdversarialCase]:
        """为当前轮次生成对抗样本。

        策略:
            - 主力样本: 针对当前课程阶段的维度/难度 (1 - exploration - variant - review)
            - 探索样本: 随机探索新维度 (exploration_ratio)
            - 变体样本: 从历史失败生成变体 (variant_ratio)
            - 回顾样本: 已掌握维度的回顾测试 (review_ratio)
        """
        batch_size = self._config.batch_size
        cases: list[AdversarialCase] = []

        # 计算各类样本数量
        n_exploration = max(1, int(batch_size * self._config.exploration_ratio))
        n_variant = int(batch_size * self._config.variant_ratio)
        n_review = int(batch_size * self._config.review_ratio)
        n_main = batch_size - n_exploration - n_variant - n_review
        n_main = max(1, n_main)

        # 主力样本
        if stage:
            main_cases = self._generate_targeted_cases(
                dimension=stage.dimension,
                difficulty=stage.difficulty,
                challenge_types=stage.challenge_types,
                count=n_main,
            )
            cases.extend(main_cases)

        # 探索样本
        exploration_cases = self._generate_exploration_cases(n_exploration)
        cases.extend(exploration_cases)

        # 变体样本
        if n_variant > 0:
            variant_cases = self._generate_variant_cases(n_variant)
            cases.extend(variant_cases)

        # 回顾样本
        if n_review > 0:
            review_cases = self._generate_review_cases(n_review)
            cases.extend(review_cases)

        logger.info(
            "Generated %d cases: main=%d, explore=%d, variant=%d, review=%d",
            len(cases), len(cases) - n_exploration - n_variant - n_review,
            n_exploration, n_variant, n_review,
        )

        return cases

    def _generate_targeted_cases(
        self,
        dimension: WeaknessDimension,
        difficulty: DifficultyLevel,
        challenge_types: list[ChallengeType],
        count: int,
    ) -> list[AdversarialCase]:
        """生成针对特定维度/难度的样本。"""
        cases: list[AdversarialCase] = []
        for _ in range(count):
            for attempt in range(self._config.max_generation_retries):
                try:
                    ct = challenge_types[0] if challenge_types else None
                    case = self._run_async(
                        self._generator.generate_challenge(
                            challenge_type=ct,
                            difficulty=difficulty,
                        )
                    )
                    if case and case.paper_snippet:
                        case.target_dimension = dimension
                        cases.append(case)
                        break
                except Exception as e:
                    logger.warning(
                        "Generation attempt %d failed: %s", attempt + 1, e
                    )
                    if attempt == self._config.max_generation_retries - 1:
                        logger.error("Failed to generate case after %d retries", attempt + 1)
        return cases

    def _generate_exploration_cases(self, count: int) -> list[AdversarialCase]:
        """生成探索性样本（随机维度/难度，用于发现未知弱点）。"""
        import random
        cases: list[AdversarialCase] = []
        all_dims = list(WeaknessDimension)

        for _ in range(count):
            dim = random.choice(all_dims)
            diff = random.choice([DifficultyLevel.EASY, DifficultyLevel.MEDIUM])
            try:
                case = self._run_async(
                    self._generator.generate_challenge(
                        difficulty=diff,
                    )
                )
                if case and case.paper_snippet:
                    case.target_dimension = dim
                    cases.append(case)
            except Exception as e:
                logger.debug("Exploration case generation failed: %s", e)

        return cases

    def _generate_variant_cases(self, count: int) -> list[AdversarialCase]:
        """从历史失败中生成变体样本。"""
        cases: list[AdversarialCase] = []

        # 收集最近失败的 case 上下文
        failure_contexts: list[dict] = []
        if self._session:
            for err_entry in self._session.error_log[-10:]:
                if err_entry.get("type") == "case_execution_error":
                    failure_contexts.append(err_entry)

        # 通过 generate_from_failure 生成变体
        for i in range(count):
            ctx = failure_contexts[i % len(failure_contexts)] if failure_contexts else {"failure_type": "unknown"}
            try:
                case = self._run_async(
                    self._generator.generate_from_failure(
                        failure_context=ctx,
                        original_case=None,
                        variation_type="surface",
                    )
                )
                if case and case.paper_snippet:
                    cases.append(case)
            except Exception as e:
                logger.debug("Variant generation failed: %s", e)

        return cases

    def _generate_review_cases(self, count: int) -> list[AdversarialCase]:
        """生成回顾样本（针对已掌握维度的巩固测试）。"""
        cases: list[AdversarialCase] = []
        # 选择掌握度较高的维度进行回顾
        mastery = self._tracker.get_all_mastery()
        mastered_dims = [
            WeaknessDimension(dim_str)
            for dim_str, m in mastery.items()
            if m >= 0.7
        ]

        if not mastered_dims:
            return cases

        import random
        for _ in range(count):
            dim = random.choice(mastered_dims)
            try:
                case = self._run_async(
                    self._generator.generate_challenge(
                        difficulty=DifficultyLevel.HARD,
                    )
                )
                if case and case.paper_snippet:
                    case.target_dimension = dim
                    cases.append(case)
            except Exception as e:
                logger.debug("Review case generation failed: %s", e)

        return cases

    # ----------------------------------------------------------
    # 内部方法: 样本执行
    # ----------------------------------------------------------

    def _execute_cases(
        self, cases: list[AdversarialCase]
    ) -> list[CaseExecutionResult]:
        """执行一批对抗样本。"""
        results: list[CaseExecutionResult] = []

        for case in cases:
            # 缓存检查
            if case.case_id in self._result_cache:
                results.append(self._result_cache[case.case_id])
                continue

            result = self._execute_single_case(case)
            results.append(result)

            # 更新缓存
            if len(self._result_cache) < self._config.result_cache_size:
                self._result_cache[case.case_id] = result

            # 更新 session 统计
            if self._session:
                self._session.record_case_result(result)

            # 预算检查（每执行一个就检查一次，及时停止）
            if self._session:
                exhausted, reason = self._session.is_budget_exhausted()
                if exhausted:
                    logger.info(
                        "Budget exhausted mid-round: %s", reason.value if reason else "unknown"
                    )
                    break

        return results

    def _execute_single_case(self, case: AdversarialCase) -> CaseExecutionResult:
        """执行单个对抗样本。"""
        start = time.time()
        try:
            result = self._executor.execute_case(case)
            result.execution_time_seconds = time.time() - start
            result.case_id = case.case_id

            # 更新 case 的使用记录
            case.record_usage(result.passed)

            return result

        except Exception as e:
            elapsed = time.time() - start
            logger.error("Case execution error [%s]: %s", case.case_id, e)

            # 记录错误到 session
            if self._session:
                self._session.error_log.append({
                    "type": "case_execution_error",
                    "case_id": case.case_id,
                    "error": str(e),
                    "timestamp": time.time(),
                    "round": self._session.current_round,
                })

            return CaseExecutionResult(
                case_id=case.case_id,
                passed=False,
                score=0.0,
                execution_time_seconds=elapsed,
                error=str(e),
            )

    # ----------------------------------------------------------
    # 内部方法: 结果评估与更新
    # ----------------------------------------------------------

    def _update_from_results(
        self,
        stage: Optional[CurriculumStage],
        cases: list[AdversarialCase],
        results: list[CaseExecutionResult],
    ) -> None:
        """根据执行结果更新学习曲线和课程状态。"""
        # 按维度汇总
        dim_results: dict[str, list[CaseExecutionResult]] = defaultdict(list)
        for case, result in zip(cases, results):
            if not result.is_error:
                dim_results[case.target_dimension.value].append(result)

        # 更新学习曲线
        for dim_str, dim_res in dim_results.items():
            if not dim_res:
                continue
            pass_rate = sum(1 for r in dim_res if r.passed) / len(dim_res)
            dim = WeaknessDimension(dim_str)

            # 确定这些 cases 的难度（取众数）
            diff = DifficultyLevel.MEDIUM
            if stage and stage.dimension == dim:
                diff = stage.difficulty

            cumulative = sum(
                len(curve_pts)
                for key, curve_pts in self._tracker._curves.items()
                if key.startswith(f"{dim_str}:")
            ) + len(dim_res)

            self._tracker.record(
                dimension=dim,
                difficulty=diff,
                pass_rate=pass_rate,
                cumulative_attempts=cumulative,
            )

        # 更新课程阶段状态
        if stage:
            for case, result in zip(cases, results):
                if case.target_dimension == stage.dimension and not result.is_error:
                    stage_result = StageResult(
                        case_id=case.case_id,
                        passed=result.passed,
                        score=result.score,
                        timestamp=time.time(),
                    )
                    stage.record_result(stage_result)

    # ----------------------------------------------------------
    # 内部方法: 轮次摘要构建
    # ----------------------------------------------------------

    def _build_round_summary(
        self,
        round_num: int,
        cases: list[AdversarialCase],
        results: list[CaseExecutionResult],
        stage: Optional[CurriculumStage],
        round_start: float,
    ) -> RoundSummary:
        """构建轮次摘要。"""
        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed and not r.is_error)
        errors = sum(1 for r in results if r.is_error)
        executed = len(results)

        # 维度通过率
        dim_stats: dict[str, list[bool]] = defaultdict(list)
        diff_stats: dict[str, list[bool]] = defaultdict(list)
        for case, result in zip(cases, results):
            if not result.is_error:
                dim_stats[case.target_dimension.value].append(result.passed)
                diff_stats[case.difficulty.value].append(result.passed)

        dim_pass_rates = {
            d: sum(v) / len(v) for d, v in dim_stats.items() if v
        }
        diff_pass_rates = {
            d: sum(v) / len(v) for d, v in diff_stats.items() if v
        }

        # 与上一轮比较
        prev_rate = (
            self._session.round_summaries[-1].overall_pass_rate
            if self._session and self._session.round_summaries
            else 0.0
        )
        current_rate = passed / executed if executed > 0 else 0.0
        improvement = current_rate - prev_rate

        # Token 统计
        tokens = sum(
            r.details.get("tokens_consumed", 0)
            for r in results
        )

        summary = RoundSummary(
            round_number=round_num,
            timestamp=time.time(),
            cases_generated=len(cases),
            cases_executed=executed,
            cases_passed=passed,
            cases_failed=failed,
            cases_error=errors,
            dimension_pass_rates=dim_pass_rates,
            difficulty_pass_rates=diff_pass_rates,
            overall_pass_rate=current_rate,
            improvement_from_last_round=improvement,
            tokens_consumed=tokens,
            time_elapsed_seconds=time.time() - round_start,
            curriculum_progress=self._session.curriculum.progress if self._session and self._session.curriculum else 0.0,
            current_dimension=stage.dimension.value if stage else "",
            current_difficulty=stage.difficulty.value if stage else "",
        )

        return summary

    def _create_empty_round_summary(
        self, round_num: int, round_start: float
    ) -> RoundSummary:
        """创建空轮次摘要（当无法生成样本时）。"""
        summary = RoundSummary(
            round_number=round_num,
            timestamp=time.time(),
            time_elapsed_seconds=time.time() - round_start,
            strategy_adjustments=["无法生成对抗样本，可能课程已完成或生成器配置问题"],
        )
        if self._session:
            self._session.round_summaries.append(summary)
        return summary

    # ----------------------------------------------------------
    # 内部方法: 控制流
    # ----------------------------------------------------------

    def _get_current_stage(self) -> Optional[CurriculumStage]:
        """获取课程的当前阶段。"""
        if not self._session or not self._session.curriculum:
            return None
        return self._session.curriculum.current_stage

    def _should_stop_before_round(self) -> bool:
        """在新一轮开始前检查是否应该停止。"""
        if self._stop_requested:
            self._session.complete(StopReason.MANUAL_STOP)
            return True

        # 最大轮次
        if self._session.current_round >= self._config.max_rounds:
            self._session.complete(StopReason.MAX_ROUNDS_REACHED)
            self._publish_event("training.session_completed", {
                "session_id": self._session.session_id,
                "reason": StopReason.MAX_ROUNDS_REACHED.value,
            })
            return True

        # 预算
        exhausted, reason = self._session.is_budget_exhausted()
        if exhausted:
            self._session.complete(reason)
            self._publish_event("training.session_completed", {
                "session_id": self._session.session_id,
                "reason": reason.value if reason else "budget_exhausted",
            })
            return True

        # 课程完成
        if self._session.curriculum and self._session.curriculum.progress >= 1.0:
            self._session.complete(StopReason.CURRICULUM_COMPLETE)
            self._publish_event("training.session_completed", {
                "session_id": self._session.session_id,
                "reason": StopReason.CURRICULUM_COMPLETE.value,
            })
            return True

        return False

    def _check_stop_conditions(self) -> None:
        """在轮次结束后检查收敛条件。"""
        if not self._convergence:
            return

        should_stop, reason = self._convergence.should_stop()
        if should_stop and reason:
            logger.info(
                "Training converged/stopped: %s (round %d)",
                reason.value, self._session.current_round,
            )
            self._session.complete(reason)
            self._publish_event("training.session_completed", {
                "session_id": self._session.session_id,
                "reason": reason.value,
                "round": self._session.current_round,
            })

    # ----------------------------------------------------------
    # 内部方法: 结果整理
    # ----------------------------------------------------------

    def _finalize_result(self) -> TrainingResult:
        """整理最终训练结果。"""
        if not self._session:
            return TrainingResult()

        final_mastery = self._tracker.get_all_mastery()
        initial_mastery = self._session.initial_mastery

        # 计算改善
        improvements: dict[str, float] = {}
        for dim_str, final_m in final_mastery.items():
            initial_m = initial_mastery.get(dim_str, 0.0)
            improvements[dim_str] = final_m - initial_m

        # 找到最佳/最差轮次
        best_round = -1
        worst_round = -1
        best_rate = -1.0
        worst_rate = 2.0
        for rs in self._session.round_summaries:
            if rs.overall_pass_rate > best_rate:
                best_rate = rs.overall_pass_rate
                best_round = rs.round_number
            if rs.overall_pass_rate < worst_rate:
                worst_rate = rs.overall_pass_rate
                worst_round = rs.round_number

        # 生成建议
        recommendations = self._generate_recommendations(final_mastery, improvements)

        # 确定停止原因
        stop_reason = StopReason.MAX_ROUNDS_REACHED
        if self._session.status == SessionStatus.COMPLETED:
            # 从 round_summaries 和 convergence 推断
            if self._convergence:
                _, detected_reason = self._convergence.should_stop()
                if detected_reason:
                    stop_reason = detected_reason

        result = TrainingResult(
            session_id=self._session.session_id,
            stop_reason=stop_reason,
            total_rounds=self._session.current_round,
            total_cases_executed=self._session.total_cases_executed,
            total_cases_passed=self._session.total_cases_passed,
            total_tokens_consumed=self._session.total_tokens_consumed,
            total_time_seconds=self._session.elapsed_seconds,
            initial_mastery=initial_mastery,
            final_mastery=final_mastery,
            mastery_improvements=improvements,
            overall_learning_efficiency=self._tracker.compute_overall_efficiency(),
            best_round=best_round,
            worst_round=worst_round,
            round_summaries=self._session.round_summaries,
            recommendations=recommendations,
        )

        # 回调
        for cb in self._callbacks:
            try:
                cb.on_session_complete(result)
            except Exception as e:
                logger.error("Session complete callback error: %s", e)

        return result

    def _generate_recommendations(
        self,
        final_mastery: dict[str, float],
        improvements: dict[str, float],
    ) -> list[str]:
        """基于训练结果生成后续建议。"""
        recommendations: list[str] = []

        # 未达标维度
        weak_dims = [
            d for d, m in final_mastery.items()
            if m < self._config.target_mastery
        ]
        if weak_dims:
            recommendations.append(
                f"以下维度仍未达标: {weak_dims}，建议继续针对性训练或降低目标阈值"
            )

        # 无进步维度
        stalled = [d for d, imp in improvements.items() if abs(imp) < 0.01]
        if stalled:
            recommendations.append(
                f"以下维度几乎无进步: {stalled}，可能需要不同的训练策略或更多样化的对抗样本"
            )

        # 退步维度
        degraded = [d for d, imp in improvements.items() if imp < -0.05]
        if degraded:
            recommendations.append(
                f"以下维度出现退步: {degraded}，建议检查训练样本质量或增加回顾频率"
            )

        # 效率建议
        efficiency = self._tracker.compute_overall_efficiency()
        if efficiency < 0.3:
            recommendations.append(
                "总体学习效率较低，建议调整样本难度分布或增大批次大小"
            )

        # 高效维度
        fast_improving = [d for d, imp in improvements.items() if imp > 0.2]
        if fast_improving:
            recommendations.append(
                f"以下维度进步显著: {fast_improving}，说明当前训练策略对它们有效"
            )

        if not recommendations:
            recommendations.append("训练表现良好，所有维度稳步提升")

        return recommendations

    # ----------------------------------------------------------
    # 序列化
    # ----------------------------------------------------------

    def serialize(self) -> dict:
        """序列化完整训练循环状态（用于暂停/恢复）。"""
        return {
            "config": self._config.to_dict(),
            "session": self._session.to_dict() if self._session else {},
            "tracker": self._tracker.serialize(),
            "convergence": self._convergence.serialize() if self._convergence else {},
        }

    # ----------------------------------------------------------
    # 工具方法
    # ----------------------------------------------------------

    @staticmethod
    def _noop_publish(event_name: str, payload: dict) -> None:
        """空操作的事件发布函数。"""
        pass

    @staticmethod
    def _run_async(coro) -> Any:
        """在同步上下文中运行异步协程。

        策略:
            1. 如果当前已有 event loop 运行，使用 nest_asyncio 或创建新线程
            2. 否则直接 asyncio.run()

        这样 TrainingLoop 本身保持同步接口（方便测试和外部调用），
        而 AdversarialGenerator 的 async API 也能正常工作。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # 当前已有 event loop（如在 Jupyter 或其他 async 环境中）
            # 创建新线程来运行协程
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        else:
            return asyncio.run(coro)
