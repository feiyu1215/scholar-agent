"""
tests/test_phase5_meta_harness.py — Phase 5 Meta-Harness 完整测试。

覆盖:
    1. quality_metrics.py: ProcessMetrics, ReviewQualityMetrics, AggregateQualityMetrics
    2. process_collector.py: collect_from_state, collect_from_metrics_file
    3. bottleneck_analyzer.py: BottleneckAnalyzer 各检测规则
    4. eval_harness.py: EvaluationHarness batch run + compare + report
    5. Kill Switch 降级行为
    6. 边界情况 (空输入、单论文、完美分数等)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.quality_metrics import (
    ProcessMetrics,
    ReviewQualityMetrics,
    AggregateQualityMetrics,
    compute_aggregate_quality,
)
from evaluation.process_collector import (
    collect_from_state,
    collect_from_metrics_file,
    collect_all_sessions,
)
from evaluation.bottleneck_analyzer import (
    BottleneckAnalyzer,
    BottleneckType,
    Severity,
    Bottleneck,
    AnalyzerConfig,
    format_bottleneck_report,
)
from evaluation.eval_harness import (
    EvaluationHarness,
    TestPaper,
    RunResult,
    BatchResult,
    generate_evaluation_report,
    load_test_papers_from_gold,
)
from evaluation.metrics import Finding


# ============================================================
# Helpers
# ============================================================


def _make_process_metrics(**kwargs) -> ProcessMetrics:
    """Create ProcessMetrics with sensible defaults."""
    defaults = {
        "loop_turns": 20,
        "total_tokens": 50000,
        "findings_per_turn": 0.3,
        "findings_per_1k_tokens": 0.12,
        "tool_calls_total": 40,
        "tool_calls_success": 36,
        "tool_success_rate": 0.9,
        "sections_read": 6,
        "total_sections": 8,
        "read_coverage": 0.75,
        "pcg_coverage": 0.8,
        "phase_transitions": 5,
    }
    defaults.update(kwargs)
    return ProcessMetrics(**defaults)


def _make_quality_metrics(paper_id: str, **kwargs) -> ReviewQualityMetrics:
    """Create ReviewQualityMetrics with sensible defaults."""
    defaults = {
        "precision": 0.7,
        "recall": 0.6,
        "f1": 0.646,
        "weighted_recall": 0.55,
        "num_predicted": 7,
        "num_gold": 8,
        "num_matched": 5,
        "category_breakdown": {
            "methodology": {"precision": 0.8, "recall": 0.7, "f1": 0.75, "num_predicted": 3, "num_gold": 3},
            "data": {"precision": 0.5, "recall": 0.4, "f1": 0.44, "num_predicted": 2, "num_gold": 3},
        },
    }
    defaults.update(kwargs)
    process = defaults.pop("process", _make_process_metrics())
    m = ReviewQualityMetrics(paper_id=paper_id, process=process, **defaults)
    m.compute_overall_score()
    return m


class MockState:
    """Mock state object mimicking WorkspaceState."""

    def __init__(self):
        self.findings = [MagicMock() for _ in range(6)]
        self.loop_turns = 25
        self.total_tokens = 60000
        self.sections_read = ["intro", "methods", "results", "discussion", "conclusion"]
        self.paper_sections = {
            "intro": "...", "lit_review": "...", "methods": "...",
            "results": "...", "discussion": "...", "conclusion": "...",
            "appendix": "...",
        }
        self.paper_cognition_graph = None
        self.reflection_stats = {
            "emergency_triggered": False,
            "fast_reflect_alerts": 2,
            "deep_reflect_ran": True,
        }


class MockRunner:
    """A simple mock AgentRunner for testing."""

    def __init__(self, findings_count: int = 5, process_kwargs: dict | None = None):
        self.findings_count = findings_count
        self.process_kwargs = process_kwargs or {}

    def run(self, paper: TestPaper) -> RunResult:
        # Return some findings that partially match gold
        predicted = []
        for i, gf in enumerate(paper.gold_findings[:self.findings_count]):
            predicted.append(Finding(
                text=gf.text,  # Exact match for simplicity
                section=gf.section,
                priority=gf.priority,
                category=gf.category,
            ))
        # Add a false positive
        predicted.append(Finding(
            text="Minor formatting issue in abstract.",
            section="abstract",
            priority="low",
            category="presentation",
        ))
        return RunResult(
            paper_id=paper.paper_id,
            predicted_findings=predicted,
            process_metrics=_make_process_metrics(**self.process_kwargs),
            run_time_seconds=1.5,
        )


# ============================================================
# Test: quality_metrics.py
# ============================================================


class TestProcessMetrics(unittest.TestCase):
    """Test ProcessMetrics dataclass."""

    def test_default_values(self):
        pm = ProcessMetrics()
        self.assertEqual(pm.loop_turns, 0)
        self.assertEqual(pm.total_tokens, 0)
        self.assertFalse(pm.doom_loop_triggered)
        self.assertEqual(pm.tool_success_rate, 0.0)

    def test_to_dict(self):
        pm = _make_process_metrics()
        d = pm.to_dict()
        self.assertIn("loop_turns", d)
        self.assertIn("tool_success_rate", d)
        self.assertEqual(d["loop_turns"], 20)
        self.assertAlmostEqual(d["tool_success_rate"], 0.9, places=4)

    def test_all_fields_present(self):
        pm = ProcessMetrics()
        d = pm.to_dict()
        expected_keys = {
            "loop_turns", "total_tokens", "findings_per_turn",
            "findings_per_1k_tokens", "doom_loop_triggered", "doom_loop_count",
            "recovery_success_rate", "phase_transitions", "phase_regressions",
            "tool_calls_total", "tool_calls_success", "tool_success_rate",
            "sections_read", "total_sections", "read_coverage", "pcg_coverage",
            "emergency_reflect_triggered", "fast_reflect_alerts", "deep_reflect_ran",
        }
        self.assertEqual(set(d.keys()), expected_keys)


class TestReviewQualityMetrics(unittest.TestCase):
    """Test ReviewQualityMetrics dataclass."""

    def test_compute_overall_score_balanced(self):
        m = _make_quality_metrics("test_001")
        score = m.overall_score
        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_compute_overall_score_perfect(self):
        m = ReviewQualityMetrics(
            paper_id="perfect",
            precision=1.0,
            recall=1.0,
            f1=1.0,
            weighted_recall=1.0,
            num_predicted=8,
            num_gold=8,
            num_matched=8,
            process=ProcessMetrics(
                findings_per_1k_tokens=2.0,
                tool_success_rate=1.0,
                read_coverage=1.0,
                doom_loop_triggered=False,
            ),
        )
        score = m.compute_overall_score()
        # With perfect metrics: 0.6*1.0 + 0.2*1.0 + 0.2*1.0 = 1.0
        self.assertAlmostEqual(score, 1.0, places=3)

    def test_compute_overall_score_zero(self):
        m = ReviewQualityMetrics(paper_id="zero")
        score = m.compute_overall_score()
        # F1=0, efficiency=0, but robustness components:
        # no doom loop = 1.0, tool_success=0, read_coverage=0 → 1/3
        # overall = 0.6*0 + 0.2*0 + 0.2*(1/3) ≈ 0.0667
        self.assertAlmostEqual(score, 0.0667, places=3)

    def test_to_dict_structure(self):
        m = _make_quality_metrics("test_002")
        d = m.to_dict()
        self.assertIn("content_quality", d)
        self.assertIn("process_quality", d)
        self.assertIn("overall_score", d)
        self.assertEqual(d["paper_id"], "test_002")
        self.assertIn("precision", d["content_quality"])
        self.assertIn("loop_turns", d["process_quality"])

    def test_custom_weights(self):
        m = ReviewQualityMetrics(
            paper_id="custom_w",
            f1=0.8,
            process=ProcessMetrics(
                findings_per_1k_tokens=1.0,
                tool_success_rate=0.9,
                read_coverage=0.8,
                doom_loop_triggered=False,
            ),
        )
        score = m.compute_overall_score(
            content_weight=1.0,
            efficiency_weight=0.0,
            robustness_weight=0.0,
        )
        self.assertAlmostEqual(score, 0.8, places=3)


class TestAggregateQualityMetrics(unittest.TestCase):
    """Test compute_aggregate_quality."""

    def test_empty_input(self):
        agg = compute_aggregate_quality([])
        self.assertEqual(agg.num_papers, 0)
        self.assertEqual(agg.avg_f1, 0.0)

    def test_single_paper(self):
        m = _make_quality_metrics("p1", f1=0.7)
        agg = compute_aggregate_quality([m])
        self.assertEqual(agg.num_papers, 1)
        self.assertAlmostEqual(agg.avg_f1, 0.7, places=3)

    def test_multiple_papers(self):
        papers = [
            _make_quality_metrics("p1", f1=0.6, precision=0.7, recall=0.5),
            _make_quality_metrics("p2", f1=0.8, precision=0.85, recall=0.75),
        ]
        agg = compute_aggregate_quality(papers)
        self.assertEqual(agg.num_papers, 2)
        self.assertAlmostEqual(agg.avg_f1, 0.7, places=3)
        self.assertAlmostEqual(agg.avg_precision, 0.775, places=3)

    def test_doom_loop_rate(self):
        papers = [
            _make_quality_metrics(
                "p1",
                process=_make_process_metrics(doom_loop_triggered=True),
            ),
            _make_quality_metrics(
                "p2",
                process=_make_process_metrics(doom_loop_triggered=False),
            ),
            _make_quality_metrics(
                "p3",
                process=_make_process_metrics(doom_loop_triggered=False),
            ),
        ]
        agg = compute_aggregate_quality(papers)
        self.assertAlmostEqual(agg.doom_loop_rate, 1 / 3, places=3)

    def test_to_dict(self):
        papers = [_make_quality_metrics("p1")]
        agg = compute_aggregate_quality(papers)
        d = agg.to_dict()
        self.assertIn("content_quality", d)
        self.assertIn("process_quality", d)
        self.assertIn("avg_overall_score", d)
        self.assertEqual(d["num_papers"], 1)


# ============================================================
# Test: process_collector.py
# ============================================================


class TestCollectFromState(unittest.TestCase):
    """Test collect_from_state."""

    def test_basic_collection(self):
        state = MockState()
        pm = collect_from_state(state)
        self.assertEqual(pm.loop_turns, 25)
        self.assertEqual(pm.total_tokens, 60000)
        self.assertEqual(pm.sections_read, 5)
        self.assertEqual(pm.total_sections, 7)
        self.assertAlmostEqual(pm.read_coverage, 5 / 7, places=3)
        self.assertAlmostEqual(pm.findings_per_turn, 6 / 25, places=3)

    def test_with_loop_guard_stats(self):
        state = MockState()
        lg_stats = {
            "doom_loop_triggered": True,
            "doom_loop_count": 2,
            "recovery_attempts": 2,
            "recovery_successes": 1,
        }
        pm = collect_from_state(state, loop_guard_stats=lg_stats)
        self.assertTrue(pm.doom_loop_triggered)
        self.assertEqual(pm.doom_loop_count, 2)
        self.assertAlmostEqual(pm.recovery_success_rate, 0.5)

    def test_with_tool_call_stats(self):
        state = MockState()
        tc_stats = {
            "total_calls": 50,
            "successful_calls": 45,
            "phase_transitions": 8,
            "phase_regressions": 2,
        }
        pm = collect_from_state(state, tool_call_stats=tc_stats)
        self.assertEqual(pm.tool_calls_total, 50)
        self.assertEqual(pm.tool_calls_success, 45)
        self.assertAlmostEqual(pm.tool_success_rate, 0.9)
        self.assertEqual(pm.phase_transitions, 8)
        self.assertEqual(pm.phase_regressions, 2)

    def test_empty_state(self):
        """State with minimal attributes."""
        state = MagicMock()
        state.findings = []
        state.loop_turns = 0
        state.total_tokens = 0
        state.sections_read = []
        state.paper_sections = {}
        state.paper_cognition_graph = None
        state.reflection_stats = {}
        pm = collect_from_state(state)
        self.assertEqual(pm.loop_turns, 0)
        self.assertEqual(pm.findings_per_turn, 0.0)


class TestCollectFromMetricsFile(unittest.TestCase):
    """Test collect_from_metrics_file."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.metrics_dir = Path(self.tmpdir)

    def _write_session_summary(self, records: list[dict]):
        """Write session summary records to tmp file."""
        f = self.metrics_dir / "session_summary.jsonl"
        with f.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")

    def test_basic_load(self):
        self._write_session_summary([{
            "timestamp": "2026-05-28T10:00:00+00:00",
            "session_id": "paper_001",
            "event_type": "session_summary",
            "payload": {
                "loop_turns": 30,
                "total_tokens": 70000,
                "findings_per_turn": 0.2,
                "findings_per_1k_tokens": 0.086,
                "sections_read": 5,
                "total_sections": 7,
                "read_ratio": 0.714,
                "pcg_coverage": 0.85,
                "emergency_triggered": False,
                "fast_reflect_alerts": 1,
                "deep_reflect_ran": True,
            },
        }])

        pm = collect_from_metrics_file(self.metrics_dir, "paper_001")
        self.assertEqual(pm.loop_turns, 30)
        self.assertEqual(pm.total_tokens, 70000)
        self.assertAlmostEqual(pm.read_coverage, 0.714, places=3)
        self.assertTrue(pm.deep_reflect_ran)

    def test_session_not_found(self):
        self._write_session_summary([{
            "timestamp": "2026-05-28T10:00:00+00:00",
            "session_id": "paper_001",
            "event_type": "session_summary",
            "payload": {"loop_turns": 10},
        }])
        pm = collect_from_metrics_file(self.metrics_dir, "nonexistent")
        self.assertEqual(pm.loop_turns, 0)

    def test_file_not_found(self):
        pm = collect_from_metrics_file(Path("/nonexistent/path"), "any")
        self.assertEqual(pm.loop_turns, 0)

    def test_multiple_sessions_returns_latest(self):
        self._write_session_summary([
            {
                "session_id": "s1",
                "event_type": "session_summary",
                "payload": {"loop_turns": 10, "total_tokens": 1000},
            },
            {
                "session_id": "s1",
                "event_type": "session_summary",
                "payload": {"loop_turns": 20, "total_tokens": 2000},
            },
        ])
        pm = collect_from_metrics_file(self.metrics_dir, "s1")
        # The function reads through all and keeps last match
        self.assertEqual(pm.loop_turns, 20)


