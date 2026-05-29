"""
training/red_blue_arena.py — 红蓝对抗竞技场 (Red-Blue Arena)

双 Agent 竞争式对抗训练系统：红队（攻击者）不断生成更具挑战性的对抗样本，
蓝队（防御者）不断提升审稿分析能力；两者通过 ELO 评级系统驱动的竞争循环互相提升。

核心组件:
    1. EloRating — ELO 评分系统（带动态 K-factor 和自信度衰减）
    2. ArenaMatch — 单局对抗记录（红队出题 vs 蓝队答题的完整上下文）
    3. RedTeam — 红队（攻击者）：专注于生成能击败蓝队的对抗样本
    4. BlueTeam — 蓝队（防御者）：专注于提升对对抗样本的检测和分析能力
    5. ArenaOrchestrator — 竞技场编排器：协调红蓝对抗，管理赛季/赛事/积分

设计原则:
    - 博弈均衡驱动: 红蓝队的相互对抗驱动向纳什均衡演化
    - ELO 公平性: 参考国际象棋 ELO，确保评分反映真实能力
    - 动态 K-factor: 初期（不确定期）评分变化大，稳定后变化小
    - 多策略红队: 红队不仅强化已知弱点，还随机探索未知盲区
    - 蓝队适应性: 蓝队记忆过去的失败模式并调整策略
    - 赛季制: 周期性重置部分 ELO，防止锁死
    - 可观测性: 每局对抗完整记录，支持事后分析
    - Kill Switch 守卫: OFF 时所有方法返回空/默认值

Kill Switch: SCHOLAR_GODEL_ADVERSARIAL_TRAINING
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from training.weakness_analyzer import (
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
    LearningCurveTracker,
)
from core.godel_config import (
    GODEL_ADVERSARIAL_TRAINING_ENABLED,
    GODEL_ADVERSARIAL_RED_TEAM_ENABLED,
    GODEL_ADVERSARIAL_BLUE_TEAM_ENABLED,
    GODEL_ADVERSARIAL_ELO_ENABLED,
    GODEL_ADVERSARIAL_SEASON_ENABLED,
)
from core.event_bus import Event, EventBus, EventType

logger = logging.getLogger(__name__)


# ==============================================================
# Kill Switch (delegate to godel_config)
# ==============================================================

ADVERSARIAL_TRAINING_ENABLED: bool = GODEL_ADVERSARIAL_TRAINING_ENABLED
"""Backward-compatible alias. Actual source of truth is godel_config."""


# ==============================================================
# EventBus Mixin
# ==============================================================

class _EventBusMixin:
    """EventBus 集成 Mixin——为 Arena 组件提供统一的事件发布能力。

    设计原则:
        - 可选集成: 如果未提供 EventBus 则所有 emit 静默跳过
        - 零开销: 无 bus 时不创建 Event 对象
        - 隔离性: 事件发布失败不影响主流程
    """

    _event_bus: Optional[EventBus] = None
    _event_source: str = "arena"

    def attach_event_bus(self, bus: EventBus, source: str = "") -> None:
        """挂载 EventBus 实例。"""
        self._event_bus = bus
        if source:
            self._event_source = source

    def _emit_event(
        self,
        event_type: EventType,
        turn: int = 0,
        **payload,
    ) -> None:
        """安全发布事件。无 bus 时静默跳过。"""
        if self._event_bus is None:
            return
        try:
            self._event_bus.emit(
                event_type=event_type,
                source=self._event_source,
                turn=turn,
                **payload,
            )
        except Exception:
            # 事件发布失败不影响主流程
            pass


# ==============================================================
# ELO 评分系统
# ==============================================================

@dataclass
class EloSnapshot:
    """某一时刻的 ELO 评分快照。"""
    rating: float
    timestamp: float
    match_id: str = ""
    reason: str = ""


class EloRating(_EventBusMixin):
    """ELO 评分系统——带动态 K-factor 和置信度衰减。

    参考国际象棋 ELO 系统，但做了以下适配:
        - 动态 K-factor: 新选手 K=40（快速校准），稳定后 K=16（精细调整）
        - 置信度衰减: 长时间不参赛 → 评分自信度下降 → K-factor 暂时升高
        - 层级约束: 红蓝队的 ELO 差异有上限，防止一方远远甩开另一方
        - 赛季重置: 支持部分重置（向均值回归），防止评分锁死

    公式:
        期望胜率: E_a = 1 / (1 + 10^((R_b - R_a) / 400))
        评分更新: R_a' = R_a + K * (S_a - E_a)
        其中 S_a = 实际结果 (1=胜, 0.5=平, 0=负)

    K-factor 动态规则:
        - 前 30 局: K = 40 (快速校准)
        - 31-100 局: K = 24 (中速调整)
        - 100+ 局: K = 16 (精细调整)
        - 长时间不活跃: K 临时提升至 32

    类比:
        - 红队 ELO 高 → 红队生成的对抗样本确实难
        - 蓝队 ELO 高 → 蓝队确实能识别各种缺陷
        - 两者接近 → 博弈接近均衡，训练效果最好
    """

    # ELO 常量
    DEFAULT_RATING: float = 1500.0
    BASE_K_FACTORS: list[tuple[int, float]] = [
        (30, 40.0),    # 前 30 局: 快速校准
        (100, 24.0),   # 31-100 局: 中速
    ]
    STABLE_K: float = 16.0           # 100+ 局: 精细调整
    INACTIVITY_K_BOOST: float = 32.0  # 不活跃时临时 K
    INACTIVITY_THRESHOLD_SECONDS: float = 86400.0 * 7  # 7 天不活跃
    MAX_RATING_GAP: float = 600.0     # 红蓝最大 ELO 差
    SEASON_REGRESSION_FACTOR: float = 0.3  # 赛季重置回归系数

    def __init__(
        self,
        initial_rating: float = DEFAULT_RATING,
        match_count: int = 0,
    ):
        self._rating: float = initial_rating
        self._match_count: int = match_count
        self._last_active: float = time.time()
        self._peak_rating: float = initial_rating
        self._floor_rating: float = initial_rating
        self._history: list[EloSnapshot] = []
        self._streak: int = 0  # 正=连胜, 负=连败

    # ----------------------------------------------------------
    # 公开属性
    # ----------------------------------------------------------

    @property
    def rating(self) -> float:
        return self._rating

    @property
    def match_count(self) -> int:
        return self._match_count

    @property
    def peak_rating(self) -> float:
        return self._peak_rating

    @property
    def floor_rating(self) -> float:
        return self._floor_rating

    @property
    def streak(self) -> int:
        return self._streak

    @property
    def history(self) -> list[EloSnapshot]:
        return list(self._history)

    @property
    def is_provisional(self) -> bool:
        """是否仍处于校准期（前 30 局）。"""
        return self._match_count < 30

    @property
    def confidence(self) -> float:
        """评分置信度 (0~1)。基于局数和活跃度。"""
        # 局数贡献: 30 局达到 0.7，100 局达到 0.95
        count_conf = 1.0 - math.exp(-self._match_count / 50.0)
        # 活跃度贡献: 最近活跃 → 1.0，7天不活跃 → 0.5
        inactive_seconds = time.time() - self._last_active
        activity_conf = max(0.5, 1.0 - inactive_seconds / (self.INACTIVITY_THRESHOLD_SECONDS * 2))
        return min(1.0, count_conf * activity_conf)

    # ----------------------------------------------------------
    # 核心方法
    # ----------------------------------------------------------

    def expected_score(self, opponent_rating: float) -> float:
        """计算对阵对手的期望胜率。

        E_a = 1 / (1 + 10^((R_b - R_a) / 400))
        """
        exponent = (opponent_rating - self._rating) / 400.0
        return 1.0 / (1.0 + math.pow(10, exponent))

    def update(
        self,
        actual_score: float,
        opponent_rating: float,
        match_id: str = "",
    ) -> float:
        """根据对局结果更新评分。

        Args:
            actual_score: 实际结果 (1.0=胜, 0.5=平, 0.0=负)
            opponent_rating: 对手评分
            match_id: 对局 ID

        Returns:
            评分变化量 (delta)
        """
        if not GODEL_ADVERSARIAL_ELO_ENABLED:
            return 0.0

        expected = self.expected_score(opponent_rating)
        k = self._get_k_factor()
        delta = k * (actual_score - expected)

        # 更新评分
        old_rating = self._rating
        self._rating += delta
        self._rating = max(100.0, self._rating)  # 最低 100

        # 更新统计
        self._match_count += 1
        self._last_active = time.time()
        self._peak_rating = max(self._peak_rating, self._rating)
        self._floor_rating = min(self._floor_rating, self._rating)

        # 连胜/连败追踪
        if actual_score > 0.5:
            self._streak = max(0, self._streak) + 1
        elif actual_score < 0.5:
            self._streak = min(0, self._streak) - 1
        else:
            self._streak = 0

        # 记录历史
        self._history.append(EloSnapshot(
            rating=self._rating,
            timestamp=time.time(),
            match_id=match_id,
            reason=f"score={actual_score:.1f}, expected={expected:.3f}, K={k:.1f}, delta={delta:+.1f}",
        ))

        # 限制历史长度
        if len(self._history) > 500:
            self._history = self._history[-300:]

        # 发布 ELO 更新事件
        self._emit_event(
            EventType.ARENA_ELO_UPDATED,
            entity=self._event_source,
            old_rating=round(old_rating, 1),
            new_rating=round(self._rating, 1),
            delta=round(delta, 2),
            match_id=match_id,
            k_factor=round(k, 1),
        )

        return delta

    def season_reset(self, target_rating: Optional[float] = None) -> float:
        """赛季重置——向目标评分回归。

        Args:
            target_rating: 回归目标（默认 DEFAULT_RATING）

        Returns:
            重置后的评分
        """
        target = target_rating or self.DEFAULT_RATING
        old_rating = self._rating
        self._rating = self._rating + self.SEASON_REGRESSION_FACTOR * (target - self._rating)
        self._streak = 0

        self._history.append(EloSnapshot(
            rating=self._rating,
            timestamp=time.time(),
            reason=f"season_reset: {old_rating:.0f} → {self._rating:.0f}",
        ))

        return self._rating

    def get_rating_trend(self, last_n: int = 10) -> float:
        """获取最近 N 局的评分趋势（正=上升，负=下降）。"""
        if len(self._history) < 2:
            return 0.0
        recent = self._history[-last_n:]
        if len(recent) < 2:
            return 0.0
        return recent[-1].rating - recent[0].rating

    # ----------------------------------------------------------
    # 序列化
    # ----------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "rating": round(self._rating, 2),
            "match_count": self._match_count,
            "last_active": self._last_active,
            "peak_rating": round(self._peak_rating, 2),
            "floor_rating": round(self._floor_rating, 2),
            "streak": self._streak,
            "confidence": round(self.confidence, 4),
            "is_provisional": self.is_provisional,
            "history_length": len(self._history),
            "recent_history": [
                {"rating": round(s.rating, 1), "timestamp": s.timestamp, "reason": s.reason}
                for s in self._history[-20:]
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EloRating":
        elo = cls(
            initial_rating=data.get("rating", cls.DEFAULT_RATING),
            match_count=data.get("match_count", 0),
        )
        elo._last_active = data.get("last_active", time.time())
        elo._peak_rating = data.get("peak_rating", elo._rating)
        elo._floor_rating = data.get("floor_rating", elo._rating)
        elo._streak = data.get("streak", 0)
        # 恢复历史（简化版，不恢复完整历史）
        for h in data.get("recent_history", []):
            elo._history.append(EloSnapshot(
                rating=h.get("rating", elo._rating),
                timestamp=h.get("timestamp", 0.0),
                reason=h.get("reason", ""),
            ))
        return elo

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _get_k_factor(self) -> float:
        """根据局数和活跃度计算动态 K-factor。"""
        # 不活跃惩罚
        inactive_seconds = time.time() - self._last_active
        if inactive_seconds > self.INACTIVITY_THRESHOLD_SECONDS:
            return self.INACTIVITY_K_BOOST

        # 根据局数选择 K
        for threshold, k in self.BASE_K_FACTORS:
            if self._match_count < threshold:
                return k
        return self.STABLE_K


# ==============================================================
# 对抗局记录
# ==============================================================

class MatchOutcome(str, Enum):
    """对局结果。"""
    RED_WIN = "red_win"
    """红队胜（蓝队未能检测出缺陷）。"""

    BLUE_WIN = "blue_win"
    """蓝队胜（成功检测出红队设置的缺陷）。"""

    DRAW = "draw"
    """平局（部分检测正确，但未完全覆盖）。"""

    INVALID = "invalid"
    """无效对局（红队生成的样本质量不合格）。"""

    ERROR = "error"
    """执行错误（非正常终局）。"""


@dataclass
class ArenaMatch:
    """单局红蓝对抗记录。

    完整记录一次红队出题 → 蓝队答题的全过程，包括:
        - 红队生成的对抗样本（含攻击策略）
        - 蓝队的分析结果（含防御策略）
        - 评分变化
        - 质量评估

    每局对抗可以视为红蓝之间的一次信息博弈:
        红队目标: 生成蓝队无法检测的缺陷
        蓝队目标: 检测并正确分析红队设置的所有缺陷
    """
    match_id: str = ""
    season: int = 1
    round_in_season: int = 0
    timestamp: float = field(default_factory=time.time)

    # 红队出题
    challenge: Optional[AdversarialCase] = None
    red_strategy: str = ""
    """红队的攻击策略描述（如"exploit weak IV detection"）。"""

    red_difficulty_target: DifficultyLevel = DifficultyLevel.MEDIUM
    red_dimension_target: WeaknessDimension = WeaknessDimension.METHODOLOGY_ANALYSIS

    # 蓝队答题
    blue_findings: list[dict] = field(default_factory=list)
    """蓝队产出的 findings。"""

    blue_score: float = 0.0
    """蓝队在此局的得分 (0~1)。"""

    blue_strategy: str = ""
    """蓝队的防御策略描述。"""

    matched_gold_count: int = 0
    total_gold_count: int = 0

    # 对局结果
    outcome: MatchOutcome = MatchOutcome.DRAW
    outcome_details: str = ""

    # ELO 变化
    red_elo_before: float = 0.0
    red_elo_after: float = 0.0
    blue_elo_before: float = 0.0
    blue_elo_after: float = 0.0

    # 质量
    challenge_quality_score: float = 0.0
    """红队生成样本的质量分 (0~1)。"""

    analysis_depth_score: float = 0.0
    """蓝队分析的深度分 (0~1)。"""

    # 时间
    generation_time_seconds: float = 0.0
    execution_time_seconds: float = 0.0

    # 错误
    error: Optional[str] = None

    def __post_init__(self):
        if not self.match_id:
            content = f"match_{self.season}_{self.round_in_season}_{self.timestamp}"
            self.match_id = hashlib.md5(content.encode()).hexdigest()[:16]

    @property
    def is_valid(self) -> bool:
        """对局是否有效（非错误、非无效样本）。"""
        return self.outcome not in (MatchOutcome.INVALID, MatchOutcome.ERROR)

    @property
    def red_elo_delta(self) -> float:
        return self.red_elo_after - self.red_elo_before

    @property
    def blue_elo_delta(self) -> float:
        return self.blue_elo_after - self.blue_elo_before

    def to_dict(self) -> dict:
        return {
            "match_id": self.match_id,
            "season": self.season,
            "round_in_season": self.round_in_season,
            "timestamp": self.timestamp,
            "challenge_id": self.challenge.case_id if self.challenge else "",
            "challenge_type": self.challenge.challenge_type.value if self.challenge else "",
            "red_strategy": self.red_strategy,
            "red_difficulty_target": self.red_difficulty_target.value,
            "red_dimension_target": self.red_dimension_target.value,
            "blue_findings_count": len(self.blue_findings),
            "blue_score": round(self.blue_score, 4),
            "blue_strategy": self.blue_strategy,
            "matched_gold_count": self.matched_gold_count,
            "total_gold_count": self.total_gold_count,
            "outcome": self.outcome.value,
            "outcome_details": self.outcome_details,
            "red_elo_before": round(self.red_elo_before, 1),
            "red_elo_after": round(self.red_elo_after, 1),
            "blue_elo_before": round(self.blue_elo_before, 1),
            "blue_elo_after": round(self.blue_elo_after, 1),
            "challenge_quality_score": round(self.challenge_quality_score, 3),
            "analysis_depth_score": round(self.analysis_depth_score, 3),
            "generation_time_seconds": round(self.generation_time_seconds, 3),
            "execution_time_seconds": round(self.execution_time_seconds, 3),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ArenaMatch":
        try:
            outcome = MatchOutcome(data.get("outcome", "draw"))
        except ValueError:
            outcome = MatchOutcome.DRAW
        try:
            difficulty = DifficultyLevel(data.get("red_difficulty_target", "medium"))
        except ValueError:
            difficulty = DifficultyLevel.MEDIUM
        try:
            dimension = WeaknessDimension(data.get("red_dimension_target", "methodology_analysis"))
        except ValueError:
            dimension = WeaknessDimension.METHODOLOGY_ANALYSIS

        match = cls(
            match_id=data.get("match_id", ""),
            season=data.get("season", 1),
            round_in_season=data.get("round_in_season", 0),
            timestamp=data.get("timestamp", time.time()),
            red_strategy=data.get("red_strategy", ""),
            red_difficulty_target=difficulty,
            red_dimension_target=dimension,
            blue_score=data.get("blue_score", 0.0),
            blue_strategy=data.get("blue_strategy", ""),
            matched_gold_count=data.get("matched_gold_count", 0),
            total_gold_count=data.get("total_gold_count", 0),
            outcome=outcome,
            outcome_details=data.get("outcome_details", ""),
            red_elo_before=data.get("red_elo_before", 0.0),
            red_elo_after=data.get("red_elo_after", 0.0),
            blue_elo_before=data.get("blue_elo_before", 0.0),
            blue_elo_after=data.get("blue_elo_after", 0.0),
            challenge_quality_score=data.get("challenge_quality_score", 0.0),
            analysis_depth_score=data.get("analysis_depth_score", 0.0),
            generation_time_seconds=data.get("generation_time_seconds", 0.0),
            execution_time_seconds=data.get("execution_time_seconds", 0.0),
            error=data.get("error"),
        )
        return match


# ==============================================================
# 红队策略
# ==============================================================

class RedStrategy(str, Enum):
    """红队攻击策略。"""
    EXPLOIT_WEAKNESS = "exploit_weakness"
    """利用已知弱点——针对蓝队 pass_rate 最低的维度。"""

    ESCALATE_DIFFICULTY = "escalate_difficulty"
    """升级难度——在蓝队已较强的维度提升难度等级。"""

    EXPLORE_BLIND_SPOT = "explore_blind_spot"
    """探索盲区——随机选择未充分测试的维度/挑战类型。"""

    VARIANT_ATTACK = "variant_attack"
    """变体攻击——基于历史成功案例生成变体。"""

    COMPOUND_CHALLENGE = "compound_challenge"
    """复合挑战——同时包含多个维度的缺陷。"""

    ADAPTIVE_COUNTER = "adaptive_counter"
    """适应性反击——针对蓝队最近的进步设置特殊陷阱。"""


# ==============================================================
# 红队 (攻击者)
# ==============================================================

class RedTeam(_EventBusMixin):
    """红队——对抗样本攻击者。

    红队的核心目标是生成蓝队无法正确分析的对抗样本。
    通过多种攻击策略的组合，最大化对蓝队的挑战:

    策略选择逻辑:
        1. 如果蓝队有明显弱点 → EXPLOIT_WEAKNESS (40%)
        2. 如果蓝队最近进步显著 → ESCALATE_DIFFICULTY (20%)
        3. 如果有未探索维度 → EXPLORE_BLIND_SPOT (15%)
        4. 如果有历史成功案例 → VARIANT_ATTACK (15%)
        5. 如果蓝队全面较强 → COMPOUND_CHALLENGE (10%)

    红队同时维护:
        - 成功案例库: 记录过去击败蓝队的案例模式
        - 策略效果统计: 哪种策略对当前蓝队最有效
        - 攻击分布: 确保不会过度集中在单一维度
    """

    def __init__(
        self,
        generator: Optional[AdversarialGenerator] = None,
        elo: Optional[EloRating] = None,
    ):
        self._generator = generator or AdversarialGenerator()
        self._elo = elo or EloRating()
        self._strategy_history: list[tuple[RedStrategy, bool]] = []
        self._success_patterns: list[dict] = []  # 成功击败蓝队的模式
        self._attack_distribution: dict[str, int] = defaultdict(int)
        self._total_attacks: int = 0
        self._wins: int = 0
        self._losses: int = 0
        self._draws: int = 0

    # ----------------------------------------------------------
    # 公开属性
    # ----------------------------------------------------------

    @property
    def elo(self) -> EloRating:
        return self._elo

    @property
    def generator(self) -> AdversarialGenerator:
        return self._generator

    @property
    def total_attacks(self) -> int:
        return self._total_attacks

    @property
    def win_rate(self) -> float:
        total = self._wins + self._losses + self._draws
        if total == 0:
            return 0.0
        return self._wins / total

    @property
    def strategy_effectiveness(self) -> dict[str, float]:
        """各策略的胜率统计。"""
        stats: dict[str, list[bool]] = defaultdict(list)
        for strategy, won in self._strategy_history:
            stats[strategy.value].append(won)
        return {
            s: (sum(results) / len(results)) if results else 0.0
            for s, results in stats.items()
        }

    # ----------------------------------------------------------
    # 核心方法
    # ----------------------------------------------------------

    def select_strategy(
        self,
        blue_weakness_profile: Optional[WeaknessProfile] = None,
        blue_recent_improvement: float = 0.0,
        explored_dimensions: Optional[set[str]] = None,
    ) -> RedStrategy:
        """选择攻击策略。

        策略选择基于当前博弈态势:
            - 蓝队有弱点 → 利用弱点
            - 蓝队在进步 → 升级难度
            - 有未探索维度 → 探索盲区
            - 有成功模式 → 变体攻击

        Args:
            blue_weakness_profile: 蓝队的弱点画像
            blue_recent_improvement: 蓝队最近的进步幅度
            explored_dimensions: 已探索的维度集合

        Returns:
            选择的攻击策略
        """
        if not GODEL_ADVERSARIAL_RED_TEAM_ENABLED:
            return RedStrategy.EXPLOIT_WEAKNESS

        # 计算各策略的权重
        weights: dict[RedStrategy, float] = {
            RedStrategy.EXPLOIT_WEAKNESS: 0.4,
            RedStrategy.ESCALATE_DIFFICULTY: 0.2,
            RedStrategy.EXPLORE_BLIND_SPOT: 0.15,
            RedStrategy.VARIANT_ATTACK: 0.15,
            RedStrategy.COMPOUND_CHALLENGE: 0.1,
        }

        # 动态调整权重

        # 如果蓝队没有明显弱点，降低 EXPLOIT_WEAKNESS 权重
        if blue_weakness_profile:
            top_k = blue_weakness_profile.get_top_k(3)
            if not top_k:
                weights[RedStrategy.EXPLOIT_WEAKNESS] = 0.1
                weights[RedStrategy.EXPLORE_BLIND_SPOT] = 0.35
                weights[RedStrategy.COMPOUND_CHALLENGE] = 0.25
        else:
            weights[RedStrategy.EXPLOIT_WEAKNESS] = 0.1
            weights[RedStrategy.EXPLORE_BLIND_SPOT] = 0.4

        # 如果蓝队最近进步显著，提升 ESCALATE 和 ADAPTIVE 权重
        if blue_recent_improvement > 0.1:
            weights[RedStrategy.ESCALATE_DIFFICULTY] = 0.35
            weights[RedStrategy.ADAPTIVE_COUNTER] = 0.15
            weights[RedStrategy.EXPLOIT_WEAKNESS] = 0.2

        # 如果有未探索维度，提升 EXPLORE 权重
        all_dims = set(d.value for d in WeaknessDimension)
        explored = explored_dimensions or set()
        unexplored_ratio = len(all_dims - explored) / max(1, len(all_dims))
        if unexplored_ratio > 0.3:
            weights[RedStrategy.EXPLORE_BLIND_SPOT] = max(
                weights[RedStrategy.EXPLORE_BLIND_SPOT], 0.3
            )

        # 如果有成功模式可复用，提升 VARIANT 权重
        if self._success_patterns:
            weights[RedStrategy.VARIANT_ATTACK] = max(
                weights[RedStrategy.VARIANT_ATTACK], 0.25
            )

        # 归一化并采样
        total_weight = sum(weights.values())
        strategies = list(weights.keys())
        probs = [weights[s] / total_weight for s in strategies]

        chosen = random.choices(strategies, weights=probs, k=1)[0]
        return chosen

    async def generate_challenge(
        self,
        strategy: RedStrategy,
        blue_weakness_profile: Optional[WeaknessProfile] = None,
        target_dimension: Optional[WeaknessDimension] = None,
        target_difficulty: Optional[DifficultyLevel] = None,
    ) -> tuple[AdversarialCase, str]:
        """根据策略生成对抗样本。

        Args:
            strategy: 攻击策略
            blue_weakness_profile: 蓝队弱点画像
            target_dimension: 目标维度（可选）
            target_difficulty: 目标难度（可选）

        Returns:
            (生成的对抗样本, 策略描述)
        """
        if not GODEL_ADVERSARIAL_RED_TEAM_ENABLED:
            return AdversarialCase(), "disabled"

        strategy_desc = f"[Red:{strategy.value}]"

        # 如果提供了弱点画像，注入生成器
        if blue_weakness_profile:
            self._generator.set_weakness_profile(blue_weakness_profile)

        # 根据策略选择生成参数
        weakness = None
        challenge_type = None
        difficulty = target_difficulty
        context = ""

        if strategy == RedStrategy.EXPLOIT_WEAKNESS:
            # 利用已知弱点
            if blue_weakness_profile:
                top = blue_weakness_profile.get_top_k(3)
                if top:
                    weakness = random.choice(top)
                    target_dimension = weakness.dimension
                    strategy_desc += f" targeting dim={weakness.dimension.value}"
            if difficulty is None:
                difficulty = DifficultyLevel.MEDIUM

        elif strategy == RedStrategy.ESCALATE_DIFFICULTY:
            # 升级难度
            difficulty = target_difficulty or DifficultyLevel.HARD
            if target_dimension is None:
                # 选择蓝队较强的维度来升级
                if blue_weakness_profile:
                    dist = blue_weakness_profile.dimension_distribution()
                    # 选择最弱的维度（对蓝队最有挑战）
                    if dist:
                        sorted_dims = sorted(dist.items(), key=lambda x: x[1], reverse=True)
                        target_dimension = WeaknessDimension(sorted_dims[0][0])
            strategy_desc += f" difficulty={difficulty.value}"

        elif strategy == RedStrategy.EXPLORE_BLIND_SPOT:
            # 探索盲区
            all_dims = list(WeaknessDimension)
            target_dimension = target_dimension or random.choice(all_dims)
            difficulty = difficulty or DifficultyLevel.EASY  # 探索用低难度
            strategy_desc += f" exploring dim={target_dimension.value}"

        elif strategy == RedStrategy.VARIANT_ATTACK:
            # 变体攻击
            if self._success_patterns:
                pattern = random.choice(self._success_patterns[-10:])
                context = f"Generate variant of successful attack: {pattern.get('description', '')}"
                challenge_type = pattern.get("challenge_type")
                if challenge_type:
                    try:
                        challenge_type = ChallengeType(challenge_type)
                    except ValueError:
                        challenge_type = None
            difficulty = difficulty or DifficultyLevel.MEDIUM
            strategy_desc += " variant_of_success"

        elif strategy == RedStrategy.COMPOUND_CHALLENGE:
            # 复合挑战（较高难度）
            difficulty = difficulty or DifficultyLevel.HARD
            context = "Generate a compound challenge with multiple subtle issues across different dimensions"
            strategy_desc += " compound"

        elif strategy == RedStrategy.ADAPTIVE_COUNTER:
            # 适应性反击
            difficulty = difficulty or DifficultyLevel.HARD
            context = "Generate a challenge specifically designed to counter recent improvements in detection"
            strategy_desc += " adaptive_counter"

        # 调用生成器
        case = await self._generator.generate_challenge(
            weakness=weakness,
            challenge_type=challenge_type,
            difficulty=difficulty,
            context=context,
        )

        # 如果指定了维度，覆盖
        if target_dimension:
            case.target_dimension = target_dimension

        # 记录攻击分布
        self._attack_distribution[case.target_dimension.value] += 1
        self._total_attacks += 1

        # 发布红队挑战事件
        self._emit_event(
            EventType.ARENA_RED_CHALLENGE,
            strategy=strategy.value,
            dimension=case.target_dimension.value,
            difficulty=case.difficulty.value,
            challenge_type=case.challenge_type.value,
            case_id=case.case_id,
        )

        return case, strategy_desc

    def record_result(self, outcome: MatchOutcome, match: ArenaMatch) -> None:
        """记录对局结果。

        Args:
            outcome: 对局结果
            match: 完整对局记录
        """
        if not GODEL_ADVERSARIAL_RED_TEAM_ENABLED:
            return

        won = outcome == MatchOutcome.RED_WIN
        lost = outcome == MatchOutcome.BLUE_WIN

        # 更新胜负统计
        if won:
            self._wins += 1
        elif lost:
            self._losses += 1
        else:
            self._draws += 1

        # 记录策略效果
        try:
            strategy = RedStrategy(match.red_strategy.replace("[Red:", "").split("]")[0])
        except (ValueError, IndexError):
            strategy = RedStrategy.EXPLOIT_WEAKNESS
        self._strategy_history.append((strategy, won))

        # 如果红队赢了，记录成功模式
        if won and match.challenge:
            self._success_patterns.append({
                "challenge_type": match.challenge.challenge_type.value,
                "dimension": match.challenge.target_dimension.value,
                "difficulty": match.challenge.difficulty.value,
                "description": match.red_strategy,
                "timestamp": time.time(),
            })
            # 保留最近 50 个成功模式
            if len(self._success_patterns) > 50:
                self._success_patterns = self._success_patterns[-50:]

        # 限制策略历史
        if len(self._strategy_history) > 200:
            self._strategy_history = self._strategy_history[-200:]

    def get_stats(self) -> dict:
        """获取红队统计信息。"""
        return {
            "elo_rating": round(self._elo.rating, 1),
            "elo_confidence": round(self._elo.confidence, 3),
            "total_attacks": self._total_attacks,
            "wins": self._wins,
            "losses": self._losses,
            "draws": self._draws,
            "win_rate": round(self.win_rate, 4),
            "strategy_effectiveness": self.strategy_effectiveness,
            "attack_distribution": dict(self._attack_distribution),
            "success_patterns_count": len(self._success_patterns),
            "elo_trend": round(self._elo.get_rating_trend(), 1),
        }

    def serialize(self) -> dict:
        return {
            "elo": self._elo.to_dict(),
            "total_attacks": self._total_attacks,
            "wins": self._wins,
            "losses": self._losses,
            "draws": self._draws,
            "success_patterns": self._success_patterns[-30:],
            "attack_distribution": dict(self._attack_distribution),
            "strategy_history": [
                (s.value, w) for s, w in self._strategy_history[-100:]
            ],
            "generator": self._generator.serialize(),
        }

    @classmethod
    def from_dict(cls, data: dict, generator: Optional[AdversarialGenerator] = None) -> "RedTeam":
        elo = EloRating.from_dict(data.get("elo", {}))
        gen = generator or AdversarialGenerator.deserialize(data.get("generator", {}))
        team = cls(generator=gen, elo=elo)
        team._total_attacks = data.get("total_attacks", 0)
        team._wins = data.get("wins", 0)
        team._losses = data.get("losses", 0)
        team._draws = data.get("draws", 0)
        team._success_patterns = data.get("success_patterns", [])
        team._attack_distribution = defaultdict(int, data.get("attack_distribution", {}))
        # 恢复策略历史
        for item in data.get("strategy_history", []):
            if isinstance(item, (list, tuple)) and len(item) == 2:
                try:
                    team._strategy_history.append((RedStrategy(item[0]), item[1]))
                except ValueError:
                    pass
        return team


# ==============================================================
# 蓝队策略
# ==============================================================

class BlueStrategy(str, Enum):
    """蓝队防御策略。"""
    STANDARD_REVIEW = "standard_review"
    """标准审稿——使用默认审稿流程。"""

    DEEP_METHODOLOGY = "deep_methodology"
    """深度方法论审查——额外关注研究设计和识别策略。"""

    STATISTICAL_FOCUS = "statistical_focus"
    """统计聚焦——额外验证统计方法和推断的正确性。"""

    CROSS_REFERENCE = "cross_reference"
    """交叉验证——重点检查表格/图/文本之间的一致性。"""

    ADVERSARIAL_AWARE = "adversarial_aware"
    """对抗意识——假设论文存在隐蔽缺陷，格外谨慎。"""

    MULTI_PASS = "multi_pass"
    """多遍审查——先概览再逐段深入。"""


# ==============================================================
# 蓝队 (防御者)
# ==============================================================

@runtime_checkable
class BlueTeamExecutor(Protocol):
    """蓝队执行器协议——执行审稿并返回分析结果。"""

    def execute_review(self, case: AdversarialCase, strategy: BlueStrategy) -> dict:
        """执行审稿分析。

        Args:
            case: 对抗样本
            strategy: 防御策略

        Returns:
            分析结果字典，包含:
                - findings: list[dict] — 发现的问题
                - score: float — 置信度分数
                - analysis_time: float — 分析耗时
        """
        ...


class BlueTeam(_EventBusMixin):
    """蓝队——审稿分析防御者。

    蓝队代表 ScholarAgent 的审稿分析能力。其目标是:
        1. 正确检测红队设置的所有缺陷
        2. 提供准确的分析和建议
        3. 不产生误报（false positive）

    蓝队维护:
        - 失败记忆: 记录过去未能检测到的模式
        - 维度强度: 各维度的检测能力评估
        - 策略适配: 根据红队的攻击模式调整防御策略
        - 学习曲线: 追踪各维度的学习进展
    """

    def __init__(
        self,
        executor: Optional[BlueTeamExecutor] = None,
        elo: Optional[EloRating] = None,
    ):
        self._executor = executor
        self._elo = elo or EloRating()
        self._failure_memory: list[dict] = []  # 失败模式记忆
        self._dimension_strength: dict[str, float] = {}  # 各维度的强度评估
        self._strategy_history: list[tuple[BlueStrategy, bool]] = []
        self._total_defenses: int = 0
        self._wins: int = 0
        self._losses: int = 0
        self._draws: int = 0
        self._false_positive_count: int = 0
        self._learning_curve: LearningCurveTracker = LearningCurveTracker()

    # ----------------------------------------------------------
    # 公开属性
    # ----------------------------------------------------------

    @property
    def elo(self) -> EloRating:
        return self._elo

    @property
    def total_defenses(self) -> int:
        return self._total_defenses

    @property
    def win_rate(self) -> float:
        total = self._wins + self._losses + self._draws
        if total == 0:
            return 0.0
        return self._wins / total

    @property
    def dimension_strength(self) -> dict[str, float]:
        return dict(self._dimension_strength)

    @property
    def false_positive_rate(self) -> float:
        """误报率。"""
        if self._total_defenses == 0:
            return 0.0
        return self._false_positive_count / self._total_defenses

    @property
    def strategy_effectiveness(self) -> dict[str, float]:
        """各策略的胜率统计。"""
        stats: dict[str, list[bool]] = defaultdict(list)
        for strategy, won in self._strategy_history:
            stats[strategy.value].append(won)
        return {
            s: (sum(results) / len(results)) if results else 0.0
            for s, results in stats.items()
        }

    # ----------------------------------------------------------
    # 核心方法
    # ----------------------------------------------------------

    def select_strategy(
        self,
        challenge: AdversarialCase,
        red_recent_strategy: Optional[str] = None,
    ) -> BlueStrategy:
        """选择防御策略。

        策略选择基于对红队攻击的分析:
            - 方法论类挑战 → DEEP_METHODOLOGY
            - 统计类挑战 → STATISTICAL_FOCUS
            - 数据一致性类 → CROSS_REFERENCE
            - 高难度/历史失败模式 → ADVERSARIAL_AWARE + MULTI_PASS
            - 默认 → STANDARD_REVIEW

        Args:
            challenge: 红队的对抗样本
            red_recent_strategy: 红队最近的攻击策略描述

        Returns:
            选择的防御策略
        """
        if not GODEL_ADVERSARIAL_BLUE_TEAM_ENABLED:
            return BlueStrategy.STANDARD_REVIEW

        # 基于挑战维度选择策略
        dim = challenge.target_dimension
        difficulty = challenge.difficulty

        # 高难度 → 对抗意识 + 多遍
        if difficulty in (DifficultyLevel.HARD, DifficultyLevel.EXPERT):
            # 50% 概率选择 ADVERSARIAL_AWARE，50% 选择 MULTI_PASS
            if random.random() < 0.5:
                return BlueStrategy.ADVERSARIAL_AWARE
            return BlueStrategy.MULTI_PASS

        # 根据维度匹配策略
        methodology_dims = {
            WeaknessDimension.METHODOLOGY_ANALYSIS,
            WeaknessDimension.CAUSAL_INFERENCE,
            WeaknessDimension.DID_ANALYSIS,
            WeaknessDimension.IV_ANALYSIS,
            WeaknessDimension.RDD_ANALYSIS,
        }
        statistical_dims = {
            WeaknessDimension.STATISTICAL_REASONING,
            WeaknessDimension.EVENT_STUDY,
            WeaknessDimension.PANEL_DATA,
        }
        consistency_dims = {
            WeaknessDimension.DATA_CONSISTENCY,
            WeaknessDimension.CROSS_SECTION_REASONING,
        }

        if dim in methodology_dims:
            return BlueStrategy.DEEP_METHODOLOGY
        elif dim in statistical_dims:
            return BlueStrategy.STATISTICAL_FOCUS
        elif dim in consistency_dims:
            return BlueStrategy.CROSS_REFERENCE
        else:
            return BlueStrategy.STANDARD_REVIEW

    def execute_defense(
        self,
        challenge: AdversarialCase,
        strategy: Optional[BlueStrategy] = None,
    ) -> tuple[list[dict], float]:
        """执行防御（审稿分析）。

        Args:
            challenge: 红队的对抗样本
            strategy: 防御策略（None = 自动选择）

        Returns:
            (findings, score) — 发现的问题列表和得分
        """
        if not GODEL_ADVERSARIAL_BLUE_TEAM_ENABLED:
            return [], 0.0

        if strategy is None:
            strategy = self.select_strategy(challenge)

        # 使用执行器（如果有）
        if self._executor:
            try:
                result = self._executor.execute_review(challenge, strategy)
                findings = result.get("findings", [])
                score = result.get("score", 0.0)
            except Exception as e:
                logger.warning("[BlueTeam] Executor failed: %s", e)
                findings = []
                score = 0.0
        else:
            # 无执行器时的 fallback 模拟
            findings, score = self._simulate_review(challenge, strategy)

        self._total_defenses += 1

        # 发布蓝队响应事件
        self._emit_event(
            EventType.ARENA_BLUE_RESPONSE,
            strategy=strategy.value if strategy else "auto",
            findings_count=len(findings),
            score=round(score, 4),
            dimension=challenge.target_dimension.value,
        )

        return findings, score

    def record_result(
        self,
        outcome: MatchOutcome,
        match: ArenaMatch,
    ) -> None:
        """记录对局结果。

        Args:
            outcome: 对局结果
            match: 完整对局记录
        """
        if not GODEL_ADVERSARIAL_BLUE_TEAM_ENABLED:
            return

        won = outcome == MatchOutcome.BLUE_WIN
        lost = outcome == MatchOutcome.RED_WIN

        # 更新胜负统计
        if won:
            self._wins += 1
        elif lost:
            self._losses += 1
        else:
            self._draws += 1

        # 记录策略效果
        try:
            strategy = BlueStrategy(match.blue_strategy)
        except ValueError:
            strategy = BlueStrategy.STANDARD_REVIEW
        self._strategy_history.append((strategy, won))

        # 如果蓝队输了，记录失败模式
        if lost and match.challenge:
            self._failure_memory.append({
                "challenge_type": match.challenge.challenge_type.value,
                "dimension": match.challenge.target_dimension.value,
                "difficulty": match.challenge.difficulty.value,
                "red_strategy": match.red_strategy,
                "blue_strategy": match.blue_strategy,
                "timestamp": time.time(),
                "match_id": match.match_id,
            })
            # 保留最近 100 个失败记忆
            if len(self._failure_memory) > 100:
                self._failure_memory = self._failure_memory[-100:]

        # 更新维度强度
        if match.challenge:
            dim_key = match.challenge.target_dimension.value
            current = self._dimension_strength.get(dim_key, 0.5)
            # 指数移动平均
            alpha = 0.2
            new_val = alpha * match.blue_score + (1 - alpha) * current
            self._dimension_strength[dim_key] = new_val

        # 更新学习曲线
        if match.challenge:
            # 计算该维度的累计尝试次数
            dim_key = match.challenge.target_dimension.value
            dim_attempts = sum(
                1 for m in self._strategy_history
            )
            self._learning_curve.record(
                dimension=match.challenge.target_dimension,
                difficulty=match.challenge.difficulty,
                pass_rate=match.blue_score,
                cumulative_attempts=dim_attempts,
            )

        # 限制策略历史
        if len(self._strategy_history) > 200:
            self._strategy_history = self._strategy_history[-200:]

    def get_weakness_dimensions(self, threshold: float = 0.4) -> list[str]:
        """获取蓝队的薄弱维度（强度低于阈值的维度）。"""
        weak: list[str] = []
        for dim, strength in self._dimension_strength.items():
            if strength < threshold:
                weak.append(dim)
        return weak

    def get_stats(self) -> dict:
        """获取蓝队统计信息。"""
        return {
            "elo_rating": round(self._elo.rating, 1),
            "elo_confidence": round(self._elo.confidence, 3),
            "total_defenses": self._total_defenses,
            "wins": self._wins,
            "losses": self._losses,
            "draws": self._draws,
            "win_rate": round(self.win_rate, 4),
            "false_positive_rate": round(self.false_positive_rate, 4),
            "dimension_strength": {k: round(v, 3) for k, v in self._dimension_strength.items()},
            "strategy_effectiveness": self.strategy_effectiveness,
            "failure_memory_count": len(self._failure_memory),
            "elo_trend": round(self._elo.get_rating_trend(), 1),
        }

    def serialize(self) -> dict:
        return {
            "elo": self._elo.to_dict(),
            "total_defenses": self._total_defenses,
            "wins": self._wins,
            "losses": self._losses,
            "draws": self._draws,
            "false_positive_count": self._false_positive_count,
            "failure_memory": self._failure_memory[-50:],
            "dimension_strength": self._dimension_strength,
            "strategy_history": [
                (s.value, w) for s, w in self._strategy_history[-100:]
            ],
            "learning_curve": self._learning_curve.serialize(),
        }

    @classmethod
    def from_dict(cls, data: dict, executor: Optional[BlueTeamExecutor] = None) -> "BlueTeam":
        elo = EloRating.from_dict(data.get("elo", {}))
        team = cls(executor=executor, elo=elo)
        team._total_defenses = data.get("total_defenses", 0)
        team._wins = data.get("wins", 0)
        team._losses = data.get("losses", 0)
        team._draws = data.get("draws", 0)
        team._false_positive_count = data.get("false_positive_count", 0)
        team._failure_memory = data.get("failure_memory", [])
        team._dimension_strength = data.get("dimension_strength", {})
        # 恢复策略历史
        for item in data.get("strategy_history", []):
            if isinstance(item, (list, tuple)) and len(item) == 2:
                try:
                    team._strategy_history.append((BlueStrategy(item[0]), item[1]))
                except ValueError:
                    pass
        # 恢复学习曲线
        lc_data = data.get("learning_curve")
        if lc_data:
            team._learning_curve = LearningCurveTracker.deserialize(lc_data)
        return team

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _simulate_review(
        self,
        challenge: AdversarialCase,
        strategy: BlueStrategy,
    ) -> tuple[list[dict], float]:
        """模拟审稿（当无真实执行器时的 fallback）。

        基于蓝队当前的维度强度和挑战难度，概率性地决定是否能检测到缺陷。
        """
        dim_key = challenge.target_dimension.value
        base_strength = self._dimension_strength.get(dim_key, 0.5)

        # 策略加成
        strategy_bonus = {
            BlueStrategy.STANDARD_REVIEW: 0.0,
            BlueStrategy.DEEP_METHODOLOGY: 0.1,
            BlueStrategy.STATISTICAL_FOCUS: 0.1,
            BlueStrategy.CROSS_REFERENCE: 0.1,
            BlueStrategy.ADVERSARIAL_AWARE: 0.15,
            BlueStrategy.MULTI_PASS: 0.12,
        }
        bonus = strategy_bonus.get(strategy, 0.0)

        # 难度惩罚
        difficulty_penalty = {
            DifficultyLevel.TRIVIAL: -0.2,
            DifficultyLevel.EASY: -0.1,
            DifficultyLevel.MEDIUM: 0.0,
            DifficultyLevel.HARD: 0.15,
            DifficultyLevel.EXPERT: 0.3,
        }
        penalty = difficulty_penalty.get(challenge.difficulty, 0.0)

        # 综合检测概率
        detection_prob = base_strength + bonus - penalty
        detection_prob = max(0.05, min(0.95, detection_prob))

        # 模拟检测
        if random.random() < detection_prob:
            # 成功检测：返回 gold findings 的子集
            n_gold = len(challenge.gold_findings)
            if n_gold > 0:
                # 检测到的数量
                detect_count = max(1, int(n_gold * detection_prob))
                detect_count = min(detect_count, n_gold)
                findings = challenge.gold_findings[:detect_count]
            else:
                findings = [{"category": "methodology", "description": "Simulated detection"}]
            score = detection_prob
        else:
            # 未能检测
            findings = []
            score = detection_prob * 0.3  # 还是给一点部分分

        return findings, score


# ==============================================================
# 评判器
# ==============================================================

class MatchJudge:
    """对局评判器——判定红蓝对抗的胜负。

    评判标准:
        1. 蓝队是否正确识别了 gold findings 中的核心问题
        2. 蓝队的分析是否准确（不是表面匹配）
        3. 蓝队是否有误报（发现了不存在的问题）

    评分规则:
        - 完全检测 (matched/total >= 0.8) → BLUE_WIN
        - 完全遗漏 (matched/total == 0) → RED_WIN
        - 部分检测 (0 < matched/total < 0.8) → DRAW
        - 红队样本质量不合格 → INVALID（不影响评分）
    """

    # 判定阈值
    BLUE_WIN_THRESHOLD: float = 0.8
    """蓝队胜利需要检测到 80% 以上的 gold findings。"""

    DRAW_THRESHOLD: float = 0.0
    """高于 0% 低于 80% 为平局。"""

    MIN_CHALLENGE_QUALITY: float = 0.3
    """红队样本最低质量分。"""

    def __init__(
        self,
        blue_win_threshold: float = BLUE_WIN_THRESHOLD,
        min_quality: float = MIN_CHALLENGE_QUALITY,
    ):
        self._blue_win_threshold = blue_win_threshold
        self._min_quality = min_quality

    def judge_match(
        self,
        challenge: AdversarialCase,
        blue_findings: list[dict],
        blue_score: float,
        challenge_quality: float = 1.0,
    ) -> tuple[MatchOutcome, float, float, str]:
        """评判一局对抗。

        Args:
            challenge: 红队的对抗样本
            blue_findings: 蓝队的分析结果
            blue_score: 蓝队的自评分数
            challenge_quality: 红队样本质量分

        Returns:
            (outcome, blue_match_score, red_match_score, details)
            blue_match_score: 蓝队在此局的 ELO 得分 (0/0.5/1)
            red_match_score: 红队在此局的 ELO 得分 (0/0.5/1)
        """
        # 质量检查
        if challenge_quality < self._min_quality:
            return (
                MatchOutcome.INVALID,
                0.5,  # 无效局双方不获得/失去
                0.5,
                f"Challenge quality too low: {challenge_quality:.2f} < {self._min_quality:.2f}",
            )

        # 计算匹配度
        gold_findings = challenge.gold_findings
        total_gold = len(gold_findings)

        if total_gold == 0:
            # 没有 gold standard，无法判定
            return (
                MatchOutcome.INVALID,
                0.5,
                0.5,
                "No gold findings to judge against",
            )

        # 匹配 gold findings（基于 category + 关键词匹配）
        matched = self._match_findings(gold_findings, blue_findings)
        match_ratio = matched / total_gold

        # 判定
        if match_ratio >= self._blue_win_threshold:
            outcome = MatchOutcome.BLUE_WIN
            blue_elo_score = 1.0
            red_elo_score = 0.0
            details = f"Blue detected {matched}/{total_gold} issues ({match_ratio:.0%})"
        elif match_ratio == 0.0 and len(blue_findings) == 0:
            outcome = MatchOutcome.RED_WIN
            blue_elo_score = 0.0
            red_elo_score = 1.0
            details = f"Blue failed to detect any of {total_gold} issues"
        elif match_ratio == 0.0 and len(blue_findings) > 0:
            # 蓝队有输出但全部是误报
            outcome = MatchOutcome.RED_WIN
            blue_elo_score = 0.1  # 给一点点安慰分
            red_elo_score = 0.9
            details = f"Blue produced {len(blue_findings)} findings but none matched gold"
        else:
            # 部分检测 → 平局（但有倾向性）
            outcome = MatchOutcome.DRAW
            # 按匹配度分配分数
            blue_elo_score = 0.3 + 0.4 * match_ratio  # 0.3~0.7
            red_elo_score = 1.0 - blue_elo_score
            details = f"Partial detection: {matched}/{total_gold} ({match_ratio:.0%})"

        return outcome, blue_elo_score, red_elo_score, details

    def _match_findings(
        self,
        gold_findings: list[dict],
        blue_findings: list[dict],
    ) -> int:
        """匹配蓝队发现与 gold standard。

        匹配策略（宽松匹配 + 语义相似性）:
            1. 精确匹配: category 相同 + description 关键词重叠度 > 50%
            2. 宽松匹配: category 兼容 + description 有核心关键词
        """
        if not gold_findings or not blue_findings:
            return 0

        matched = 0
        used_blue_indices: set[int] = set()

        for gold in gold_findings:
            gold_cat = gold.get("category", "").lower()
            gold_desc = gold.get("description", "").lower()
            gold_keywords = set(gold_desc.split())

            best_match_idx = -1
            best_overlap = 0.0

            for i, blue in enumerate(blue_findings):
                if i in used_blue_indices:
                    continue

                blue_cat = blue.get("category", "").lower()
                blue_desc = blue.get("description", "").lower()
                blue_keywords = set(blue_desc.split())

                # 类别匹配（宽松）
                cat_match = self._categories_compatible(gold_cat, blue_cat)
                if not cat_match:
                    continue

                # 关键词重叠度
                if gold_keywords and blue_keywords:
                    overlap = len(gold_keywords & blue_keywords) / max(1, len(gold_keywords))
                else:
                    overlap = 0.0

                if overlap > best_overlap:
                    best_overlap = overlap
                    best_match_idx = i

            # 阈值: 关键词重叠 > 20% 视为匹配
            if best_match_idx >= 0 and best_overlap >= 0.2:
                matched += 1
                used_blue_indices.add(best_match_idx)

        return matched

    def _categories_compatible(self, gold_cat: str, blue_cat: str) -> bool:
        """判断两个类别是否兼容（宽松匹配）。"""
        if gold_cat == blue_cat:
            return True

        # 兼容映射
        compatible_groups = [
            {"methodology", "methods", "research_design", "design"},
            {"statistics", "statistical", "inference", "econometrics"},
            {"logic", "logical", "coherence", "reasoning"},
            {"clarity", "writing", "presentation", "format"},
            {"citation", "references", "literature"},
            {"data", "consistency", "verification"},
        ]

        for group in compatible_groups:
            if gold_cat in group and blue_cat in group:
                return True

        # 部分匹配
        return gold_cat in blue_cat or blue_cat in gold_cat


# ==============================================================
# 赛季配置
# ==============================================================

@dataclass
class SeasonConfig:
    """赛季配置。"""
    matches_per_season: int = 50
    """每赛季对局数。"""

    elo_reset_on_season_end: bool = True
    """赛季结束时是否重置 ELO（部分回归）。"""

    escalation_per_season: float = 0.1
    """每赛季的平均难度提升幅度。"""

    min_matches_for_strategy_switch: int = 5
    """最少对局数后才允许切换策略。"""

    balance_check_interval: int = 10
    """每隔多少局检查红蓝平衡性。"""

    max_elo_gap: float = 400.0
    """最大允许的 ELO 差距（超过时介入平衡）。"""


@dataclass
class SeasonSummary:
    """赛季总结。"""
    season_number: int = 0
    total_matches: int = 0
    red_wins: int = 0
    blue_wins: int = 0
    draws: int = 0
    invalid_matches: int = 0

    red_elo_start: float = 0.0
    red_elo_end: float = 0.0
    blue_elo_start: float = 0.0
    blue_elo_end: float = 0.0

    avg_challenge_quality: float = 0.0
    avg_analysis_depth: float = 0.0

    dimension_coverage: dict[str, int] = field(default_factory=dict)
    difficulty_distribution: dict[str, int] = field(default_factory=dict)

    started_at: float = field(default_factory=time.time)
    ended_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "season_number": self.season_number,
            "total_matches": self.total_matches,
            "red_wins": self.red_wins,
            "blue_wins": self.blue_wins,
            "draws": self.draws,
            "invalid_matches": self.invalid_matches,
            "red_elo_change": round(self.red_elo_end - self.red_elo_start, 1),
            "blue_elo_change": round(self.blue_elo_end - self.blue_elo_start, 1),
            "avg_challenge_quality": round(self.avg_challenge_quality, 3),
            "avg_analysis_depth": round(self.avg_analysis_depth, 3),
            "dimension_coverage": self.dimension_coverage,
            "difficulty_distribution": self.difficulty_distribution,
        }


# ==============================================================
# 竞技场编排器
# ==============================================================

class ArenaOrchestrator(_EventBusMixin):
    """竞技场编排器——协调红蓝对抗的核心引擎。

    职责:
        1. 组织红蓝对抗（赛季制/锦标赛制）
        2. 执行评判并更新 ELO
        3. 监控红蓝平衡性，必要时介入调整
        4. 发布事件（对局结果、赛季总结等）
        5. 持久化对抗历史

    运行模式:
        - 赛季制: 每赛季 N 局，赛季结束时 ELO 部分回归，防止锁死
        - 自动平衡: 如果一方 ELO 远超另一方，自动调整难度/策略
        - 可暂停/恢复: 所有状态可序列化

    使用方式:
        orchestrator = ArenaOrchestrator(red_team, blue_team)

        # 运行一局
        match = await orchestrator.run_match()

        # 运行一赛季
        summary = await orchestrator.run_season()

        # 获取统计
        stats = orchestrator.get_arena_stats()
    """

    def __init__(
        self,
        red_team: Optional[RedTeam] = None,
        blue_team: Optional[BlueTeam] = None,
        judge: Optional[MatchJudge] = None,
        config: Optional[SeasonConfig] = None,
        event_bus: Optional[EventBus] = None,
    ):
        self._red = red_team or RedTeam()
        self._blue = blue_team or BlueTeam()
        self._judge = judge or MatchJudge()
        self._config = config or SeasonConfig()

        # 赛季状态
        self._current_season: int = 1
        self._matches_in_season: int = 0
        self._season_summaries: list[SeasonSummary] = []
        self._current_season_summary: SeasonSummary = SeasonSummary(season_number=1)

        # 全局历史
        self._match_history: list[ArenaMatch] = []
        self._total_matches: int = 0

        # 平衡控制
        self._balance_interventions: int = 0
        self._last_balance_check: int = 0

        # 回调
        self._on_match_complete: Optional[Callable[[ArenaMatch], None]] = None
        self._on_season_complete: Optional[Callable[[SeasonSummary], None]] = None

        # EventBus 集成 — 统一挂载到所有子组件
        if event_bus:
            self.attach_event_bus(event_bus, source="arena.orchestrator")
            self._red.attach_event_bus(event_bus, source="arena.red_team")
            self._blue.attach_event_bus(event_bus, source="arena.blue_team")
            self._red.elo.attach_event_bus(event_bus, source="arena.red_elo")
            self._blue.elo.attach_event_bus(event_bus, source="arena.blue_elo")

    # ----------------------------------------------------------
    # 公开属性
    # ----------------------------------------------------------

    @property
    def red_team(self) -> RedTeam:
        return self._red

    @property
    def blue_team(self) -> BlueTeam:
        return self._blue

    @property
    def current_season(self) -> int:
        return self._current_season

    @property
    def total_matches(self) -> int:
        return self._total_matches

    @property
    def match_history(self) -> list[ArenaMatch]:
        return list(self._match_history)

    @property
    def elo_gap(self) -> float:
        """红蓝 ELO 差距（正=红强，负=蓝强）。"""
        return self._red.elo.rating - self._blue.elo.rating

    @property
    def is_balanced(self) -> bool:
        """红蓝是否平衡（ELO 差距在合理范围内）。"""
        return abs(self.elo_gap) < self._config.max_elo_gap

    # ----------------------------------------------------------
    # 回调注册
    # ----------------------------------------------------------

    def set_on_match_complete(self, callback: Callable[[ArenaMatch], None]) -> None:
        """注册对局完成回调。"""
        self._on_match_complete = callback

    def set_on_season_complete(self, callback: Callable[[SeasonSummary], None]) -> None:
        """注册赛季完成回调。"""
        self._on_season_complete = callback

    # ----------------------------------------------------------
    # 核心方法
    # ----------------------------------------------------------

    async def run_match(
        self,
        red_strategy: Optional[RedStrategy] = None,
        blue_strategy: Optional[BlueStrategy] = None,
        target_dimension: Optional[WeaknessDimension] = None,
    ) -> ArenaMatch:
        """运行一局红蓝对抗。

        流程:
            1. 红队选择攻击策略 → 生成对抗样本
            2. 蓝队选择防御策略 → 执行审稿分析
            3. 评判器判定胜负
            4. 更新双方 ELO
            5. 记录对局

        Args:
            red_strategy: 强制指定红队策略（None=自动选择）
            blue_strategy: 强制指定蓝队策略（None=自动选择）
            target_dimension: 强制指定目标维度

        Returns:
            完整的对局记录
        """
        if not GODEL_ADVERSARIAL_TRAINING_ENABLED:
            return ArenaMatch(outcome=MatchOutcome.INVALID)

        match = ArenaMatch(
            season=self._current_season,
            round_in_season=self._matches_in_season + 1,
        )

        # 发布对局开始事件
        self._emit_event(
            EventType.ARENA_MATCH_STARTED,
            season=self._current_season,
            round_in_season=self._matches_in_season + 1,
            red_elo=round(self._red.elo.rating, 1),
            blue_elo=round(self._blue.elo.rating, 1),
        )

        # 记录 ELO 初始值
        match.red_elo_before = self._red.elo.rating
        match.blue_elo_before = self._blue.elo.rating

        try:
            # === 第 1 阶段: 红队出题 ===
            gen_start = time.time()

            # 红队选择策略
            if red_strategy is None:
                blue_weaknesses = self._get_blue_weakness_profile()
                blue_improvement = self._get_blue_recent_improvement()
                explored = self._get_explored_dimensions()
                red_strategy = self._red.select_strategy(
                    blue_weakness_profile=blue_weaknesses,
                    blue_recent_improvement=blue_improvement,
                    explored_dimensions=explored,
                )

            # 红队生成对抗样本
            challenge, strategy_desc = await self._red.generate_challenge(
                strategy=red_strategy,
                blue_weakness_profile=self._get_blue_weakness_profile(),
                target_dimension=target_dimension,
            )

            match.challenge = challenge
            match.red_strategy = strategy_desc
            match.red_difficulty_target = challenge.difficulty
            match.red_dimension_target = challenge.target_dimension
            match.generation_time_seconds = time.time() - gen_start

            # 验证红队样本质量
            is_valid, issues = self._red.generator.validate_case(challenge)
            match.challenge_quality_score = 1.0 if is_valid else 0.3

            if not is_valid and not challenge.paper_snippet:
                # 样本完全无效
                match.outcome = MatchOutcome.INVALID
                match.outcome_details = f"Invalid challenge: {'; '.join(issues)}"
                match.red_elo_after = match.red_elo_before
                match.blue_elo_after = match.blue_elo_before
                self._record_match(match)
                return match

            # === 第 2 阶段: 蓝队答题 ===
            exec_start = time.time()

            # 蓝队选择防御策略
            if blue_strategy is None:
                blue_strategy = self._blue.select_strategy(
                    challenge=challenge,
                    red_recent_strategy=strategy_desc,
                )

            match.blue_strategy = blue_strategy.value

            # 蓝队执行防御
            findings, score = self._blue.execute_defense(
                challenge=challenge,
                strategy=blue_strategy,
            )

            match.blue_findings = findings
            match.blue_score = score
            match.execution_time_seconds = time.time() - exec_start

            # === 第 3 阶段: 评判 ===
            outcome, blue_elo_score, red_elo_score, details = self._judge.judge_match(
                challenge=challenge,
                blue_findings=findings,
                blue_score=score,
                challenge_quality=match.challenge_quality_score,
            )

            match.outcome = outcome
            match.outcome_details = details

            # 发布评判结果事件
            self._emit_event(
                EventType.ARENA_JUDGE_VERDICT,
                match_id=match.match_id,
                outcome=outcome.value,
                blue_elo_score=round(blue_elo_score, 3),
                red_elo_score=round(red_elo_score, 3),
                details=details,
            )

            # 统计匹配数据
            match.total_gold_count = len(challenge.gold_findings)
            match.matched_gold_count = self._judge._match_findings(
                challenge.gold_findings, findings
            )

            # === 第 4 阶段: 更新 ELO ===
            if match.is_valid:
                # 红队 ELO 更新
                self._red.elo.update(
                    actual_score=red_elo_score,
                    opponent_rating=self._blue.elo.rating,
                    match_id=match.match_id,
                )
                # 蓝队 ELO 更新
                self._blue.elo.update(
                    actual_score=blue_elo_score,
                    opponent_rating=self._red.elo.rating,
                    match_id=match.match_id,
                )

            match.red_elo_after = self._red.elo.rating
            match.blue_elo_after = self._blue.elo.rating

            # === 第 5 阶段: 记录结果 ===
            self._red.record_result(outcome, match)
            self._blue.record_result(outcome, match)

            # 更新对抗样本的使用记录
            challenge.record_usage(passed=(outcome == MatchOutcome.BLUE_WIN))

        except Exception as e:
            logger.error("[ArenaOrchestrator] Match execution error: %s", e)
            match.outcome = MatchOutcome.ERROR
            match.error = str(e)
            match.red_elo_after = match.red_elo_before
            match.blue_elo_after = match.blue_elo_before

        # 记录对局
        self._record_match(match)

        # 发布对局完成事件
        self._emit_event(
            EventType.ARENA_MATCH_COMPLETED,
            match_id=match.match_id,
            outcome=match.outcome.value,
            red_elo_after=round(match.red_elo_after, 1),
            blue_elo_after=round(match.blue_elo_after, 1),
            red_elo_delta=round(match.red_elo_delta, 2),
            blue_elo_delta=round(match.blue_elo_delta, 2),
            season=match.season,
            round_in_season=match.round_in_season,
            generation_time=round(match.generation_time_seconds, 3),
            execution_time=round(match.execution_time_seconds, 3),
        )

        # 平衡检查
        if self._total_matches - self._last_balance_check >= self._config.balance_check_interval:
            self._check_and_rebalance()
            self._last_balance_check = self._total_matches

        return match

    async def run_season(
        self,
        matches: Optional[int] = None,
    ) -> SeasonSummary:
        """运行一个完整赛季。

        Args:
            matches: 本赛季对局数（None = 使用配置默认值）

        Returns:
            赛季总结
        """
        if not GODEL_ADVERSARIAL_TRAINING_ENABLED:
            return SeasonSummary()

        # 发布赛季开始事件
        if GODEL_ADVERSARIAL_SEASON_ENABLED:
            self._emit_event(
                EventType.ARENA_SEASON_STARTED,
                season=self._current_season,
                planned_matches=matches or self._config.matches_per_season,
                red_elo=round(self._red.elo.rating, 1),
                blue_elo=round(self._blue.elo.rating, 1),
            )

        target_matches = matches or self._config.matches_per_season
        self._current_season_summary = SeasonSummary(
            season_number=self._current_season,
            red_elo_start=self._red.elo.rating,
            blue_elo_start=self._blue.elo.rating,
        )

        for _ in range(target_matches):
            match = await self.run_match()

            # 更新赛季统计
            self._current_season_summary.total_matches += 1
            if match.outcome == MatchOutcome.RED_WIN:
                self._current_season_summary.red_wins += 1
            elif match.outcome == MatchOutcome.BLUE_WIN:
                self._current_season_summary.blue_wins += 1
            elif match.outcome == MatchOutcome.DRAW:
                self._current_season_summary.draws += 1
            else:
                self._current_season_summary.invalid_matches += 1

            # 维度覆盖
            if match.challenge:
                dim = match.challenge.target_dimension.value
                self._current_season_summary.dimension_coverage[dim] = (
                    self._current_season_summary.dimension_coverage.get(dim, 0) + 1
                )
                diff = match.challenge.difficulty.value
                self._current_season_summary.difficulty_distribution[diff] = (
                    self._current_season_summary.difficulty_distribution.get(diff, 0) + 1
                )

            # 质量统计
            if match.challenge_quality_score > 0:
                n = self._current_season_summary.total_matches
                old_avg = self._current_season_summary.avg_challenge_quality
                self._current_season_summary.avg_challenge_quality = (
                    old_avg * (n - 1) + match.challenge_quality_score
                ) / n

        # 赛季结束
        self._current_season_summary.red_elo_end = self._red.elo.rating
        self._current_season_summary.blue_elo_end = self._blue.elo.rating
        self._current_season_summary.ended_at = time.time()

        # 保存赛季总结
        self._season_summaries.append(self._current_season_summary)

        # 赛季 ELO 重置
        if self._config.elo_reset_on_season_end and GODEL_ADVERSARIAL_SEASON_ENABLED:
            self._red.elo.season_reset()
            self._blue.elo.season_reset()

        # 发布赛季结束事件
        if GODEL_ADVERSARIAL_SEASON_ENABLED:
            self._emit_event(
                EventType.ARENA_SEASON_ENDED,
                season=self._current_season,
                total_matches=self._current_season_summary.total_matches,
                red_wins=self._current_season_summary.red_wins,
                blue_wins=self._current_season_summary.blue_wins,
                draws=self._current_season_summary.draws,
                red_elo_change=round(
                    self._current_season_summary.red_elo_end - self._current_season_summary.red_elo_start, 1
                ),
                blue_elo_change=round(
                    self._current_season_summary.blue_elo_end - self._current_season_summary.blue_elo_start, 1
                ),
            )

        # 通知
        if self._on_season_complete:
            self._on_season_complete(self._current_season_summary)

        # 进入下一赛季
        summary = self._current_season_summary
        self._current_season += 1
        self._matches_in_season = 0

        return summary

    def get_arena_stats(self) -> dict:
        """获取竞技场全局统计。"""
        return {
            "current_season": self._current_season,
            "total_matches": self._total_matches,
            "matches_in_season": self._matches_in_season,
            "red_team": self._red.get_stats(),
            "blue_team": self._blue.get_stats(),
            "elo_gap": round(self.elo_gap, 1),
            "is_balanced": self.is_balanced,
            "balance_interventions": self._balance_interventions,
            "seasons_completed": len(self._season_summaries),
            "recent_outcomes": [
                m.outcome.value for m in self._match_history[-10:]
            ],
        }

    def get_learning_insights(self) -> dict:
        """获取学习洞察——从红蓝对抗历史中提取训练建议。"""
        if not self._match_history:
            return {"recommendations": [], "insights": []}

        insights: list[str] = []
        recommendations: list[str] = []

        # 分析蓝队薄弱维度
        weak_dims = self._blue.get_weakness_dimensions()
        if weak_dims:
            insights.append(
                f"蓝队在以下维度较弱: {', '.join(weak_dims)}"
            )
            recommendations.append(
                f"建议针对 {weak_dims[0]} 维度增加训练量"
            )

        # 分析红队最有效策略
        red_effectiveness = self._red.strategy_effectiveness
        if red_effectiveness:
            best_strategy = max(red_effectiveness.items(), key=lambda x: x[1])
            if best_strategy[1] > 0.6:
                insights.append(
                    f"红队策略 '{best_strategy[0]}' 胜率最高 ({best_strategy[1]:.0%})"
                )

        # ELO 趋势分析
        red_trend = self._red.elo.get_rating_trend()
        blue_trend = self._blue.elo.get_rating_trend()

        if red_trend > 50:
            insights.append("红队近期 ELO 上升明显，蓝队面临更大挑战")
        if blue_trend > 50:
            insights.append("蓝队近期 ELO 上升明显，检测能力正在提升")
            recommendations.append("蓝队进步良好，可以尝试更高难度的挑战")

        # 平衡性分析
        if not self.is_balanced:
            gap = self.elo_gap
            if gap > 0:
                insights.append(f"红蓝不平衡: 红队领先 {gap:.0f} ELO，蓝队需要加强")
                recommendations.append("建议降低红队难度或增加蓝队训练量")
            else:
                insights.append(f"红蓝不平衡: 蓝队领先 {-gap:.0f} ELO，红队需要更有创意")
                recommendations.append("建议红队使用更多 EXPLORE 和 COMPOUND 策略")

        return {
            "insights": insights,
            "recommendations": recommendations,
            "red_elo": round(self._red.elo.rating, 1),
            "blue_elo": round(self._blue.elo.rating, 1),
            "red_trend": round(red_trend, 1),
            "blue_trend": round(blue_trend, 1),
            "total_matches": self._total_matches,
        }

    # ----------------------------------------------------------
    # 序列化
    # ----------------------------------------------------------

    def serialize(self) -> dict:
        return {
            "current_season": self._current_season,
            "matches_in_season": self._matches_in_season,
            "total_matches": self._total_matches,
            "balance_interventions": self._balance_interventions,
            "last_balance_check": self._last_balance_check,
            "red_team": self._red.serialize(),
            "blue_team": self._blue.serialize(),
            "config": {
                "matches_per_season": self._config.matches_per_season,
                "elo_reset_on_season_end": self._config.elo_reset_on_season_end,
                "escalation_per_season": self._config.escalation_per_season,
                "min_matches_for_strategy_switch": self._config.min_matches_for_strategy_switch,
                "balance_check_interval": self._config.balance_check_interval,
                "max_elo_gap": self._config.max_elo_gap,
            },
            "season_summaries": [s.to_dict() for s in self._season_summaries],
            "match_history": [m.to_dict() for m in self._match_history[-100:]],
        }

    @classmethod
    def from_dict(
        cls,
        data: dict,
        generator: Optional[AdversarialGenerator] = None,
        executor: Optional[BlueTeamExecutor] = None,
    ) -> "ArenaOrchestrator":
        # 配置
        config_data = data.get("config", {})
        config = SeasonConfig(
            matches_per_season=config_data.get("matches_per_season", 50),
            elo_reset_on_season_end=config_data.get("elo_reset_on_season_end", True),
            escalation_per_season=config_data.get("escalation_per_season", 0.1),
            min_matches_for_strategy_switch=config_data.get("min_matches_for_strategy_switch", 5),
            balance_check_interval=config_data.get("balance_check_interval", 10),
            max_elo_gap=config_data.get("max_elo_gap", 400.0),
        )

        # 红蓝队
        red = RedTeam.from_dict(data.get("red_team", {}), generator=generator)
        blue = BlueTeam.from_dict(data.get("blue_team", {}), executor=executor)

        orchestrator = cls(red_team=red, blue_team=blue, config=config)
        orchestrator._current_season = data.get("current_season", 1)
        orchestrator._matches_in_season = data.get("matches_in_season", 0)
        orchestrator._total_matches = data.get("total_matches", 0)
        orchestrator._balance_interventions = data.get("balance_interventions", 0)
        orchestrator._last_balance_check = data.get("last_balance_check", 0)

        # 恢复赛季总结
        for s_data in data.get("season_summaries", []):
            summary = SeasonSummary(
                season_number=s_data.get("season_number", 0),
                total_matches=s_data.get("total_matches", 0),
                red_wins=s_data.get("red_wins", 0),
                blue_wins=s_data.get("blue_wins", 0),
                draws=s_data.get("draws", 0),
                invalid_matches=s_data.get("invalid_matches", 0),
            )
            orchestrator._season_summaries.append(summary)

        # 恢复对局历史
        for m_data in data.get("match_history", []):
            match = ArenaMatch.from_dict(m_data)
            orchestrator._match_history.append(match)

        return orchestrator

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _record_match(self, match: ArenaMatch) -> None:
        """记录对局到历史。"""
        self._match_history.append(match)
        self._total_matches += 1
        self._matches_in_season += 1

        # 限制历史长度
        if len(self._match_history) > 500:
            self._match_history = self._match_history[-300:]

        # 通知回调
        if self._on_match_complete:
            try:
                self._on_match_complete(match)
            except Exception as e:
                logger.warning("[ArenaOrchestrator] Match callback error: %s", e)

    def _get_blue_weakness_profile(self) -> Optional[WeaknessProfile]:
        """从蓝队失败历史中构建弱点画像。

        注: 这是一个简化版本。完整版本会调用 WeaknessAnalyzer。
        """
        # 如果失败记忆不足，返回 None
        failures = self._blue._failure_memory
        if len(failures) < 3:
            return None

        # 构建简化的 WeaknessProfile（基于失败维度频率）
        # 实际项目中应该调用 WeaknessAnalyzer.analyze()
        return None  # 简化：让红队自动选择策略时不依赖弱点画像

    def _get_blue_recent_improvement(self) -> float:
        """获取蓝队最近的 ELO 变化（作为进步指标）。"""
        return self._blue.elo.get_rating_trend(last_n=5) / 100.0  # 归一化

    def _get_explored_dimensions(self) -> set[str]:
        """获取已探索的维度集合。"""
        explored: set[str] = set()
        for match in self._match_history[-50:]:
            if match.challenge:
                explored.add(match.challenge.target_dimension.value)
        return explored

    def _check_and_rebalance(self) -> None:
        """检查红蓝平衡性并在必要时介入。

        当一方 ELO 远超另一方时，通过策略调整来恢复平衡:
            - 红队太强: 限制红队难度，给蓝队更多简单样本练习
            - 蓝队太强: 鼓励红队使用更有创意的策略
        """
        gap = self.elo_gap

        if abs(gap) < self._config.max_elo_gap:
            return  # 在平衡范围内

        self._balance_interventions += 1

        if gap > self._config.max_elo_gap:
            # 红队太强——限制红队难度
            action = "limit_red_difficulty"
            logger.info(
                "[ArenaOrchestrator] Rebalancing: Red too strong (gap=%.0f). "
                "Limiting red difficulty.",
                gap,
            )
            self._red.generator.difficulty_controller.force_level(DifficultyLevel.MEDIUM)
        elif gap < -self._config.max_elo_gap:
            # 蓝队太强——鼓励红队升级
            action = "escalate_red_difficulty"
            logger.info(
                "[ArenaOrchestrator] Rebalancing: Blue too strong (gap=%.0f). "
                "Encouraging red escalation.",
                gap,
            )
            self._red.generator.difficulty_controller.force_level(DifficultyLevel.HARD)
        else:
            action = "none"

        # 发布平衡调整事件
        self._emit_event(
            EventType.ARENA_BALANCE_ADJUSTED,
            action=action,
            elo_gap=round(gap, 1),
            red_elo=round(self._red.elo.rating, 1),
            blue_elo=round(self._blue.elo.rating, 1),
            intervention_count=self._balance_interventions,
        )
