"""
evaluation/bottleneck_analyzer.py — 从批量评估结果中识别系统性瓶颈。

核心思路:
    分析多篇论文的评估结果，找出模式性的弱点：
    - 某个 category 的 recall 系统性偏低 → 该领域 Skill 有问题
    - 长论文的 efficiency 低 → compaction 策略需要优化
    - 特定论文类型的 F1 低 → 对应 template 不足
    - 工具成功率低 → 某个工具实现有 bug
    - doom loop 频繁 → loop guard 阈值需调整

输出为结构化的 Bottleneck 对象列表，可直接生成优化建议。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from evaluation.quality_metrics import (
    ReviewQualityMetrics,
    AggregateQualityMetrics,
)


# ============================================================
# Bottleneck Types & Severity
# ============================================================


class BottleneckType(str, Enum):
    """瓶颈类型分类。"""
    CATEGORY_WEAKNESS = "category_weakness"        # 某类 finding 系统性弱
    EFFICIENCY_DEGRADATION = "efficiency_degradation"  # 效率问题
    TOOL_RELIABILITY = "tool_reliability"           # 工具可靠性问题
    COVERAGE_GAP = "coverage_gap"                  # 覆盖度不足
    LOOP_INSTABILITY = "loop_instability"          # 循环不稳定
    PHASE_INEFFICIENCY = "phase_inefficiency"      # phase 流转低效


class Severity(str, Enum):
    """瓶颈严重程度。"""
    CRITICAL = "critical"   # 严重影响审稿质量，需立即修复
    HIGH = "high"           # 显著影响，应在当前迭代内修复
    MEDIUM = "medium"       # 有改进空间，可规划到下一迭代
    LOW = "low"             # 轻微问题，可作为长期优化方向


@dataclass
class Bottleneck:
    """一个识别出的系统性瓶颈。"""
    type: BottleneckType
    severity: Severity
    description: str
    evidence: dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""
    affected_papers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "severity": self.severity.value,
            "description": self.description,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "affected_papers": self.affected_papers,
        }


# ============================================================
# Analyzer Configuration
# ============================================================


@dataclass
class AnalyzerConfig:
    """瓶颈分析器配置阈值。"""

    # Category weakness: recall 低于此值则标记
    category_recall_threshold: float = 0.4

    # Efficiency: findings/1k_tokens 低于此值则标记
    efficiency_threshold: float = 0.3

    # Tool reliability: 成功率低于此值则标记
    tool_success_threshold: float = 0.8

    # Coverage: 阅读覆盖率低于此值则标记
    coverage_threshold: float = 0.6

    # Loop instability: doom loop 比例高于此值则标记
    doom_loop_rate_threshold: float = 0.2

    # Phase: 回退次数占总转换比例高于此值则标记
    phase_regression_ratio_threshold: float = 0.3

    # Minimum papers to trigger a category-level bottleneck
    min_papers_for_category: int = 2


# ============================================================
# Core Analyzer
# ============================================================


class BottleneckAnalyzer:
    """从批量评估结果中识别系统性瓶颈。

    Usage:
        analyzer = BottleneckAnalyzer()
        bottlenecks = analyzer.analyze(aggregate_metrics)
        for b in bottlenecks:
            print(f"[{b.severity}] {b.type}: {b.description}")
    """

    def __init__(self, config: AnalyzerConfig | None = None):
        self.config = config or AnalyzerConfig()

    def analyze(
        self,
        aggregate: AggregateQualityMetrics,
    ) -> list[Bottleneck]:
        """运行所有检测规则，返回发现的瓶颈列表。

        Args:
            aggregate: 聚合质量度量（含每篇详情）

        Returns:
            按严重程度排序的瓶颈列表
        """
        if not aggregate.per_paper:
            return []

        bottlenecks: list[Bottleneck] = []

        bottlenecks.extend(self._check_category_weaknesses(aggregate))
        bottlenecks.extend(self._check_efficiency(aggregate))
        bottlenecks.extend(self._check_tool_reliability(aggregate))
        bottlenecks.extend(self._check_coverage_gaps(aggregate))
        bottlenecks.extend(self._check_loop_stability(aggregate))
        bottlenecks.extend(self._check_phase_efficiency(aggregate))

        # 按严重程度排序
        severity_order = {
            Severity.CRITICAL: 0,
            Severity.HIGH: 1,
            Severity.MEDIUM: 2,
            Severity.LOW: 3,
        }
        bottlenecks.sort(key=lambda b: severity_order.get(b.severity, 99))

        return bottlenecks

    # ============================================================
    # Detection Rules
    # ============================================================

    def _check_category_weaknesses(
        self,
        aggregate: AggregateQualityMetrics,
    ) -> list[Bottleneck]:
        """检测系统性的 category 弱点。

        策略: 聚合所有论文的 category_breakdown，找出 recall 系统性低的 category。
        """
        bottlenecks: list[Bottleneck] = []

        # 收集每个 category 在各论文中的表现
        category_data: dict[str, list[tuple[str, dict[str, float]]]] = {}
        for paper_metrics in aggregate.per_paper:
            for cat, vals in paper_metrics.category_breakdown.items():
                if cat not in category_data:
                    category_data[cat] = []
                category_data[cat].append((paper_metrics.paper_id, vals))

        for cat, entries in category_data.items():
            if len(entries) < self.config.min_papers_for_category:
                continue

            recalls = [v.get("recall", 0.0) for _, v in entries]
            avg_recall = sum(recalls) / len(recalls) if recalls else 0.0

            if avg_recall < self.config.category_recall_threshold:
                affected = [pid for pid, _ in entries]
                severity = (
                    Severity.CRITICAL if avg_recall < 0.2
                    else Severity.HIGH if avg_recall < 0.3
                    else Severity.MEDIUM
                )
                bottlenecks.append(Bottleneck(
                    type=BottleneckType.CATEGORY_WEAKNESS,
                    severity=severity,
                    description=(
                        f"Category '{cat}' has systematically low recall "
                        f"(avg={avg_recall:.2f}) across {len(entries)} papers."
                    ),
                    evidence={
                        "category": cat,
                        "avg_recall": avg_recall,
                        "per_paper_recalls": {
                            pid: v.get("recall", 0.0) for pid, v in entries
                        },
                    },
                    recommendation=(
                        f"Review and improve the '{cat}' analysis skill or "
                        f"add dedicated tools/prompts for {cat} detection."
                    ),
                    affected_papers=affected,
                ))

        return bottlenecks

    def _check_efficiency(
        self,
        aggregate: AggregateQualityMetrics,
    ) -> list[Bottleneck]:
        """检测效率问题。"""
        bottlenecks: list[Bottleneck] = []

        low_eff_papers = [
            m for m in aggregate.per_paper
            if m.process.findings_per_1k_tokens < self.config.efficiency_threshold
            and m.process.total_tokens > 0
        ]

        if len(low_eff_papers) > len(aggregate.per_paper) * 0.3:
            severity = (
                Severity.HIGH
                if aggregate.avg_findings_per_1k_tokens < 0.2
                else Severity.MEDIUM
            )
            bottlenecks.append(Bottleneck(
                type=BottleneckType.EFFICIENCY_DEGRADATION,
                severity=severity,
                description=(
                    f"Token efficiency is low: avg {aggregate.avg_findings_per_1k_tokens:.3f} "
                    f"findings/1k tokens across {aggregate.num_papers} papers. "
                    f"{len(low_eff_papers)} papers below threshold."
                ),
                evidence={
                    "avg_efficiency": aggregate.avg_findings_per_1k_tokens,
                    "threshold": self.config.efficiency_threshold,
                    "low_eff_count": len(low_eff_papers),
                    "total_papers": aggregate.num_papers,
                },
                recommendation=(
                    "Consider improving compaction strategy, reducing "
                    "unnecessary context, or optimizing prompt length."
                ),
                affected_papers=[m.paper_id for m in low_eff_papers],
            ))

        return bottlenecks

    def _check_tool_reliability(
        self,
        aggregate: AggregateQualityMetrics,
    ) -> list[Bottleneck]:
        """检测工具可靠性问题。"""
        bottlenecks: list[Bottleneck] = []

        low_tool_papers = [
            m for m in aggregate.per_paper
            if m.process.tool_success_rate < self.config.tool_success_threshold
            and m.process.tool_calls_total > 0
        ]

        if low_tool_papers:
            avg_rate = (
                sum(m.process.tool_success_rate for m in low_tool_papers)
                / len(low_tool_papers)
            )
            severity = (
                Severity.CRITICAL if avg_rate < 0.5
                else Severity.HIGH if avg_rate < 0.7
                else Severity.MEDIUM
            )
            bottlenecks.append(Bottleneck(
                type=BottleneckType.TOOL_RELIABILITY,
                severity=severity,
                description=(
                    f"Tool success rate is low in {len(low_tool_papers)} papers "
                    f"(avg rate={avg_rate:.2f}). Indicates tool implementation issues."
                ),
                evidence={
                    "avg_success_rate": avg_rate,
                    "threshold": self.config.tool_success_threshold,
                    "affected_count": len(low_tool_papers),
                },
                recommendation=(
                    "Audit tool implementations for error handling. "
                    "Check for common failure patterns in tool call logs."
                ),
                affected_papers=[m.paper_id for m in low_tool_papers],
            ))

        return bottlenecks

    def _check_coverage_gaps(
        self,
        aggregate: AggregateQualityMetrics,
    ) -> list[Bottleneck]:
        """检测覆盖度不足。"""
        bottlenecks: list[Bottleneck] = []

        low_cov_papers = [
            m for m in aggregate.per_paper
            if m.process.read_coverage < self.config.coverage_threshold
            and m.process.total_sections > 0
        ]

        if len(low_cov_papers) > len(aggregate.per_paper) * 0.3:
            bottlenecks.append(Bottleneck(
                type=BottleneckType.COVERAGE_GAP,
                severity=Severity.HIGH,
                description=(
                    f"Reading coverage is below {self.config.coverage_threshold:.0%} "
                    f"in {len(low_cov_papers)}/{aggregate.num_papers} papers. "
                    f"Agent may be skipping important sections."
                ),
                evidence={
                    "avg_read_coverage": aggregate.avg_read_coverage,
                    "threshold": self.config.coverage_threshold,
                    "low_coverage_count": len(low_cov_papers),
                },
                recommendation=(
                    "Review phase planning to ensure all sections are read. "
                    "Consider adjusting token budget allocation across phases."
                ),
                affected_papers=[m.paper_id for m in low_cov_papers],
            ))

        return bottlenecks

    def _check_loop_stability(
        self,
        aggregate: AggregateQualityMetrics,
    ) -> list[Bottleneck]:
        """检测循环不稳定性。"""
        bottlenecks: list[Bottleneck] = []

        if aggregate.doom_loop_rate > self.config.doom_loop_rate_threshold:
            severity = (
                Severity.CRITICAL if aggregate.doom_loop_rate > 0.5
                else Severity.HIGH
            )
            bottlenecks.append(Bottleneck(
                type=BottleneckType.LOOP_INSTABILITY,
                severity=severity,
                description=(
                    f"Doom loop triggered in {aggregate.doom_loop_rate:.0%} of papers "
                    f"(threshold: {self.config.doom_loop_rate_threshold:.0%}). "
                    f"Agent is frequently getting stuck."
                ),
                evidence={
                    "doom_loop_rate": aggregate.doom_loop_rate,
                    "threshold": self.config.doom_loop_rate_threshold,
                },
                recommendation=(
                    "Review loop guard thresholds and recovery strategies. "
                    "Check if specific tool sequences are causing loops."
                ),
                affected_papers=[
                    m.paper_id for m in aggregate.per_paper
                    if m.process.doom_loop_triggered
                ],
            ))

        return bottlenecks

    def _check_phase_efficiency(
        self,
        aggregate: AggregateQualityMetrics,
    ) -> list[Bottleneck]:
        """检测 phase 流转低效。"""
        bottlenecks: list[Bottleneck] = []

        papers_with_regressions = [
            m for m in aggregate.per_paper
            if m.process.phase_transitions > 0
            and (m.process.phase_regressions / m.process.phase_transitions)
            > self.config.phase_regression_ratio_threshold
        ]

        if papers_with_regressions:
            bottlenecks.append(Bottleneck(
                type=BottleneckType.PHASE_INEFFICIENCY,
                severity=Severity.MEDIUM,
                description=(
                    f"High phase regression ratio in {len(papers_with_regressions)} papers. "
                    f"Agent is frequently reverting to earlier phases, "
                    f"indicating indecision or planning issues."
                ),
                evidence={
                    "affected_count": len(papers_with_regressions),
                    "regression_threshold": self.config.phase_regression_ratio_threshold,
                },
                recommendation=(
                    "Review phase transition conditions. Consider strengthening "
                    "phase completion criteria to reduce premature transitions."
                ),
                affected_papers=[m.paper_id for m in papers_with_regressions],
            ))

        return bottlenecks


# ============================================================
# Convenience: Generate summary report from bottlenecks
# ============================================================


def format_bottleneck_report(bottlenecks: list[Bottleneck]) -> str:
    """将瓶颈列表格式化为可读的 Markdown 报告段。"""
    if not bottlenecks:
        return "## Bottleneck Analysis\n\nNo significant bottlenecks detected.\n"

    lines = [
        "## Bottleneck Analysis",
        "",
        f"Found **{len(bottlenecks)} bottleneck(s)**:",
        "",
    ]

    for i, b in enumerate(bottlenecks, 1):
        lines.append(
            f"### {i}. [{b.severity.value.upper()}] {b.type.value}"
        )
        lines.append("")
        lines.append(f"**Description**: {b.description}")
        lines.append("")
        lines.append(f"**Recommendation**: {b.recommendation}")
        lines.append("")
        if b.affected_papers:
            lines.append(
                f"**Affected papers**: {', '.join(b.affected_papers[:5])}"
                + (f" (and {len(b.affected_papers) - 5} more)"
                   if len(b.affected_papers) > 5 else "")
            )
            lines.append("")

    return "\n".join(lines)
