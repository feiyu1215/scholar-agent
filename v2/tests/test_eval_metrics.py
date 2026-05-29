"""
tests/test_eval_metrics.py — Unit tests for evaluation/metrics.py.

Verifies:
1. Text similarity computation (Jaccard + section bonus)
2. Greedy matching algorithm correctness
3. Precision/recall/F1 calculations
4. Weighted recall (priority-based)
5. Category breakdown
6. Edge cases (empty inputs, perfect match, no match)
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.metrics import (
    Finding,
    MatchResult,
    EvalMetrics,
    AggregateMetrics,
    compute_similarity,
    match_findings,
    compute_metrics,
    compute_aggregate,
    MATCH_THRESHOLD,
    SECTION_BONUS,
    HIGH_PRIORITY_WEIGHT,
    _tokenize,
)


class TestTokenize(unittest.TestCase):
    """Test the tokenization helper."""

    def test_basic(self):
        tokens = _tokenize("Hello World, this is a test!")
        self.assertEqual(tokens, {"hello", "world", "this", "is", "a", "test"})

    def test_empty(self):
        self.assertEqual(_tokenize(""), set())

    def test_numbers(self):
        tokens = _tokenize("Table 3 shows p < 0.05")
        self.assertIn("table", tokens)
        self.assertIn("3", tokens)
        self.assertIn("0.05", tokens)


class TestComputeSimilarity(unittest.TestCase):
    """Test similarity computation."""

    def test_identical_texts(self):
        sim = compute_similarity("hello world", "hello world")
        self.assertAlmostEqual(sim, 1.0)

    def test_no_overlap(self):
        sim = compute_similarity("hello world", "foo bar baz")
        self.assertAlmostEqual(sim, 0.0)

    def test_partial_overlap(self):
        sim = compute_similarity(
            "The method lacks statistical significance tests",
            "Statistical significance testing is missing from the method",
        )
        # Should have decent overlap: "the", "method", "statistical", "significance"
        self.assertGreater(sim, 0.3)
        self.assertLess(sim, 1.0)

    def test_section_bonus(self):
        # Use partial overlap so Jaccard < 1.0, making bonus visible
        text_a = "pruning threshold may be too aggressive"
        text_b = "the pruning threshold needs more justification for aggressive choice"
        sim_no_section = compute_similarity(text_a, text_b)
        sim_with_section = compute_similarity(
            text_a, text_b,
            section_a="methodology", section_b="methodology",
        )
        self.assertAlmostEqual(sim_with_section - sim_no_section, SECTION_BONUS)

    def test_section_bonus_mismatch(self):
        sim = compute_similarity(
            "pruning threshold", "pruning threshold",
            section_a="methodology", section_b="results",
        )
        # No bonus for mismatched sections
        self.assertAlmostEqual(sim, 1.0)

    def test_similarity_capped_at_1(self):
        # With section bonus, total should not exceed 1.0
        sim = compute_similarity(
            "identical text here", "identical text here",
            section_a="intro", section_b="intro",
        )
        self.assertLessEqual(sim, 1.0)

    def test_empty_input(self):
        self.assertEqual(compute_similarity("", "hello"), 0.0)
        self.assertEqual(compute_similarity("hello", ""), 0.0)
        self.assertEqual(compute_similarity("", ""), 0.0)


class TestMatchFindings(unittest.TestCase):
    """Test the greedy matching algorithm."""

    def test_perfect_match(self):
        predicted = [
            Finding("The method lacks significance tests", section="results"),
            Finding("Baseline is outdated from 2019", section="results"),
        ]
        gold = [
            Finding("The method lacks significance tests", section="results"),
            Finding("Baseline is outdated from 2019", section="results"),
        ]
        matches, unmatched_p, unmatched_g = match_findings(predicted, gold)
        self.assertEqual(len(matches), 2)
        self.assertEqual(unmatched_p, [])
        self.assertEqual(unmatched_g, [])

    def test_no_match(self):
        predicted = [Finding("Writing style is poor")]
        gold = [Finding("Statistical tests are missing")]
        matches, unmatched_p, unmatched_g = match_findings(predicted, gold)
        self.assertEqual(len(matches), 0)
        self.assertEqual(unmatched_p, [0])
        self.assertEqual(unmatched_g, [0])

    def test_partial_match(self):
        predicted = [
            Finding("The statistical tests are missing from results"),
            Finding("Writing could be improved"),  # false positive
        ]
        gold = [
            Finding("Statistical significance tests are absent from the results section"),
            Finding("Baseline comparison uses only one model"),  # missed
        ]
        matches, unmatched_p, unmatched_g = match_findings(predicted, gold)
        # First predicted should match first gold (high token overlap)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].predicted_idx, 0)
        self.assertEqual(matches[0].gold_idx, 0)
        self.assertEqual(unmatched_p, [1])
        self.assertEqual(unmatched_g, [1])

    def test_empty_predicted(self):
        gold = [Finding("Some finding")]
        matches, unmatched_p, unmatched_g = match_findings([], gold)
        self.assertEqual(len(matches), 0)
        self.assertEqual(unmatched_p, [])
        self.assertEqual(unmatched_g, [0])

    def test_empty_gold(self):
        predicted = [Finding("Some finding")]
        matches, unmatched_p, unmatched_g = match_findings(predicted, [])
        self.assertEqual(len(matches), 0)
        self.assertEqual(unmatched_p, [0])
        self.assertEqual(unmatched_g, [])

    def test_one_to_one_constraint(self):
        """Each gold finding can only be matched once."""
        predicted = [
            Finding("pruning threshold lacks justification"),
            Finding("the pruning threshold of 0.1 needs justification"),
        ]
        gold = [
            Finding("pruning threshold 0.1 used without justification"),
        ]
        matches, unmatched_p, unmatched_g = match_findings(predicted, gold)
        # Only one match allowed
        self.assertEqual(len(matches), 1)
        self.assertEqual(len(unmatched_p), 1)
        self.assertEqual(unmatched_g, [])


class TestComputeMetrics(unittest.TestCase):
    """Test metrics computation."""

    def test_perfect_score(self):
        findings = [
            Finding("exact text A", section="intro", priority="high", category="methodology"),
            Finding("exact text B", section="results", priority="medium", category="data"),
        ]
        metrics = compute_metrics("test", findings, findings)
        self.assertAlmostEqual(metrics.precision, 1.0)
        self.assertAlmostEqual(metrics.recall, 1.0)
        self.assertAlmostEqual(metrics.f1, 1.0)

    def test_zero_recall(self):
        predicted = [Finding("completely unrelated finding about writing")]
        gold = [Finding("statistical significance is missing from experiments")]
        metrics = compute_metrics("test", predicted, gold)
        self.assertEqual(metrics.num_matched, 0)
        self.assertAlmostEqual(metrics.precision, 0.0)
        self.assertAlmostEqual(metrics.recall, 0.0)
        self.assertAlmostEqual(metrics.f1, 0.0)

    def test_no_predictions(self):
        gold = [Finding("something important")]
        metrics = compute_metrics("test", [], gold)
        self.assertAlmostEqual(metrics.precision, 0.0)
        self.assertAlmostEqual(metrics.recall, 0.0)
        self.assertAlmostEqual(metrics.f1, 0.0)

    def test_weighted_recall_prioritizes_high(self):
        """High-priority findings missed hurt weighted recall more."""
        gold = [
            Finding("critical issue", priority="critical"),
            Finding("minor style issue", priority="low"),
        ]
        # Only match the low-priority one
        predicted = [Finding("minor style issue", priority="low")]
        metrics = compute_metrics("test", predicted, gold)

        # Weighted recall: matched_weight=1.0 (low), total_weight=2.0+1.0=3.0
        # So weighted_recall = 1.0/3.0 ≈ 0.333
        self.assertAlmostEqual(metrics.weighted_recall, 1.0 / 3.0, places=3)

        # Compare: if we match the high one instead
        predicted2 = [Finding("critical issue", priority="critical")]
        metrics2 = compute_metrics("test2", predicted2, gold)
        # weighted_recall = 2.0/3.0 ≈ 0.667
        self.assertAlmostEqual(metrics2.weighted_recall, 2.0 / 3.0, places=3)

        # High-priority match should give better weighted recall
        self.assertGreater(metrics2.weighted_recall, metrics.weighted_recall)

    def test_category_breakdown(self):
        predicted = [
            Finding("method issue", category="methodology"),
            Finding("data issue", category="data"),
        ]
        gold = [
            Finding("method issue here", category="methodology"),
            Finding("different data problem", category="data"),
            Finding("logic flaw", category="logic"),
        ]
        metrics = compute_metrics("test", predicted, gold)
        self.assertIn("methodology", metrics.category_breakdown)
        self.assertIn("data", metrics.category_breakdown)
        self.assertIn("logic", metrics.category_breakdown)
        # Logic has 0 predicted, so precision should be 0
        self.assertEqual(metrics.category_breakdown["logic"]["num_predicted"], 0)


class TestComputeAggregate(unittest.TestCase):
    """Test aggregate metrics computation."""

    def test_single_paper(self):
        findings = [Finding("test finding")]
        metrics = compute_metrics("p1", findings, findings)
        agg = compute_aggregate([metrics])
        self.assertEqual(agg.num_papers, 1)
        self.assertAlmostEqual(agg.avg_f1, 1.0)

    def test_empty(self):
        agg = compute_aggregate([])
        self.assertEqual(agg.num_papers, 0)
        self.assertAlmostEqual(agg.avg_f1, 0.0)

    def test_macro_averaging(self):
        """Aggregate uses macro-averaging (average of per-paper metrics)."""
        # Paper 1: perfect
        m1 = compute_metrics("p1", [Finding("A")], [Finding("A")])
        # Paper 2: zero (no match)
        m2 = compute_metrics("p2", [Finding("X")], [Finding("completely different Y")])

        agg = compute_aggregate([m1, m2])
        # Macro average: (1.0 + 0.0) / 2 = 0.5
        self.assertAlmostEqual(agg.avg_precision, 0.5)
        self.assertAlmostEqual(agg.avg_recall, 0.5)


if __name__ == "__main__":
    unittest.main()
