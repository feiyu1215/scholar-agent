"""
training/weakness_analyzer.py — 弱点分析器 (Weakness Analyzer)

从多个数据源（MetaHarness 评估结果、Memory 层历史经验、FailureStore 失败记录）
聚合分析 Agent 的系统性弱点，生成结构化的弱点画像（WeaknessProfile）。

弱点画像是对抗训练的起点：它告诉 AdversarialGenerator "应该针对哪些维度生成挑战"。

数据源架构:
    - Phase 5 MetaHarness → BatchResult.bottlenecks → 类别级弱点
    - Phase 5 MetaHarness → per-paper metrics → 论文类型级弱点
    - Phase 4 FailureStore → FailureContext 列表 → Skill 级弱点
    - Memory 层 → DomainPattern + ProceduralPattern → 认知模式级弱点
    - Phase 6 Reflection → 反复差距模式 → 反思触发的弱点

设计原则:
    - 多源融合: 单一数据源可能有偏，多源交叉验证提高可信度
    - 时间衰减: 较新的失败权重更高（Agent 可能已经修复了旧问题）
    - 置信度标注: 每个弱点附带置信度，低置信度弱点用于探索性训练
    - 增量更新: 支持增量式更新弱点画像，无需每次全量重算

Kill Switch: SCHOLAR_GODEL_ADVERSARIAL_TRAINING
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable

from core.godel_config import GODEL_ADVERSARIAL_TRAINING_ENABLED

logger = logging.getLogger(__name__)


# ==============================================================
# Kill Switch (delegate to godel_config — single source of truth)
# ==============================================================

ADVERSARIAL_TRAINING_ENABLED: bool = GODEL_ADVERSARIAL_TRAINING_ENABLED
"""Backward-compatible alias. Actual source of truth is core.godel_config."""


# ==============================================================
# 弱点维度枚举
# ==============================================================

class WeaknessDimension(str, Enum):
    """弱点所属的维度分类。"""

    # --- 内容分析能力 ---
    METHODOLOGY_ANALYSIS = "methodology_analysis"
    """方法论分析能力不足（无法识别研究设计缺陷）。"""

    STATISTICAL_REASONING = "statistical_reasoning"
    """统计推理能力不足（误判显著性、遗漏假设违反）。"""

    CAUSAL_INFERENCE = "causal_inference"
    """因果推断分析能力不足（无法判断识别策略有效性）。"""

    DATA_CONSISTENCY = "data_consistency"
    """数据一致性验证能力不足（遗漏表格/文本/图表之间的矛盾）。"""

    LITERATURE_COVERAGE = "literature_coverage"
    """文献覆盖度分析能力不足（无法判断遗漏的关键引文）。"""

    LOGICAL_COHERENCE = "logical_coherence"
    """逻辑连贯性分析能力不足（无法发现论证链中的逻辑漏洞）。"""

    # --- 格式与结构 ---
    FORMAT_UNDERSTANDING = "format_understanding"
    """格式理解能力不足（无法正确解析非标准论文结构）。"""

    CROSS_SECTION_REASONING = "cross_section_reasoning"
    """跨章节推理能力不足（无法连接不同章节的信息做综合判断）。"""

    # --- 特定领域 ---
    DID_ANALYSIS = "did_analysis"
    """DID (Difference-in-Differences) 分析专项弱点。"""

    IV_ANALYSIS = "iv_analysis"
    """工具变量 (Instrumental Variable) 分析专项弱点。"""

    RDD_ANALYSIS = "rdd_analysis"
    """断点回归 (Regression Discontinuity) 分析专项弱点。"""

    EVENT_STUDY = "event_study"
    """事件研究 (Event Study) 分析专项弱点。"""

    PANEL_DATA = "panel_data"
    """面板数据方法分析专项弱点。"""

    # --- 元能力 ---
    EFFICIENCY = "efficiency"
    """审稿效率问题（token 消耗过高、循环次数过多）。"""

    COVERAGE = "coverage"
    """覆盖度问题（系统性遗漏某类 Findings）。"""

    DEPTH = "depth"
    """深度问题（发现问题但分析不够深入）。"""

    PRECISION = "precision"
    """精度问题（误报率过高）。"""


# ==============================================================
# 弱点来源
# ==============================================================

class WeaknessSource(str, Enum):
    """弱点证据的来源。"""
    META_HARNESS_BOTTLENECK = "meta_harness_bottleneck"
    META_HARNESS_PER_PAPER = "meta_harness_per_paper"
    FAILURE_STORE = "failure_store"
    MEMORY_PATTERN = "memory_pattern"
    REFLECTION_GAP = "reflection_gap"
    MANUAL_ANNOTATION = "manual_annotation"


# ==============================================================
# 弱点证据
# ==============================================================

@dataclass
class WeaknessEvidence:
    """一条弱点的证据记录。"""
    source: WeaknessSource
    description: str
    severity: float = 0.5  # 0.0~1.0
    timestamp: float = field(default_factory=time.time)
    raw_data: dict = field(default_factory=dict)

    @property
    def age_days(self) -> float:
        """证据的年龄（天）。"""
        return (time.time() - self.timestamp) / 86400.0

    def time_decay_weight(self, half_life_days: float = 14.0) -> float:
        """时间衰减权重（半衰期模型）。

        Args:
            half_life_days: 半衰期天数。14天 = 2周前的证据权重减半。
        """
        if half_life_days <= 0:
            return 1.0
        return math.exp(-0.693 * self.age_days / half_life_days)

    def to_dict(self) -> dict:
        return {
            "source": self.source.value,
            "description": self.description,
            "severity": self.severity,
            "timestamp": self.timestamp,
            "raw_data": self.raw_data,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WeaknessEvidence":
        return cls(
            source=WeaknessSource(data.get("source", "manual_annotation")),
            description=data.get("description", ""),
            severity=data.get("severity", 0.5),
            timestamp=data.get("timestamp", time.time()),
            raw_data=data.get("raw_data", {}),
        )


# ==============================================================
# 弱点条目
# ==============================================================

@dataclass
class WeaknessEntry:
    """单个弱点条目：一个维度上的具体弱点。"""
    dimension: WeaknessDimension
    summary: str
    evidences: list[WeaknessEvidence] = field(default_factory=list)
    priority: float = 0.0  # 0.0~1.0, 综合评分后填充
    training_attempts: int = 0  # 已针对此弱点进行的训练次数
    last_improvement: float = 0.0  # 上次训练后的改善幅度
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def weakness_id(self) -> str:
        content = f"{self.dimension.value}:{self.summary[:50]}"
        return hashlib.md5(content.encode()).hexdigest()[:12]

    @property
    def evidence_count(self) -> int:
        return len(self.evidences)

    @property
    def multi_source_confirmed(self) -> bool:
        """是否有多个不同来源的证据确认（交叉验证）。"""
        sources = {e.source for e in self.evidences}
        return len(sources) >= 2

    def compute_confidence(self, half_life_days: float = 14.0) -> float:
        """计算弱点的置信度。

        考虑因素:
            - 证据数量: 多证据 → 高置信度
            - 多源确认: 不同来源交叉确认 → 加分
            - 时间衰减: 旧证据贡献递减
            - 历史训练: 已针对性训练过 → 可能已修复，降低置信度
        """
        if not self.evidences:
            return 0.0

        # 基础: 时间衰减加权证据数
        weighted_count = sum(
            e.time_decay_weight(half_life_days) for e in self.evidences
        )

        # 数量贡献 (saturating: 5 条证据 ≈ 满分)
        quantity_score = min(1.0, weighted_count / 5.0)

        # 多源奖励
        unique_sources = len({e.source for e in self.evidences})
        source_bonus = min(0.3, (unique_sources - 1) * 0.1)

        # 严重度加权平均
        if self.evidences:
            avg_severity = sum(
                e.severity * e.time_decay_weight(half_life_days) for e in self.evidences
            ) / max(1e-9, weighted_count)
        else:
            avg_severity = 0.5

        # 训练衰减: 如果已训练多次且有改善，降低优先级
        training_decay = 1.0
        if self.training_attempts > 0 and self.last_improvement > 0.1:
            training_decay = max(0.3, 1.0 - self.training_attempts * 0.15)

        confidence = (
            0.4 * quantity_score
            + 0.3 * avg_severity
            + source_bonus
        ) * training_decay

        return max(0.0, min(1.0, confidence))

    def compute_priority(self, half_life_days: float = 14.0) -> float:
        """计算训练优先级。

        高优先级: 高置信度 + 高严重度 + 少训练尝试 + 多源确认
        低优先级: 低置信度 或 已训练多次但无改善
        """
        confidence = self.compute_confidence(half_life_days)

        # 严重度中位数
        severities = [e.severity for e in self.evidences] if self.evidences else [0.5]
        median_severity = sorted(severities)[len(severities) // 2]

        # 训练饥饿度: 训练次数越少，越需要训练
        hunger = 1.0 / (1.0 + self.training_attempts * 0.3)

        # 恶化趋势: 最近证据严重度 > 历史平均 → 问题在恶化
        worsening_factor = 1.0
        if len(self.evidences) >= 3:
            recent = sorted(self.evidences, key=lambda e: e.timestamp)[-2:]
            recent_avg = sum(e.severity for e in recent) / len(recent)
            overall_avg = sum(e.severity for e in self.evidences) / len(self.evidences)
            if recent_avg > overall_avg * 1.2:
                worsening_factor = 1.3  # 恶化趋势加速优先级

        priority = (
            0.35 * confidence
            + 0.30 * median_severity
            + 0.20 * hunger
            + 0.15 * (1.0 if self.multi_source_confirmed else 0.5)
        ) * worsening_factor

        return max(0.0, min(1.0, priority))

    def add_evidence(self, evidence: WeaknessEvidence) -> None:
        """添加新证据并更新时间戳。"""
        self.evidences.append(evidence)
        self.updated_at = time.time()

    def record_training_attempt(self, improvement: float) -> None:
        """记录一次训练尝试及其效果。"""
        self.training_attempts += 1
        self.last_improvement = improvement
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "weakness_id": self.weakness_id,
            "dimension": self.dimension.value,
            "summary": self.summary,
            "evidences": [e.to_dict() for e in self.evidences],
            "priority": self.priority,
            "training_attempts": self.training_attempts,
            "last_improvement": self.last_improvement,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WeaknessEntry":
        return cls(
            dimension=WeaknessDimension(data["dimension"]),
            summary=data.get("summary", ""),
            evidences=[WeaknessEvidence.from_dict(e) for e in data.get("evidences", [])],
            priority=data.get("priority", 0.0),
            training_attempts=data.get("training_attempts", 0),
            last_improvement=data.get("last_improvement", 0.0),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )


# ==============================================================
# 弱点画像
# ==============================================================

@dataclass
class WeaknessProfile:
    """Agent 的弱点画像 — 对抗训练的输入。

    画像维护一组按优先级排序的弱点条目，对抗样本生成器
    从画像中选取 Top-K 弱点作为挑战目标。
    """
    entries: list[WeaknessEntry] = field(default_factory=list)
    last_full_analysis: float = 0.0
    analysis_count: int = 0
    version: int = 0

    @property
    def top_weaknesses(self) -> list[WeaknessEntry]:
        """按优先级排序的弱点列表（Top 优先）。"""
        return sorted(self.entries, key=lambda e: e.priority, reverse=True)

    def get_top_k(self, k: int = 5) -> list[WeaknessEntry]:
        """获取前 K 个最高优先级弱点。"""
        return self.top_weaknesses[:k]

    def get_by_dimension(self, dim: WeaknessDimension) -> list[WeaknessEntry]:
        """获取某个维度的所有弱点。"""
        return [e for e in self.entries if e.dimension == dim]

    def get_trainable(self, min_confidence: float = 0.3) -> list[WeaknessEntry]:
        """获取适合训练的弱点（有足够置信度）。"""
        return [
            e for e in self.top_weaknesses
            if e.compute_confidence() >= min_confidence
        ]

    def get_exploratory(self, max_confidence: float = 0.3) -> list[WeaknessEntry]:
        """获取适合探索性训练的弱点（低置信度，需验证是否真是弱点）。"""
        return [
            e for e in self.entries
            if e.compute_confidence() < max_confidence
        ]

    def upsert_entry(self, entry: WeaknessEntry) -> None:
        """插入或更新弱点条目（基于维度+摘要去重）。"""
        for i, existing in enumerate(self.entries):
            if (existing.dimension == entry.dimension
                    and existing.summary == entry.summary):
                # 合并证据
                for ev in entry.evidences:
                    existing.add_evidence(ev)
                existing.priority = existing.compute_priority()
                return
        entry.priority = entry.compute_priority()
        self.entries.append(entry)

    def recompute_priorities(self, half_life_days: float = 14.0) -> None:
        """重新计算所有弱点的优先级。"""
        for entry in self.entries:
            entry.priority = entry.compute_priority(half_life_days)

    def prune_resolved(self, min_confidence: float = 0.1) -> list[WeaknessEntry]:
        """移除已解决的弱点（置信度过低 = 近期无证据支撑）。

        Returns:
            被移除的弱点列表（用于记录日志）。
        """
        resolved = []
        remaining = []
        for entry in self.entries:
            if entry.compute_confidence() < min_confidence:
                resolved.append(entry)
            else:
                remaining.append(entry)
        self.entries = remaining
        return resolved

    def dimension_distribution(self) -> dict[WeaknessDimension, float]:
        """各维度的弱点分布（按优先级加权）。"""
        dist: dict[WeaknessDimension, float] = defaultdict(float)
        total = sum(e.priority for e in self.entries) or 1.0
        for entry in self.entries:
            dist[entry.dimension] += entry.priority / total
        return dict(dist)

    def to_dict(self) -> dict:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "last_full_analysis": self.last_full_analysis,
            "analysis_count": self.analysis_count,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WeaknessProfile":
        return cls(
            entries=[WeaknessEntry.from_dict(e) for e in data.get("entries", [])],
            last_full_analysis=data.get("last_full_analysis", 0.0),
            analysis_count=data.get("analysis_count", 0),
            version=data.get("version", 0),
        )


# ==============================================================
# 输入数据协议（面向已有模块的松耦合接口）
# ==============================================================

@runtime_checkable
class BottleneckProvider(Protocol):
    """提供 Bottleneck 数据的协议（由 MetaHarness BatchResult 实现）。"""

    def get_bottlenecks(self) -> list[dict]:
        """返回瓶颈列表，每项包含 type/severity/description/evidence/affected_papers。"""
        ...


@runtime_checkable
class FailureProvider(Protocol):
    """提供失败记录的协议（由 Phase 4 FailureStore 实现）。"""

    def get_recent_failures(self, limit: int = 100) -> list[dict]:
        """返回最近的失败记录列表。"""
        ...


@runtime_checkable
class MemoryProvider(Protocol):
    """提供记忆模式的协议（由 Memory 层实现）。"""

    def get_domain_patterns(self) -> list[dict]:
        """返回领域模式列表。"""
        ...

    def get_procedural_patterns(self) -> list[dict]:
        """返回程序性模式列表。"""
        ...


@runtime_checkable
class ReflectionProvider(Protocol):
    """提供反思差距模式的协议（由 Phase 6 ReflectionComplete 实现）。"""

    def get_recurring_gaps(self) -> list[dict]:
        """返回反复出现的差距模式列表。"""
        ...


# ==============================================================
# 维度映射器
# ==============================================================

class DimensionMapper:
    """将各来源的标签/分类映射到统一的 WeaknessDimension。"""

    # Bottleneck type → WeaknessDimension 映射
    _BOTTLENECK_TYPE_MAP: dict[str, WeaknessDimension] = {
        "category_weakness": WeaknessDimension.COVERAGE,
        "efficiency_degradation": WeaknessDimension.EFFICIENCY,
        "tool_reliability": WeaknessDimension.METHODOLOGY_ANALYSIS,
        "coverage_gap": WeaknessDimension.COVERAGE,
        "loop_instability": WeaknessDimension.EFFICIENCY,
        "phase_inefficiency": WeaknessDimension.EFFICIENCY,
    }

    # Failure type → WeaknessDimension 映射
    _FAILURE_TYPE_MAP: dict[str, WeaknessDimension] = {
        "tool_error": WeaknessDimension.METHODOLOGY_ANALYSIS,
        "wrong_tool": WeaknessDimension.METHODOLOGY_ANALYSIS,
        "insufficient_info": WeaknessDimension.COVERAGE,
        "logic_error": WeaknessDimension.LOGICAL_COHERENCE,
        "format_mismatch": WeaknessDimension.FORMAT_UNDERSTANDING,
        "timeout": WeaknessDimension.EFFICIENCY,
        "low_quality": WeaknessDimension.DEPTH,
        "missed_issue": WeaknessDimension.COVERAGE,
    }

    # 关键词到具体方法论弱点的映射
    _KEYWORD_DIMENSIONS: list[tuple[list[str], WeaknessDimension]] = [
        (["did", "difference-in-difference", "双重差分", "平行趋势"],
         WeaknessDimension.DID_ANALYSIS),
        (["instrumental variable", "iv", "工具变量", "2sls", "two-stage"],
         WeaknessDimension.IV_ANALYSIS),
        (["regression discontinuity", "rdd", "断点回归", "cutoff"],
         WeaknessDimension.RDD_ANALYSIS),
        (["event study", "事件研究", "event-study"],
         WeaknessDimension.EVENT_STUDY),
        (["panel data", "面板数据", "fixed effect", "random effect"],
         WeaknessDimension.PANEL_DATA),
        (["causal", "因果", "endogen", "内生"],
         WeaknessDimension.CAUSAL_INFERENCE),
        (["statistic", "统计", "p-value", "significance", "显著"],
         WeaknessDimension.STATISTICAL_REASONING),
        (["cross-section", "跨章节", "multi-section"],
         WeaknessDimension.CROSS_SECTION_REASONING),
        (["consistency", "一致性", "contradiction", "矛盾"],
         WeaknessDimension.DATA_CONSISTENCY),
    ]

    @classmethod
    def from_bottleneck(cls, bottleneck_type: str, description: str = "") -> WeaknessDimension:
        """从 Bottleneck 类型推断弱点维度。"""
        # 先看描述中的关键词是否能匹配到更具体的维度
        if description:
            specific = cls._keyword_match(description)
            if specific:
                return specific
        return cls._BOTTLENECK_TYPE_MAP.get(bottleneck_type, WeaknessDimension.COVERAGE)

    @classmethod
    def from_failure(cls, failure_type: str, context_text: str = "") -> WeaknessDimension:
        """从失败类型推断弱点维度。"""
        if context_text:
            specific = cls._keyword_match(context_text)
            if specific:
                return specific
        return cls._FAILURE_TYPE_MAP.get(failure_type, WeaknessDimension.METHODOLOGY_ANALYSIS)

    @classmethod
    def _keyword_match(cls, text: str) -> Optional[WeaknessDimension]:
        """通过关键词匹配推断具体维度。"""
        text_lower = text.lower()
        for keywords, dimension in cls._KEYWORD_DIMENSIONS:
            for kw in keywords:
                if kw.lower() in text_lower:
                    return dimension
        return None


# ==============================================================
# 弱点分析器配置
# ==============================================================

@dataclass
class AnalyzerConfig:
    """弱点分析器配置。"""
    # 时间衰减
    half_life_days: float = 14.0
    """证据半衰期（天）。"""

    # 阈值
    min_evidence_for_entry: int = 1
    """创建弱点条目所需的最少证据数。"""

    prune_confidence_threshold: float = 0.1
    """低于此置信度的弱点将被清理。"""

    max_entries: int = 50
    """画像中保留的最大弱点数。超出时清理最低优先级的。"""

    # 来源权重
    source_weights: dict[str, float] = field(default_factory=lambda: {
        WeaknessSource.META_HARNESS_BOTTLENECK.value: 1.0,
        WeaknessSource.META_HARNESS_PER_PAPER.value: 0.8,
        WeaknessSource.FAILURE_STORE.value: 0.9,
        WeaknessSource.MEMORY_PATTERN.value: 0.6,
        WeaknessSource.REFLECTION_GAP.value: 0.85,
        WeaknessSource.MANUAL_ANNOTATION.value: 1.2,
    })

    # Bottleneck severity 映射
    bottleneck_severity_map: dict[str, float] = field(default_factory=lambda: {
        "critical": 1.0,
        "high": 0.8,
        "medium": 0.5,
        "low": 0.3,
    })


# ==============================================================
# 弱点分析器
# ==============================================================

class WeaknessAnalyzer:
    """从多个数据源聚合分析 Agent 的系统性弱点。

    使用方式:
        analyzer = WeaknessAnalyzer()

        # 增量注入数据
        analyzer.ingest_bottlenecks(batch_result.bottlenecks)
        analyzer.ingest_failures(failure_store.get_recent())
        analyzer.ingest_memory_patterns(memory.get_domain_patterns())
        analyzer.ingest_reflection_gaps(reflection.get_recurring_gaps())

        # 获取弱点画像
        profile = analyzer.build_profile()

        # 或者一次性从 Provider 接口分析
        profile = analyzer.analyze(bottleneck_provider, failure_provider, ...)
    """

    def __init__(self, config: Optional[AnalyzerConfig] = None):
        self._config = config or AnalyzerConfig()
        self._profile = WeaknessProfile()
        self._raw_evidences: list[WeaknessEvidence] = []

    @property
    def config(self) -> AnalyzerConfig:
        return self._config

    @property
    def profile(self) -> WeaknessProfile:
        return self._profile

    # ----------------------------------------------------------
    # 一站式分析接口
    # ----------------------------------------------------------

    def analyze(
        self,
        bottleneck_provider: Optional[BottleneckProvider] = None,
        failure_provider: Optional[FailureProvider] = None,
        memory_provider: Optional[MemoryProvider] = None,
        reflection_provider: Optional[ReflectionProvider] = None,
    ) -> WeaknessProfile:
        """一站式分析：从所有可用的 Provider 聚合弱点。

        如果 Kill Switch OFF，返回空画像。
        """
        if not ADVERSARIAL_TRAINING_ENABLED:
            logger.debug("[WeaknessAnalyzer] Kill switch OFF, returning empty profile")
            return WeaknessProfile()

        # 收集证据
        if bottleneck_provider:
            self.ingest_bottlenecks(bottleneck_provider.get_bottlenecks())

        if failure_provider:
            self.ingest_failures(failure_provider.get_recent_failures())

        if memory_provider:
            self.ingest_memory_patterns(
                memory_provider.get_domain_patterns(),
                memory_provider.get_procedural_patterns(),
            )

        if reflection_provider:
            self.ingest_reflection_gaps(reflection_provider.get_recurring_gaps())

        return self.build_profile()

    # ----------------------------------------------------------
    # 增量数据注入
    # ----------------------------------------------------------

    def ingest_bottlenecks(self, bottlenecks: list[dict]) -> int:
        """从 MetaHarness 瓶颈数据注入弱点证据。

        Args:
            bottlenecks: Bottleneck.to_dict() 格式的列表

        Returns:
            新增证据数。
        """
        if not ADVERSARIAL_TRAINING_ENABLED:
            return 0

        count = 0
        for bn in bottlenecks:
            bn_type = bn.get("type", "")
            description = bn.get("description", "")
            severity_str = bn.get("severity", "medium")
            severity = self._config.bottleneck_severity_map.get(severity_str, 0.5)
            affected = bn.get("affected_papers", [])

            dimension = DimensionMapper.from_bottleneck(bn_type, description)

            evidence = WeaknessEvidence(
                source=WeaknessSource.META_HARNESS_BOTTLENECK,
                description=f"[Bottleneck:{bn_type}] {description}",
                severity=severity * self._config.source_weights.get(
                    WeaknessSource.META_HARNESS_BOTTLENECK.value, 1.0
                ),
                raw_data={"bottleneck": bn, "affected_papers": affected},
            )

            entry = WeaknessEntry(
                dimension=dimension,
                summary=self._summarize_bottleneck(bn_type, description),
                evidences=[evidence],
            )
            self._profile.upsert_entry(entry)
            count += 1

        return count

    def ingest_failures(self, failures: list[dict]) -> int:
        """从 FailureStore 注入弱点证据。

        Args:
            failures: FailureContext.to_dict() 格式的列表

        Returns:
            新增证据数。
        """
        if not ADVERSARIAL_TRAINING_ENABLED:
            return 0

        count = 0
        for fc in failures:
            failure_type = fc.get("failure_type", "tool_error")
            skill_name = fc.get("skill_name", "unknown")
            error_msg = fc.get("error_message", "")
            snippet = fc.get("paper_text_snippet", "")
            ts = fc.get("timestamp", time.time())

            # 组合上下文文本用于维度推断
            context_text = f"{skill_name} {error_msg} {snippet}"
            dimension = DimensionMapper.from_failure(failure_type, context_text)

            severity = self._failure_severity(failure_type, error_msg)

            evidence = WeaknessEvidence(
                source=WeaknessSource.FAILURE_STORE,
                description=f"[Failure:{failure_type}] Skill={skill_name}: {error_msg[:100]}",
                severity=severity * self._config.source_weights.get(
                    WeaknessSource.FAILURE_STORE.value, 0.9
                ),
                timestamp=ts,
                raw_data=fc,
            )

            entry = WeaknessEntry(
                dimension=dimension,
                summary=f"{skill_name} {failure_type} failure",
                evidences=[evidence],
            )
            self._profile.upsert_entry(entry)
            count += 1

        return count

    def ingest_memory_patterns(
        self,
        domain_patterns: list[dict],
        procedural_patterns: Optional[list[dict]] = None,
    ) -> int:
        """从 Memory 层的模式数据注入弱点证据。

        Domain patterns 中 success_rate < 0.5 的模式暗示该领域是弱点。
        Procedural patterns 中标记为 "ineffective" 的程序性知识暗示元能力问题。
        """
        if not ADVERSARIAL_TRAINING_ENABLED:
            return 0

        count = 0

        # Domain patterns: 低成功率 → 弱点
        for dp in domain_patterns:
            success_rate = dp.get("success_rate", 1.0)
            if success_rate >= 0.6:
                continue  # 只关注成功率低的

            domain = dp.get("domain", "")
            description = dp.get("description", "")
            context_text = f"{domain} {description}"
            dimension = DimensionMapper._keyword_match(context_text) or WeaknessDimension.METHODOLOGY_ANALYSIS

            severity = max(0.2, 1.0 - success_rate)  # success_rate越低，severity越高

            evidence = WeaknessEvidence(
                source=WeaknessSource.MEMORY_PATTERN,
                description=f"[MemoryPattern] Domain={domain}, success_rate={success_rate:.2f}",
                severity=severity * self._config.source_weights.get(
                    WeaknessSource.MEMORY_PATTERN.value, 0.6
                ),
                raw_data=dp,
            )

            entry = WeaknessEntry(
                dimension=dimension,
                summary=f"Low success in {domain}" if domain else "Low success pattern",
                evidences=[evidence],
            )
            self._profile.upsert_entry(entry)
            count += 1

        # Procedural patterns: 无效模式
        if procedural_patterns:
            for pp in procedural_patterns:
                effectiveness = pp.get("effectiveness", 1.0)
                if effectiveness >= 0.5:
                    continue

                pattern_name = pp.get("name", pp.get("pattern", "unknown"))
                dimension = WeaknessDimension.EFFICIENCY  # 程序性弱点多为效率类

                evidence = WeaknessEvidence(
                    source=WeaknessSource.MEMORY_PATTERN,
                    description=f"[ProceduralPattern] {pattern_name} effectiveness={effectiveness:.2f}",
                    severity=(1.0 - effectiveness) * self._config.source_weights.get(
                        WeaknessSource.MEMORY_PATTERN.value, 0.6
                    ),
                    raw_data=pp,
                )

                entry = WeaknessEntry(
                    dimension=dimension,
                    summary=f"Ineffective procedure: {pattern_name}",
                    evidences=[evidence],
                )
                self._profile.upsert_entry(entry)
                count += 1

        return count

    def ingest_reflection_gaps(self, recurring_gaps: list[dict]) -> int:
        """从 Phase 6 反思系统的反复差距模式注入弱点证据。

        每个 gap 包含: pattern_name, occurrence_count, severity, recent_examples
        """
        if not ADVERSARIAL_TRAINING_ENABLED:
            return 0

        count = 0
        for gap in recurring_gaps:
            pattern_name = gap.get("pattern_name", gap.get("name", ""))
            occurrence = gap.get("occurrence_count", gap.get("occurrences", 1))
            severity = gap.get("severity", 0.5)
            examples = gap.get("recent_examples", [])

            # 从模式名称和示例推断维度
            context_text = pattern_name + " " + " ".join(str(e) for e in examples[:3])
            dimension = DimensionMapper._keyword_match(context_text) or WeaknessDimension.COVERAGE

            # 出现次数越多，severity 加权越高
            adjusted_severity = min(1.0, severity * (1.0 + 0.1 * (occurrence - 1)))

            evidence = WeaknessEvidence(
                source=WeaknessSource.REFLECTION_GAP,
                description=f"[ReflectionGap] {pattern_name} (x{occurrence})",
                severity=adjusted_severity * self._config.source_weights.get(
                    WeaknessSource.REFLECTION_GAP.value, 0.85
                ),
                raw_data=gap,
            )

            entry = WeaknessEntry(
                dimension=dimension,
                summary=f"Recurring gap: {pattern_name}",
                evidences=[evidence],
            )
            self._profile.upsert_entry(entry)
            count += 1

        return count

    def ingest_manual(
        self,
        dimension: WeaknessDimension,
        description: str,
        severity: float = 0.8,
    ) -> None:
        """手动标注弱点（来自人类审稿反馈）。"""
        if not ADVERSARIAL_TRAINING_ENABLED:
            return

        evidence = WeaknessEvidence(
            source=WeaknessSource.MANUAL_ANNOTATION,
            description=description,
            severity=severity * self._config.source_weights.get(
                WeaknessSource.MANUAL_ANNOTATION.value, 1.2
            ),
        )
        entry = WeaknessEntry(
            dimension=dimension,
            summary=description[:80],
            evidences=[evidence],
        )
        self._profile.upsert_entry(entry)

    # ----------------------------------------------------------
    # 画像构建与维护
    # ----------------------------------------------------------

    def build_profile(self) -> WeaknessProfile:
        """构建/刷新弱点画像。

        操作:
            1. 重算所有条目优先级
            2. 清理已解决的弱点
            3. 裁剪到最大条目数
            4. 更新元数据
        """
        if not ADVERSARIAL_TRAINING_ENABLED:
            return WeaknessProfile()

        # 1. 重算优先级
        self._profile.recompute_priorities(self._config.half_life_days)

        # 2. 清理
        self._profile.prune_resolved(self._config.prune_confidence_threshold)

        # 3. 裁剪
        if len(self._profile.entries) > self._config.max_entries:
            sorted_entries = sorted(
                self._profile.entries, key=lambda e: e.priority, reverse=True
            )
            self._profile.entries = sorted_entries[:self._config.max_entries]

        # 4. 元数据
        self._profile.last_full_analysis = time.time()
        self._profile.analysis_count += 1
        self._profile.version += 1

        logger.info(
            "[WeaknessAnalyzer] Profile built: %d entries, top priority=%.3f",
            len(self._profile.entries),
            self._profile.entries[0].priority if self._profile.entries else 0.0,
        )

        return self._profile

    def reset(self) -> None:
        """重置分析器状态。"""
        self._profile = WeaknessProfile()
        self._raw_evidences.clear()

    # ----------------------------------------------------------
    # 序列化
    # ----------------------------------------------------------

    def serialize(self) -> dict:
        """序列化完整状态。"""
        return {
            "profile": self._profile.to_dict(),
            "config": {
                "half_life_days": self._config.half_life_days,
                "min_evidence_for_entry": self._config.min_evidence_for_entry,
                "prune_confidence_threshold": self._config.prune_confidence_threshold,
                "max_entries": self._config.max_entries,
            },
        }

    @classmethod
    def deserialize(cls, data: dict) -> "WeaknessAnalyzer":
        """从序列化数据恢复。"""
        config_data = data.get("config", {})
        config = AnalyzerConfig(
            half_life_days=config_data.get("half_life_days", 14.0),
            min_evidence_for_entry=config_data.get("min_evidence_for_entry", 1),
            prune_confidence_threshold=config_data.get("prune_confidence_threshold", 0.1),
            max_entries=config_data.get("max_entries", 50),
        )
        analyzer = cls(config=config)
        profile_data = data.get("profile", {})
        if profile_data:
            analyzer._profile = WeaknessProfile.from_dict(profile_data)
        return analyzer

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _summarize_bottleneck(self, bn_type: str, description: str) -> str:
        """从 Bottleneck 生成简洁摘要。"""
        if len(description) <= 60:
            return description
        return f"{bn_type}: {description[:50]}..."

    def _failure_severity(self, failure_type: str, error_msg: str) -> float:
        """根据失败类型和信息估计严重程度。"""
        base_severity = {
            "logic_error": 0.9,
            "missed_issue": 0.85,
            "low_quality": 0.7,
            "wrong_tool": 0.6,
            "insufficient_info": 0.5,
            "format_mismatch": 0.4,
            "tool_error": 0.3,
            "timeout": 0.3,
        }.get(failure_type, 0.5)

        # 如果错误信息提及 "critical" 或 "严重"，提升严重度
        if error_msg:
            msg_lower = error_msg.lower()
            if any(kw in msg_lower for kw in ["critical", "严重", "fatal", "致命"]):
                base_severity = min(1.0, base_severity + 0.2)

        return base_severity