class TestCollectAllSessions(unittest.TestCase):
    """Test collect_all_sessions."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.metrics_dir = Path(self.tmpdir)

    def test_multiple_sessions(self):
        f = self.metrics_dir / "session_summary.jsonl"
        with f.open("w", encoding="utf-8") as fh:
            for sid in ["s1", "s2", "s3"]:
                fh.write(json.dumps({
                    "session_id": sid,
                    "event_type": "session_summary",
                    "payload": {"loop_turns": 10},
                }) + "\n")

        results = collect_all_sessions(self.metrics_dir)
        self.assertEqual(len(results), 3)
        self.assertIn("s1", results)
        self.assertIn("s2", results)
        self.assertIn("s3", results)

    def test_empty_dir(self):
        results = collect_all_sessions(self.metrics_dir)
        self.assertEqual(results, {})


# ============================================================
# Test: bottleneck_analyzer.py
# ============================================================


class TestBottleneckAnalyzer(unittest.TestCase):
    """Test BottleneckAnalyzer detection rules."""

    def _make_aggregate(self, per_paper: list[ReviewQualityMetrics]) -> AggregateQualityMetrics:
        return compute_aggregate_quality(per_paper)

    def test_no_bottlenecks_healthy_system(self):
        """Healthy metrics should yield no bottlenecks."""
        papers = [
            _make_quality_metrics(
                f"p{i}",
                f1=0.8,
                process=_make_process_metrics(
                    tool_success_rate=0.95,
                    read_coverage=0.85,
                    findings_per_1k_tokens=0.5,
                    doom_loop_triggered=False,
                ),
                category_breakdown={
                    "methodology": {"recall": 0.7, "precision": 0.8, "f1": 0.75,
                                    "num_predicted": 3, "num_gold": 3},
                },
            )
            for i in range(3)
        ]
        agg = self._make_aggregate(papers)
        analyzer = BottleneckAnalyzer()
        bottlenecks = analyzer.analyze(agg)
        self.assertEqual(len(bottlenecks), 0)

    def test_category_weakness_detected(self):
        """Low recall in a category across multiple papers."""
        papers = [
            _make_quality_metrics(
                f"p{i}",
                category_breakdown={
                    "methodology": {"recall": 0.8, "precision": 0.8, "f1": 0.8,
                                    "num_predicted": 3, "num_gold": 3},
                    "statistics": {"recall": 0.2, "precision": 0.5, "f1": 0.29,
                                   "num_predicted": 1, "num_gold": 3},
                },
                process=_make_process_metrics(
                    tool_success_rate=0.9, read_coverage=0.8,
                    findings_per_1k_tokens=0.5,
                ),
            )
            for i in range(3)
        ]
        agg = self._make_aggregate(papers)
        analyzer = BottleneckAnalyzer()
        bottlenecks = analyzer.analyze(agg)

        cat_bottlenecks = [
            b for b in bottlenecks
            if b.type == BottleneckType.CATEGORY_WEAKNESS
        ]
        self.assertGreater(len(cat_bottlenecks), 0)
        self.assertIn("statistics", cat_bottlenecks[0].evidence.get("category", ""))

    def test_efficiency_degradation_detected(self):
        """Low token efficiency across many papers."""
        papers = [
            _make_quality_metrics(
                f"p{i}",
                process=_make_process_metrics(
                    findings_per_1k_tokens=0.05,
                    total_tokens=100000,
                    tool_success_rate=0.9,
                    read_coverage=0.8,
                ),
                category_breakdown={},
            )
            for i in range(5)
        ]
        agg = self._make_aggregate(papers)
        analyzer = BottleneckAnalyzer()
        bottlenecks = analyzer.analyze(agg)

        eff_bottlenecks = [
            b for b in bottlenecks
            if b.type == BottleneckType.EFFICIENCY_DEGRADATION
        ]
        self.assertGreater(len(eff_bottlenecks), 0)

    def test_tool_reliability_detected(self):
        """Low tool success rate."""
        papers = [
            _make_quality_metrics(
                f"p{i}",
                process=_make_process_metrics(
                    tool_success_rate=0.5,
                    tool_calls_total=30,
                    tool_calls_success=15,
                    findings_per_1k_tokens=0.5,
                    read_coverage=0.8,
                ),
                category_breakdown={},
            )
            for i in range(3)
        ]
        agg = self._make_aggregate(papers)
        analyzer = BottleneckAnalyzer()
        bottlenecks = analyzer.analyze(agg)

        tool_bottlenecks = [
            b for b in bottlenecks
            if b.type == BottleneckType.TOOL_RELIABILITY
        ]
        self.assertGreater(len(tool_bottlenecks), 0)

    def test_coverage_gap_detected(self):
        """Low read coverage across many papers."""
        papers = [
            _make_quality_metrics(
                f"p{i}",
                process=_make_process_metrics(
                    read_coverage=0.3,
                    total_sections=10,
                    sections_read=3,
                    tool_success_rate=0.9,
                    findings_per_1k_tokens=0.5,
                ),
                category_breakdown={},
            )
            for i in range(5)
        ]
        agg = self._make_aggregate(papers)
        analyzer = BottleneckAnalyzer()
        bottlenecks = analyzer.analyze(agg)

        cov_bottlenecks = [
            b for b in bottlenecks
            if b.type == BottleneckType.COVERAGE_GAP
        ]
        self.assertGreater(len(cov_bottlenecks), 0)

    def test_loop_instability_detected(self):
        """High doom loop rate."""
        papers = [
            _make_quality_metrics(
                f"p{i}",
                process=_make_process_metrics(
                    doom_loop_triggered=True,
                    tool_success_rate=0.9,
                    read_coverage=0.8,
                    findings_per_1k_tokens=0.5,
                ),
                category_breakdown={},
            )
            for i in range(4)
        ]
        agg = self._make_aggregate(papers)
        analyzer = BottleneckAnalyzer()
        bottlenecks = analyzer.analyze(agg)

        loop_bottlenecks = [
            b for b in bottlenecks
            if b.type == BottleneckType.LOOP_INSTABILITY
        ]
        self.assertGreater(len(loop_bottlenecks), 0)

    def test_phase_inefficiency_detected(self):
        """High phase regression ratio."""
        papers = [
            _make_quality_metrics(
                f"p{i}",
                process=_make_process_metrics(
                    phase_transitions=10,
                    phase_regressions=5,
                    tool_success_rate=0.9,
                    read_coverage=0.8,
                    findings_per_1k_tokens=0.5,
                ),
                category_breakdown={},
            )
            for i in range(3)
        ]
        agg = self._make_aggregate(papers)
        analyzer = BottleneckAnalyzer()
        bottlenecks = analyzer.analyze(agg)

        phase_bottlenecks = [
            b for b in bottlenecks
            if b.type == BottleneckType.PHASE_INEFFICIENCY
        ]
        self.assertGreater(len(phase_bottlenecks), 0)

    def test_severity_ordering(self):
        """Bottlenecks should be sorted by severity."""
        papers = [
            _make_quality_metrics(
                f"p{i}",
                process=_make_process_metrics(
                    doom_loop_triggered=True,
                    tool_success_rate=0.3,
                    tool_calls_total=30,
                    tool_calls_success=9,
                    read_coverage=0.3,
                    total_sections=10,
                    sections_read=3,
                    findings_per_1k_tokens=0.05,
                    total_tokens=100000,
                ),
                category_breakdown={
                    "statistics": {"recall": 0.1, "precision": 0.3, "f1": 0.15,
                                   "num_predicted": 1, "num_gold": 5},
                },
            )
            for i in range(5)
        ]
        agg = self._make_aggregate(papers)
        analyzer = BottleneckAnalyzer()
        bottlenecks = analyzer.analyze(agg)

        # Should have multiple, sorted by severity
        self.assertGreater(len(bottlenecks), 1)
        severities = [b.severity for b in bottlenecks]
        severity_order = {
            Severity.CRITICAL: 0, Severity.HIGH: 1,
            Severity.MEDIUM: 2, Severity.LOW: 3,
        }
        orders = [severity_order[s] for s in severities]
        self.assertEqual(orders, sorted(orders))

    def test_empty_aggregate(self):
        """Empty aggregate yields no bottlenecks."""
        agg = AggregateQualityMetrics()
        analyzer = BottleneckAnalyzer()
        bottlenecks = analyzer.analyze(agg)
        self.assertEqual(len(bottlenecks), 0)

    def test_custom_config_thresholds(self):
        """Custom config relaxes thresholds."""
        papers = [
            _make_quality_metrics(
                f"p{i}",
                process=_make_process_metrics(
                    tool_success_rate=0.7,
                    tool_calls_total=30,
                    tool_calls_success=21,
                    read_coverage=0.8,
                    findings_per_1k_tokens=0.5,
                ),
                category_breakdown={},
            )
            for i in range(3)
        ]
        agg = self._make_aggregate(papers)

        # Default threshold (0.8) would flag tool_success_rate=0.7
        analyzer_strict = BottleneckAnalyzer()
        bottlenecks_strict = analyzer_strict.analyze(agg)
        tool_strict = [b for b in bottlenecks_strict if b.type == BottleneckType.TOOL_RELIABILITY]
        self.assertGreater(len(tool_strict), 0)

        # Relaxed threshold (0.6) should not flag
        config = AnalyzerConfig(tool_success_threshold=0.6)
        analyzer_relaxed = BottleneckAnalyzer(config)
        bottlenecks_relaxed = analyzer_relaxed.analyze(agg)
        tool_relaxed = [b for b in bottlenecks_relaxed if b.type == BottleneckType.TOOL_RELIABILITY]
        self.assertEqual(len(tool_relaxed), 0)


class TestFormatBottleneckReport(unittest.TestCase):
    """Test format_bottleneck_report."""

    def test_no_bottlenecks(self):
        report = format_bottleneck_report([])
        self.assertIn("No significant bottlenecks", report)

    def test_with_bottlenecks(self):
        bottlenecks = [
            Bottleneck(
                type=BottleneckType.CATEGORY_WEAKNESS,
                severity=Severity.HIGH,
                description="Test description",
                recommendation="Fix it",
                affected_papers=["p1", "p2"],
            )
        ]
        report = format_bottleneck_report(bottlenecks)
        self.assertIn("HIGH", report)
        self.assertIn("Test description", report)
        self.assertIn("Fix it", report)
        self.assertIn("p1", report)


# ============================================================
# Test: eval_harness.py
# ============================================================


class TestEvaluationHarness(unittest.TestCase):
    """Test EvaluationHarness batch evaluation."""

    def setUp(self):
        self.test_papers = [
            TestPaper(
                paper_id="paper_001",
                title="Test Paper 1",
                gold_findings=[
                    Finding(text="Issue A in methodology", section="methodology",
                            priority="high", category="methodology"),
                    Finding(text="Issue B in data", section="results",
                            priority="medium", category="data"),
                    Finding(text="Issue C in logic", section="discussion",
                            priority="high", category="logic"),
                ],
            ),
            TestPaper(
                paper_id="paper_002",
                title="Test Paper 2",
                gold_findings=[
                    Finding(text="Missing citation X", section="related_work",
                            priority="medium", category="citation"),
                    Finding(text="Statistical error Y", section="results",
                            priority="critical", category="methodology"),
                ],
            ),
        ]

    def test_basic_batch_run(self):
        runner = MockRunner(findings_count=2)
        harness = EvaluationHarness(self.test_papers, runner)
        result = harness.run_batch(config_name="test_config")

        self.assertEqual(result.config_name, "test_config")
        self.assertEqual(result.papers_succeeded, 2)
        self.assertEqual(result.papers_failed, 0)
        self.assertGreater(result.aggregate.avg_f1, 0.0)
        self.assertGreater(result.total_run_time_seconds, 0.0)

    def test_batch_with_filter(self):
        runner = MockRunner(findings_count=2)
        harness = EvaluationHarness(self.test_papers, runner)
        result = harness.run_batch(paper_ids=["paper_001"])

        self.assertEqual(result.papers_succeeded, 1)
        self.assertEqual(result.aggregate.num_papers, 1)

    def test_failed_paper_handling(self):
        """Runner that returns error for specific paper."""
        class FailingRunner:
            def run(self, paper: TestPaper) -> RunResult:
                if paper.paper_id == "paper_002":
                    return RunResult(paper_id=paper.paper_id, error="Simulated failure")
                return RunResult(
                    paper_id=paper.paper_id,
                    predicted_findings=[
                        Finding(text="Found something", section="methodology",
                                priority="medium", category="methodology")
                    ],
                    process_metrics=_make_process_metrics(),
                )

        harness = EvaluationHarness(self.test_papers, FailingRunner())
        result = harness.run_batch()

        self.assertEqual(result.papers_succeeded, 1)
        self.assertEqual(result.papers_failed, 1)
        self.assertEqual(result.aggregate.num_papers, 1)

    def test_compare_mode(self):
        runner_a = MockRunner(findings_count=3)
        runner_b = MockRunner(findings_count=1)

        harness = EvaluationHarness(self.test_papers, runner_a)
        result_a, result_b, delta = harness.compare(
            "config_a", "config_b", runner_a, runner_b
        )

        self.assertGreater(result_a.aggregate.avg_recall, result_b.aggregate.avg_recall)
        self.assertGreater(delta["recall_delta"], 0)

    def test_bottleneck_analysis_integrated(self):
        """Verify bottleneck analysis runs as part of batch."""
        # Create a runner that always triggers doom loops
        runner = MockRunner(
            findings_count=2,
            process_kwargs={"doom_loop_triggered": True},
        )
        # Need enough papers to trigger
        papers = self.test_papers * 3  # 6 papers
        for i, p in enumerate(papers):
            p.paper_id = f"paper_{i:03d}"

        harness = EvaluationHarness(papers, runner)
        result = harness.run_batch()

        loop_bottlenecks = [
            b for b in result.bottlenecks
            if b.type == BottleneckType.LOOP_INSTABILITY
        ]
        self.assertGreater(len(loop_bottlenecks), 0)

    def test_overall_score_computed(self):
        runner = MockRunner(findings_count=2)
        harness = EvaluationHarness(self.test_papers, runner)
        result = harness.run_batch()

        for paper_m in result.aggregate.per_paper:
            self.assertGreater(paper_m.overall_score, 0.0)
        self.assertGreater(result.aggregate.avg_overall_score, 0.0)


class TestGenerateEvaluationReport(unittest.TestCase):
    """Test report generation."""

    def test_basic_report(self):
        runner = MockRunner(findings_count=2)
        papers = [
            TestPaper(
                paper_id="p1",
                title="Test",
                gold_findings=[
                    Finding(text="Issue", section="methods",
                            priority="high", category="methodology"),
                ],
            ),
        ]
        harness = EvaluationHarness(papers, runner)
        result = harness.run_batch(config_name="test")

        report = generate_evaluation_report(result)
        self.assertIn("Meta-Harness Evaluation Report", report)
        self.assertIn("Content Quality", report)
        self.assertIn("Process Quality", report)
        self.assertIn("Overall Score", report)
        self.assertIn("p1", report)


class TestLoadTestPapers(unittest.TestCase):
    """Test load_test_papers_from_gold."""

    def test_load_from_real_gold_dir(self):
        gold_dir = Path(__file__).resolve().parent.parent / "evaluation" / "gold_standard"
        if not gold_dir.exists():
            self.skipTest("Gold standard dir not available")

        papers = load_test_papers_from_gold(gold_dir)
        self.assertGreater(len(papers), 0)
        for p in papers:
            self.assertTrue(p.paper_id)
            self.assertGreater(len(p.gold_findings), 0)

    def test_load_from_empty_dir(self):
        tmpdir = Path(tempfile.mkdtemp())
        papers = load_test_papers_from_gold(tmpdir)
        self.assertEqual(len(papers), 0)


# ============================================================
# Test: Kill Switch behavior
# ============================================================


class TestKillSwitch(unittest.TestCase):
    """Test GODEL_META_HARNESS_ENABLED kill switch."""

    def test_kill_switch_exists(self):
        from core.godel_config import GODEL_META_HARNESS_ENABLED
        self.assertIsInstance(GODEL_META_HARNESS_ENABLED, bool)

    def test_kill_switch_default_on(self):
        """Default value (no env override) should be True."""
        from core.godel_config import _env_flag
        # Directly test the flag function with no env set
        with patch.dict(os.environ, {}, clear=False):
            # Remove any test-set override, then check
            env_copy = os.environ.copy()
            env_copy.pop("SCHOLAR_GODEL_META_HARNESS", None)
            with patch.dict(os.environ, env_copy, clear=True):
                result = _env_flag("SCHOLAR_GODEL_META_HARNESS")
                self.assertTrue(result)

    def test_kill_switch_can_be_disabled(self):
        """Setting env var to '0' should disable."""
        from core.godel_config import _env_flag
        with patch.dict(os.environ, {"SCHOLAR_GODEL_META_HARNESS": "0"}):
            result = _env_flag("SCHOLAR_GODEL_META_HARNESS")
            self.assertFalse(result)

    def test_kill_switch_patching_module_variable(self):
        """Verify module-level variable can be patched for tests."""
        with patch("core.godel_config.GODEL_META_HARNESS_ENABLED", False):
            from core import godel_config
            self.assertFalse(godel_config.GODEL_META_HARNESS_ENABLED)


# ============================================================
# Test: Bottleneck.to_dict
# ============================================================


class TestBottleneckSerialization(unittest.TestCase):
    """Test Bottleneck serialization."""

    def test_to_dict(self):
        b = Bottleneck(
            type=BottleneckType.TOOL_RELIABILITY,
            severity=Severity.HIGH,
            description="Tools are failing",
            evidence={"rate": 0.5},
            recommendation="Fix tools",
            affected_papers=["p1", "p2"],
        )
        d = b.to_dict()
        self.assertEqual(d["type"], "tool_reliability")
        self.assertEqual(d["severity"], "high")
        self.assertIn("Tools are failing", d["description"])
        self.assertEqual(d["evidence"]["rate"], 0.5)

    def test_batch_result_to_dict(self):
        result = BatchResult(
            config_name="test",
            timestamp="2026-05-28",
            papers_succeeded=3,
            papers_failed=1,
            total_run_time_seconds=10.5,
            bottlenecks=[
                Bottleneck(
                    type=BottleneckType.COVERAGE_GAP,
                    severity=Severity.MEDIUM,
                    description="Low coverage",
                )
            ],
        )
        d = result.to_dict()
        self.assertEqual(d["config_name"], "test")
        self.assertEqual(d["run_stats"]["papers_succeeded"], 3)
        self.assertEqual(len(d["bottlenecks"]), 1)


if __name__ == "__main__":
    unittest.main()
