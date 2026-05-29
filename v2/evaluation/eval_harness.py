"""
evaluation/eval_harness.py — Meta-Harness: 批量评估运行器（Phase 5 核心模块）。

整合 metrics.py、quality_metrics.py、process_collector.py、bottleneck_analyzer.py，
提供统一的批量评估 API。

功能:
    1. 批量运行 Agent 审稿（mock / real 模式）
    2. 收集内容质量（P/R/F1）+ 过程质量指标
    3. 计算综合质量分数
    4. 识别系统性瓶颈
    5. 生成详细评估报告

Kill Switch: GODEL_META_HARNESS_ENABLED (core/godel_config.py)
    - 默认开启
    - 设为 "0" 时退化为只运行基础 P/R/F1（即 run_eval.py 原有行为）
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from evaluation.metrics import (
    Finding,
    EvalMetrics,
    compute_metrics,
    compute_aggregate,
)
from evaluation.quality_metrics import (
    ProcessMetrics,
    ReviewQualityMetrics,
    AggregateQualityMetrics,
    compute_aggregate_quality,
)
from evaluation.bottleneck_analyzer import (
    BottleneckAnalyzer,
    AnalyzerConfig,
    Bottleneck,
    format_bottleneck_report,
)

logger = logging.getLogger(__name__)


# ============================================================
# Test Paper Protocol
# ============================================================


@dataclass
class EvalPaper:
    """一篇用于评估的测试论文。"""
    paper_id: str
    title: str = ""
    description: str = ""
    sections: list[str] = field(default_factory=list)
    gold_findings: list[Finding] = field(default_factory=list)
    paper_path: str | None = None  # PDF/MD 文件路径
    metadata: dict[str, Any] = field(default_factory=dict)


# Alias for backward compat and intuitive naming in test contexts
TestPaper = EvalPaper
TestPaper.__test__ = False  # Prevent pytest from collecting this as a test class


@dataclass
class RunResult:
    """单次运行的结果。"""
    paper_id: str
    predicted_findings: list[Finding] = field(default_factory=list)
    process_metrics: ProcessMetrics = field(default_factory=ProcessMetrics)
    run_time_seconds: float = 0.0
    error: str | None = None


# ============================================================
# Agent Runner Protocol
# ============================================================


class AgentRunner(Protocol):
    """Agent 运行器的协议接口。实际 / Mock agent 均需实现此协议。"""

    def run(self, paper: TestPaper) -> RunResult:
        """运行 Agent 审稿并返回结果。"""
        ...


# ============================================================
# Batch Result
# ============================================================


@dataclass
class BatchResult:
    """批量评估的完整结果。"""
    # 配置
    config_name: str = ""
    timestamp: str = ""

    # 质量度量
    aggregate: AggregateQualityMetrics = field(
        default_factory=AggregateQualityMetrics
    )

    # 瓶颈
    bottlenecks: list[Bottleneck] = field(default_factory=list)

    # 运行统计
    total_run_time_seconds: float = 0.0
    papers_succeeded: int = 0
    papers_failed: int = 0

    # 原始数据
    run_results: list[RunResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_name": self.config_name,
            "timestamp": self.timestamp,
            "aggregate": self.aggregate.to_dict(),
            "bottlenecks": [b.to_dict() for b in self.bottlenecks],
            "run_stats": {
                "total_run_time_seconds": round(self.total_run_time_seconds, 2),
                "papers_succeeded": self.papers_succeeded,
                "papers_failed": self.papers_failed,
            },
        }


# ============================================================
# Evaluation Harness
# ============================================================


class EvaluationHarness:
    """Meta-Harness: 批量评估运行器。

    Usage:
        harness = EvaluationHarness(
            test_papers=load_test_papers(),
            runner=MockAgentRunner(),
        )
        result = harness.run_batch(config_name="v3_all_features")
        print(f"F1: {result.aggregate.avg_f1:.3f}")
        for b in result.bottlenecks:
            print(f"Bottleneck: {b.description}")
    """

    def __init__(
        self,
        test_papers: list[TestPaper],
        runner: AgentRunner,
        match_threshold: float = 0.4,
        analyzer_config: AnalyzerConfig | None = None,
    ):
        """初始化评估 Harness。

        Args:
            test_papers: 测试论文集合（含 gold standard）
            runner: Agent 运行器
            match_threshold: Finding 匹配阈值
            analyzer_config: 瓶颈分析器配置
        """
        self.test_papers = test_papers
        self.runner = runner
        self.match_threshold = match_threshold
        self.analyzer = BottleneckAnalyzer(analyzer_config)

    def run_batch(
        self,
        config_name: str = "default",
        paper_ids: list[str] | None = None,
    ) -> BatchResult:
        """运行批量评估。

        Args:
            config_name: 配置名称（用于报告标识）
            paper_ids: 可选，只评估指定论文

        Returns:
            BatchResult 包含完整评估结果
        """
        start_time = time.time()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 筛选论文
        papers = self.test_papers
        if paper_ids:
            papers = [p for p in papers if p.paper_id in paper_ids]

        # 逐篇运行
        per_paper_metrics: list[ReviewQualityMetrics] = []
        run_results: list[RunResult] = []
        papers_succeeded = 0
        papers_failed = 0

        for paper in papers:
            logger.info("Evaluating: %s", paper.paper_id)
            try:
                result = self.runner.run(paper)
                run_results.append(result)

                if result.error:
                    logger.warning(
                        "Paper %s failed: %s", paper.paper_id, result.error
                    )
                    papers_failed += 1
                    continue

                papers_succeeded += 1

                # 计算内容质量
                content_metrics = compute_metrics(
                    paper.paper_id,
                    result.predicted_findings,
                    paper.gold_findings,
                    threshold=self.match_threshold,
                )

                # 组合为 ReviewQualityMetrics
                quality = ReviewQualityMetrics(
                    paper_id=paper.paper_id,
                    precision=content_metrics.precision,
                    recall=content_metrics.recall,
                    f1=content_metrics.f1,
                    weighted_recall=content_metrics.weighted_recall,
                    num_predicted=content_metrics.num_predicted,
                    num_gold=content_metrics.num_gold,
                    num_matched=content_metrics.num_matched,
                    category_breakdown=content_metrics.category_breakdown,
                    process=result.process_metrics,
                )
                quality.compute_overall_score()
                per_paper_metrics.append(quality)

            except Exception as e:
                logger.error("Unexpected error for %s: %s", paper.paper_id, e)
                papers_failed += 1
                run_results.append(RunResult(
                    paper_id=paper.paper_id,
                    error=str(e),
                ))

        # 聚合
        aggregate = compute_aggregate_quality(per_paper_metrics)

        # 瓶颈分析
        bottlenecks = self.analyzer.analyze(aggregate)

        total_time = time.time() - start_time

        return BatchResult(
            config_name=config_name,
            timestamp=timestamp,
            aggregate=aggregate,
            bottlenecks=bottlenecks,
            total_run_time_seconds=total_time,
            papers_succeeded=papers_succeeded,
            papers_failed=papers_failed,
            run_results=run_results,
        )

    def compare(
        self,
        config_a_name: str,
        config_b_name: str,
        runner_a: AgentRunner,
        runner_b: AgentRunner,
        paper_ids: list[str] | None = None,
    ) -> tuple[BatchResult, BatchResult, dict[str, Any]]:
        """对比两个配置的评估结果。

        Args:
            config_a_name: 配置 A 名称
            config_b_name: 配置 B 名称
            runner_a: 配置 A 的 runner
            runner_b: 配置 B 的 runner
            paper_ids: 可选，只评估指定论文

        Returns:
            (result_a, result_b, delta_summary)
        """
        # 运行 A
        self.runner = runner_a
        result_a = self.run_batch(config_a_name, paper_ids)

        # 运行 B
        self.runner = runner_b
        result_b = self.run_batch(config_b_name, paper_ids)

        # 计算 delta
        delta = {
            "precision_delta": (
                result_a.aggregate.avg_precision - result_b.aggregate.avg_precision
            ),
            "recall_delta": (
                result_a.aggregate.avg_recall - result_b.aggregate.avg_recall
            ),
            "f1_delta": (
                result_a.aggregate.avg_f1 - result_b.aggregate.avg_f1
            ),
            "efficiency_delta": (
                result_a.aggregate.avg_findings_per_1k_tokens
                - result_b.aggregate.avg_findings_per_1k_tokens
            ),
            "overall_score_delta": (
                result_a.aggregate.avg_overall_score
                - result_b.aggregate.avg_overall_score
            ),
        }

        return result_a, result_b, delta


# ============================================================
# Report Generation
# ============================================================


def generate_evaluation_report(batch_result: BatchResult) -> str:
    """生成完整的评估报告（Markdown 格式）。"""
    lines = [
        "# Meta-Harness Evaluation Report",
        "",
        f"**Config**: {batch_result.config_name}",
        f"**Timestamp**: {batch_result.timestamp}",
        f"**Papers**: {batch_result.papers_succeeded} succeeded, "
        f"{batch_result.papers_failed} failed",
        f"**Total Run Time**: {batch_result.total_run_time_seconds:.1f}s",
        "",
        "---",
        "",
        "## Content Quality (Aggregate)",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Precision | {batch_result.aggregate.avg_precision:.4f} |",
        f"| Recall | {batch_result.aggregate.avg_recall:.4f} |",
        f"| F1 | {batch_result.aggregate.avg_f1:.4f} |",
        f"| Weighted Recall | {batch_result.aggregate.avg_weighted_recall:.4f} |",
        "",
        "## Process Quality (Aggregate)",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Avg Loop Turns | {batch_result.aggregate.avg_loop_turns:.1f} |",
        f"| Avg Tokens | {batch_result.aggregate.avg_tokens:.0f} |",
        f"| Avg Findings/Turn | {batch_result.aggregate.avg_findings_per_turn:.3f} |",
        f"| Avg Findings/1k Tokens | "
        f"{batch_result.aggregate.avg_findings_per_1k_tokens:.3f} |",
        f"| Avg Tool Success Rate | {batch_result.aggregate.avg_tool_success_rate:.3f} |",
        f"| Avg Read Coverage | {batch_result.aggregate.avg_read_coverage:.3f} |",
        f"| Doom Loop Rate | {batch_result.aggregate.doom_loop_rate:.3f} |",
        "",
        f"## Overall Score: {batch_result.aggregate.avg_overall_score:.4f}",
        "",
        "---",
        "",
    ]

    # 瓶颈分析
    lines.append(format_bottleneck_report(batch_result.bottlenecks))
    lines.append("")
    lines.append("---")
    lines.append("")

    # 逐篇详情
    lines.append("## Per-Paper Results")
    lines.append("")

    for paper_m in batch_result.aggregate.per_paper:
        lines.append(f"### {paper_m.paper_id}")
        lines.append("")
        lines.append(
            f"- **Content**: P={paper_m.precision:.3f} R={paper_m.recall:.3f} "
            f"F1={paper_m.f1:.3f} ({paper_m.num_matched}/{paper_m.num_gold} matched)"
        )
        lines.append(
            f"- **Process**: {paper_m.process.loop_turns} turns, "
            f"{paper_m.process.total_tokens} tokens, "
            f"coverage={paper_m.process.read_coverage:.2f}"
        )
        lines.append(f"- **Overall Score**: {paper_m.overall_score:.4f}")
        lines.append("")

    return "\n".join(lines)


# ============================================================
# Helper: Load TestPaper from gold_standard/*.json
# ============================================================


def load_test_papers_from_gold(gold_dir: Path) -> list[TestPaper]:
    """从 gold_standard 目录加载测试论文。"""
    papers: list[TestPaper] = []

    for f in sorted(gold_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load %s: %s", f, e)
            continue

        # Support both formats: legacy "findings" and new "gold_findings"
        raw_findings = data.get("findings", []) or data.get("gold_findings", [])
        gold_findings = [
            Finding(
                text=fd.get("text", fd.get("description", "")),
                section=fd.get("section", fd.get("location", "")),
                priority=fd.get("priority", fd.get("severity", "medium")),
                category=fd.get("category", ""),
            )
            for fd in raw_findings
        ]

        papers.append(TestPaper(
            paper_id=data["paper_id"],
            title=data.get("title", ""),
            description=data.get("description", ""),
            sections=data.get("sections", []),
            gold_findings=gold_findings,
            paper_path=data.get("paper_path"),
            metadata=data.get("metadata", {}),
        ))

    return papers
