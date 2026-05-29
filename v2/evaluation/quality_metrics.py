"""
evaluation/quality_metrics.py — ReviewQualityMetrics: 全面审稿质量度量。

Phase 5 (Meta-Harness) 核心数据类。包含两大维度：
    1. 内容质量 (content quality) — 基于 evaluation/metrics.py 的 P/R/F1
    2. 过程质量 (process quality) — 效率、循环次数、工具成功率等

设计原则:
    - 与 metrics.py 中的 EvalMetrics 互补而非替代
    - 支持序列化为 dict（方便 JSON 输出和报告生成）
    - 支持跨论文聚合
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ============================================================
# Process Quality Metrics
# ============================================================


@dataclass
class ProcessMetrics:
    """审稿过程的质量指标。从 session 运行数据中提取。"""

    # 效率指标
    loop_turns: int = 0                  # 总循环轮次
    total_tokens: int = 0                # 总 token 消耗
    findings_per_turn: float = 0.0       # 发现/轮次 效率
    findings_per_1k_tokens: float = 0.0  # 发现/千token 效率

    # 循环健康
    doom_loop_triggered: bool = False     # 是否触发过 doom loop
    doom_loop_count: int = 0              # doom loop 触发次数
    recovery_success_rate: float = 0.0    # 恢复成功率 (成功恢复次数/总触发次数)

    # Phase 流转
    phase_transitions: int = 0           # phase 切换次数
    phase_regressions: int = 0           # phase 回退次数（过多表示犹豫不决）

    # 工具使用
    tool_calls_total: int = 0            # 工具调用总数
    tool_calls_success: int = 0          # 成功的工具调用
    tool_success_rate: float = 0.0       # 工具调用成功率

    # 覆盖度
    sections_read: int = 0               # 已读章节数
    total_sections: int = 0              # 总章节数
    read_coverage: float = 0.0           # 阅读覆盖率
    pcg_coverage: float = 0.0            # Paper Cognition Graph 覆盖率

    # 反思系统
    emergency_reflect_triggered: bool = False  # 紧急反思是否触发
    fast_reflect_alerts: int = 0              # 快速反思 alert 数
    deep_reflect_ran: bool = False            # 深度反思是否执行

    def to_dict(self) -> dict[str, Any]:
        return {
            "loop_turns": self.loop_turns,
            "total_tokens": self.total_tokens,
            "findings_per_turn": round(self.findings_per_turn, 4),
            "findings_per_1k_tokens": round(self.findings_per_1k_tokens, 4),
            "doom_loop_triggered": self.doom_loop_triggered,
            "doom_loop_count": self.doom_loop_count,
            "recovery_success_rate": round(self.recovery_success_rate, 4),
            "phase_transitions": self.phase_transitions,
            "phase_regressions": self.phase_regressions,
            "tool_calls_total": self.tool_calls_total,
            "tool_calls_success": self.tool_calls_success,
            "tool_success_rate": round(self.tool_success_rate, 4),
            "sections_read": self.sections_read,
            "total_sections": self.total_sections,
            "read_coverage": round(self.read_coverage, 4),
            "pcg_coverage": round(self.pcg_coverage, 4),
            "emergency_reflect_triggered": self.emergency_reflect_triggered,
            "fast_reflect_alerts": self.fast_reflect_alerts,
            "deep_reflect_ran": self.deep_reflect_ran,
        }


# ============================================================
# Combined Quality Metrics
# ============================================================


@dataclass
class ReviewQualityMetrics:
    """单篇论文的完整审稿质量度量（内容 + 过程）。

    内容质量来自 evaluation/metrics.py 的 EvalMetrics（P/R/F1），
    过程质量来自 ProcessMetrics。
    """

    paper_id: str

    # 内容质量（从 EvalMetrics 提取核心指标）
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    weighted_recall: float = 0.0
    num_predicted: int = 0
    num_gold: int = 0
    num_matched: int = 0
    category_breakdown: dict[str, dict[str, float]] = field(default_factory=dict)

    # 过程质量
    process: ProcessMetrics = field(default_factory=ProcessMetrics)

    # 综合质量评分（加权平均）
    overall_score: float = 0.0

    def compute_overall_score(
        self,
        content_weight: float = 0.6,
        efficiency_weight: float = 0.2,
        robustness_weight: float = 0.2,
    ) -> float:
        """计算综合质量评分。

        Args:
            content_weight: 内容质量权重 (P/R/F1)
            efficiency_weight: 效率权重 (token efficiency)
            robustness_weight: 鲁棒性权重 (无 loop, 高工具成功率)

        Returns:
            综合评分 [0, 1]
        """
        # 内容维度: F1 as primary signal
        content_score = self.f1

        # 效率维度: normalize findings_per_1k_tokens (capped at reasonable max)
        # 经验值: 好的 agent 应该每 1000 token 产出 0.5-2 个 finding
        eff_raw = min(self.process.findings_per_1k_tokens / 2.0, 1.0)
        efficiency_score = eff_raw

        # 鲁棒性维度: 没有 doom loop + 高工具成功率 + 高覆盖率
        robustness_components = [
            1.0 if not self.process.doom_loop_triggered else 0.5,
            self.process.tool_success_rate,
            self.process.read_coverage,
        ]
        robustness_score = (
            sum(robustness_components) / len(robustness_components)
            if robustness_components
            else 0.0
        )

        self.overall_score = (
            content_weight * content_score
            + efficiency_weight * efficiency_score
            + robustness_weight * robustness_score
        )
        return self.overall_score

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "content_quality": {
                "precision": round(self.precision, 4),
                "recall": round(self.recall, 4),
                "f1": round(self.f1, 4),
                "weighted_recall": round(self.weighted_recall, 4),
                "num_predicted": self.num_predicted,
                "num_gold": self.num_gold,
                "num_matched": self.num_matched,
                "category_breakdown": self.category_breakdown,
            },
            "process_quality": self.process.to_dict(),
            "overall_score": round(self.overall_score, 4),
        }


# ============================================================
# Aggregate Quality Metrics
# ============================================================


@dataclass
class AggregateQualityMetrics:
    """跨多篇论文的聚合质量度量。"""

    num_papers: int = 0

    # 内容质量聚合 (macro-average)
    avg_precision: float = 0.0
    avg_recall: float = 0.0
    avg_f1: float = 0.0
    avg_weighted_recall: float = 0.0

    # 过程质量聚合
    avg_loop_turns: float = 0.0
    avg_tokens: float = 0.0
    avg_findings_per_turn: float = 0.0
    avg_findings_per_1k_tokens: float = 0.0
    avg_tool_success_rate: float = 0.0
    avg_read_coverage: float = 0.0
    doom_loop_rate: float = 0.0  # 触发 doom loop 的论文比例

    # 综合
    avg_overall_score: float = 0.0

    # 逐篇详情
    per_paper: list[ReviewQualityMetrics] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "num_papers": self.num_papers,
            "content_quality": {
                "avg_precision": round(self.avg_precision, 4),
                "avg_recall": round(self.avg_recall, 4),
                "avg_f1": round(self.avg_f1, 4),
                "avg_weighted_recall": round(self.avg_weighted_recall, 4),
            },
            "process_quality": {
                "avg_loop_turns": round(self.avg_loop_turns, 2),
                "avg_tokens": round(self.avg_tokens, 0),
                "avg_findings_per_turn": round(self.avg_findings_per_turn, 4),
                "avg_findings_per_1k_tokens": round(self.avg_findings_per_1k_tokens, 4),
                "avg_tool_success_rate": round(self.avg_tool_success_rate, 4),
                "avg_read_coverage": round(self.avg_read_coverage, 4),
                "doom_loop_rate": round(self.doom_loop_rate, 4),
            },
            "avg_overall_score": round(self.avg_overall_score, 4),
            "per_paper": [m.to_dict() for m in self.per_paper],
        }


def compute_aggregate_quality(
    per_paper: list[ReviewQualityMetrics],
) -> AggregateQualityMetrics:
    """从逐篇质量度量计算聚合指标。"""
    if not per_paper:
        return AggregateQualityMetrics()

    n = len(per_paper)

    # 先确保每篇都有 overall_score
    for m in per_paper:
        if m.overall_score == 0.0:
            m.compute_overall_score()

    return AggregateQualityMetrics(
        num_papers=n,
        # 内容
        avg_precision=sum(m.precision for m in per_paper) / n,
        avg_recall=sum(m.recall for m in per_paper) / n,
        avg_f1=sum(m.f1 for m in per_paper) / n,
        avg_weighted_recall=sum(m.weighted_recall for m in per_paper) / n,
        # 过程
        avg_loop_turns=sum(m.process.loop_turns for m in per_paper) / n,
        avg_tokens=sum(m.process.total_tokens for m in per_paper) / n,
        avg_findings_per_turn=sum(m.process.findings_per_turn for m in per_paper) / n,
        avg_findings_per_1k_tokens=sum(
            m.process.findings_per_1k_tokens for m in per_paper
        ) / n,
        avg_tool_success_rate=sum(m.process.tool_success_rate for m in per_paper) / n,
        avg_read_coverage=sum(m.process.read_coverage for m in per_paper) / n,
        doom_loop_rate=sum(
            1 for m in per_paper if m.process.doom_loop_triggered
        ) / n,
        # 综合
        avg_overall_score=sum(m.overall_score for m in per_paper) / n,
        per_paper=per_paper,
    )
