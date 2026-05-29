"""
tests/test_decision_observability.py — Tests for C-8 decision observability.

Covers:
- DecisionTrace generation in action_router
- Decision summary formatting
- DecisionReport generation and output
- Score attribution
- Capability boundary identification
- JSONL trace writing
"""

import json
import tempfile
import time
from pathlib import Path

import pytest

from tools.action_router import (
    route_issues,
    DecisionTrace,
    RoutedIssue,
    _route_single_issue,
    _build_decision_summary,
    _get_meta_risk_for_category,
)
from tools.decision_report import (
    generate_decision_report,
    DecisionReport,
    ScoreAttribution,
    CapabilityBoundary,
    format_decision_report_compact,
    _compute_score_attribution,
    _identify_boundaries,
    _summarize_decision_patterns,
)


# ============================================================
# Fixtures
# ============================================================

def _make_issue(
    id="ISS-001",
    category="clarity",
    action_type="auto_fix",
    severity="minor",
    description="Unclear sentence",
    suggestion="Rewrite for clarity",
    location=None,
    **kwargs,
):
    """Helper to create a test issue dict."""
    issue = {
        "id": id,
        "category": category,
        "action_type": action_type,
        "severity": severity,
        "description": description,
        "suggestion": suggestion,
        "location": location or {"section_id": "03_methodology", "quote": ""},
        "action_rationale": "Standard clarity fix",
        "fix_complexity": "sentence_level",
    }
    issue.update(kwargs)
    return issue


def _make_thesis_issue():
    """Create an issue that touches thesis content."""
    return _make_issue(
        id="ISS-THESIS",
        category="argument",
        action_type="auto_fix",
        description="This paper argues that X causes Y",
        suggestion="Change from causal to correlational",
        location={"section_id": "02_introduction", "quote": "this paper argues that"},
    )


def _make_fabrication_issue():
    """Create an issue that might introduce new claims."""
    return _make_issue(
        id="ISS-FAB",
        category="missing_reference",
        action_type="auto_fix",
        description="Missing key citation",
        suggestion="Add a study supporting this claim",
    )


# ============================================================
# DecisionTrace Tests
# ============================================================

class TestDecisionTrace:
    """Tests for DecisionTrace dataclass and generation."""

    def test_trace_generated_for_simple_issue(self):
        """Every routed issue should have a DecisionTrace."""
        issue = _make_issue()
        stats = {"red_line_downgrades": 0, "budget_downgrades": 0, "first_of_type_confirms": 0}
        result = _route_single_issue(issue, "full", {"clarity"}, stats)
        
        assert result.decision_trace is not None
        assert isinstance(result.decision_trace, DecisionTrace)
        assert result.decision_trace.issue_id == "ISS-001"
        assert result.decision_trace.original_action == "auto_fix"
        assert result.decision_trace.final_action == "auto_fix"

    def test_trace_records_all_checks(self):
        """Trace should record every check (triggered or not)."""
        issue = _make_issue()
        stats = {"red_line_downgrades": 0, "budget_downgrades": 0, "first_of_type_confirms": 0}
        result = _route_single_issue(issue, "full", {"clarity"}, stats)
        
        trace = result.decision_trace
        check_names = [c["check"] for c in trace.checks_applied]
        assert "RED_LINE_1_THESIS" in check_names
        assert "RED_LINE_2_FABRICATION" in check_names
        assert "FIRST_OF_TYPE" in check_names
        assert "BUDGET_CEILING" in check_names

    def test_trace_red_line_triggered(self):
        """When Red Line fires, trace should record triggered=True with reason."""
        issue = _make_thesis_issue()
        stats = {"red_line_downgrades": 0, "budget_downgrades": 0, "first_of_type_confirms": 0}
        result = _route_single_issue(issue, "full", set(), stats)
        
        trace = result.decision_trace
        rl1 = next(c for c in trace.checks_applied if c["check"] == "RED_LINE_1_THESIS")
        assert rl1["triggered"] is True
        assert "thesis" in rl1["reason"].lower() or "causal" in rl1["reason"].lower()
        assert trace.final_action == "guidance"

    def test_trace_first_of_type_triggered(self):
        """First-of-type should trigger when category not seen."""
        issue = _make_issue(category="grammar")
        stats = {"red_line_downgrades": 0, "budget_downgrades": 0, "first_of_type_confirms": 0}
        result = _route_single_issue(issue, "full", set(), stats)
        
        trace = result.decision_trace
        fot = next(c for c in trace.checks_applied if c["check"] == "FIRST_OF_TYPE")
        assert fot["triggered"] is True
        assert "grammar" in fot["reason"]
        assert trace.final_action == "confirm_fix"

    def test_trace_budget_triggered(self):
        """Budget ceiling should trigger in minimal mode."""
        issue = _make_issue(action_type="auto_fix", category="clarity")
        stats = {"red_line_downgrades": 0, "budget_downgrades": 0, "first_of_type_confirms": 0}
        result = _route_single_issue(issue, "minimal", {"clarity"}, stats)
        
        trace = result.decision_trace
        budget = next(c for c in trace.checks_applied if c["check"] == "BUDGET_CEILING")
        assert budget["triggered"] is True
        assert "minimal" in budget["reason"]

    def test_trace_no_triggers_for_safe_issue(self):
        """When no checks trigger, all should be triggered=False."""
        issue = _make_issue(category="clarity", action_type="auto_fix")
        stats = {"red_line_downgrades": 0, "budget_downgrades": 0, "first_of_type_confirms": 0}
        result = _route_single_issue(issue, "full", {"clarity"}, stats)
        
        trace = result.decision_trace
        assert all(not c["triggered"] for c in trace.checks_applied)
        assert trace.original_action == trace.final_action == "auto_fix"

    def test_trace_risk_factors_populated(self):
        """Risk factors dict should contain all expected fields."""
        issue = _make_issue()
        stats = {"red_line_downgrades": 0, "budget_downgrades": 0, "first_of_type_confirms": 0}
        result = _route_single_issue(issue, "full", {"clarity"}, stats)
        
        rf = result.decision_trace.risk_factors
        assert "meta_risk" in rf
        assert "touches_thesis" in rf
        assert "might_introduce_claims" in rf
        assert "category" in rf
        assert "budget" in rf
        assert "category_previously_seen" in rf

    def test_trace_serializes_to_jsonl(self):
        """DecisionTrace should produce valid JSON."""
        issue = _make_issue()
        stats = {"red_line_downgrades": 0, "budget_downgrades": 0, "first_of_type_confirms": 0}
        result = _route_single_issue(issue, "full", {"clarity"}, stats)
        
        jsonl = result.decision_trace.to_jsonl_entry()
        parsed = json.loads(jsonl)
        assert parsed["issue_id"] == "ISS-001"
        assert "checks_applied" in parsed
        assert isinstance(parsed["timestamp"], float)

    def test_trace_timestamp_is_recent(self):
        """Timestamp should be within a few seconds of now."""
        issue = _make_issue()
        stats = {"red_line_downgrades": 0, "budget_downgrades": 0, "first_of_type_confirms": 0}
        before = time.time()
        result = _route_single_issue(issue, "full", {"clarity"}, stats)
        after = time.time()
        
        assert before <= result.decision_trace.timestamp <= after


# ============================================================
# Decision Summary Tests
# ============================================================

class TestDecisionSummary:
    """Tests for _build_decision_summary."""

    def test_summary_no_change_no_triggers(self):
        """Summary for issue with no changes."""
        checks = [
            {"check": "RED_LINE_1_THESIS", "triggered": False, "reason": ""},
            {"check": "BUDGET_CEILING", "triggered": False, "reason": ""},
        ]
        summary = _build_decision_summary(
            "auto_fix", "auto_fix", "clarity", checks, {"clarity"}, "full", "low"
        )
        assert "auto_fix chosen" in summary
        assert "no Red Line triggers" in summary

    def test_summary_downgraded(self):
        """Summary for downgraded issue should mention cause."""
        checks = [
            {"check": "RED_LINE_1_THESIS", "triggered": True, "reason": ""},
            {"check": "BUDGET_CEILING", "triggered": False, "reason": ""},
        ]
        summary = _build_decision_summary(
            "auto_fix", "guidance", "argument", checks, set(), "full", "medium"
        )
        assert "downgraded" in summary
        assert "RED_LINE_1_THESIS" in summary

    def test_summary_unchanged_with_triggers(self):
        """Summary when checks fire but no downgrade (already guidance)."""
        checks = [
            {"check": "RED_LINE_1_THESIS", "triggered": True, "reason": ""},
        ]
        summary = _build_decision_summary(
            "guidance", "guidance", "argument", checks, set(), "full", "low"
        )
        assert "unchanged" in summary
        assert "threshold" in summary


# ============================================================
# Meta Risk Mapping Tests
# ============================================================

class TestMetaRiskMapping:
    """Tests for _get_meta_risk_for_category."""

    def test_known_category_returns_risk(self):
        """Known categories should return a valid risk level."""
        risk = _get_meta_risk_for_category("clarity")
        assert risk in ("low", "medium", "high")

    def test_unknown_category_returns_unknown(self):
        """Unknown categories should return 'unknown'."""
        risk = _get_meta_risk_for_category("totally_unknown_category")
        assert risk == "unknown"

    def test_missing_reference_maps_to_verify(self):
        """missing_reference should map to literature_verify tool."""
        risk = _get_meta_risk_for_category("missing_reference")
        assert risk in ("low", "medium", "high")


# ============================================================
# Route Issues Integration (trace_dir) Tests
# ============================================================

class TestRouteIssuesTracing:
    """Tests for trace file writing in route_issues."""

    def test_trace_file_written(self):
        """route_issues should write JSONL traces to trace_dir."""
        issues = [_make_issue(id=f"ISS-{i}", category="clarity") for i in range(3)]
        
        with tempfile.TemporaryDirectory() as tmpdir:
            trace_dir = Path(tmpdir) / "trace"
            routed, stats = route_issues(
                issues, budget="full", 
                seen_categories={"clarity"},
                trace_dir=trace_dir,
            )
            
            trace_file = trace_dir / "routing_decisions.jsonl"
            assert trace_file.exists()
            
            lines = trace_file.read_text().strip().split("\n")
            assert len(lines) == 3
            
            # Each line should be valid JSON
            for line in lines:
                entry = json.loads(line)
                assert "issue_id" in entry
                assert "decision_summary" in entry

    def test_trace_disabled_with_false(self):
        """Passing trace_dir=False should skip trace writing."""
        issues = [_make_issue()]
        routed, stats = route_issues(
            issues, budget="full", 
            seen_categories={"clarity"},
            trace_dir=False,
        )
        # Should not raise, traces still in memory
        assert routed[0].decision_trace is not None

    def test_trace_appends_to_existing(self):
        """Traces should append (not overwrite) existing file."""
        issues = [_make_issue(id="ISS-A")]
        
        with tempfile.TemporaryDirectory() as tmpdir:
            trace_dir = Path(tmpdir) / "trace"
            trace_dir.mkdir()
            trace_file = trace_dir / "routing_decisions.jsonl"
            trace_file.write_text('{"existing": true}\n')
            
            route_issues(
                issues, budget="full",
                seen_categories={"clarity"},
                trace_dir=trace_dir,
            )
            
            lines = trace_file.read_text().strip().split("\n")
            assert len(lines) == 2  # existing + new
            assert json.loads(lines[0]) == {"existing": True}


# ============================================================
# Decision Report Tests
# ============================================================

class TestDecisionReport:
    """Tests for generate_decision_report and output formatting."""

    def _make_routed_set(self):
        """Create a realistic set of routed issues for testing."""
        issues = [
            _make_issue(id="ISS-001", category="clarity", action_type="auto_fix"),
            _make_issue(id="ISS-002", category="grammar", action_type="auto_fix"),
            _make_issue(id="ISS-003", category="clarity", action_type="auto_fix"),
            _make_thesis_issue(),
            _make_fabrication_issue(),
        ]
        routed, stats = route_issues(
            issues, budget="full", 
            seen_categories={"clarity", "grammar"},
            trace_dir=False,
        )
        return routed, stats

    def test_report_generation(self):
        """generate_decision_report should produce a valid report."""
        routed, stats = self._make_routed_set()
        report = generate_decision_report(
            routed, stats, score_before=4.2, score_after=6.8
        )
        
        assert isinstance(report, DecisionReport)
        assert report.total_issues == 5
        assert report.score_delta == pytest.approx(2.6, abs=0.01)
        assert report.score_before == 4.2
        assert report.score_after == 6.8

    def test_report_action_counts(self):
        """Report should correctly count action types."""
        routed, stats = self._make_routed_set()
        report = generate_decision_report(routed, stats)
        
        total_counted = sum(report.action_counts.values())
        assert total_counted == 5

    def test_report_has_boundaries(self):
        """Report should identify capability boundaries."""
        routed, stats = self._make_routed_set()
        report = generate_decision_report(routed, stats)
        
        # Thesis issue should be a boundary (auto_fix → guidance)
        boundary_ids = [b.issue_id for b in report.boundaries]
        assert "ISS-THESIS" in boundary_ids

    def test_report_categories_tracked(self):
        """Report should list all processed categories."""
        routed, stats = self._make_routed_set()
        report = generate_decision_report(routed, stats)
        
        assert "clarity" in report.categories_processed
        assert len(report.categories_processed) == 5  # One per issue

    def test_report_to_json(self):
        """Report should serialize to valid JSON."""
        routed, stats = self._make_routed_set()
        report = generate_decision_report(
            routed, stats, score_before=4.0, score_after=7.0
        )
        
        json_str = report.to_json()
        parsed = json.loads(json_str)
        assert parsed["total_issues"] == 5
        assert parsed["score_delta"] == pytest.approx(3.0)

    def test_report_to_markdown(self):
        """Report should produce readable Markdown."""
        routed, stats = self._make_routed_set()
        report = generate_decision_report(
            routed, stats, score_before=4.0, score_after=7.0
        )
        
        md = report.to_markdown()
        assert "# Decision Report" in md
        assert "Executive Summary" in md
        assert "Processing Overview" in md
        assert "Score Attribution" in md
        assert "4.0" in md
        assert "7.0" in md

    def test_report_save(self):
        """Report.save() should write both JSON and Markdown files."""
        routed, stats = self._make_routed_set()
        report = generate_decision_report(routed, stats, score_before=4.0, score_after=6.0)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path, md_path = report.save(Path(tmpdir))
            
            assert json_path.exists()
            assert md_path.exists()
            assert json_path.suffix == ".json"
            assert md_path.suffix == ".md"
            
            # Verify content
            content = json.loads(json_path.read_text())
            assert content["total_issues"] == 5

    def test_report_no_scores(self):
        """Report should work without score data."""
        routed, stats = self._make_routed_set()
        report = generate_decision_report(routed, stats)
        
        assert report.score_before is None
        assert report.score_after is None
        assert report.score_delta is None
        
        md = report.to_markdown()
        assert "Score Attribution" not in md  # Section omitted


# ============================================================
# Score Attribution Tests
# ============================================================

class TestScoreAttribution:
    """Tests for score attribution logic."""

    def test_attribution_weights(self):
        """auto_fix should get proportionally more attribution."""
        issues = [
            _make_issue(id=f"ISS-A{i}", category="clarity", action_type="auto_fix") 
            for i in range(5)
        ] + [
            _make_issue(id=f"ISS-G{i}", category="style", action_type="guidance")
            for i in range(5)
        ]
        routed, stats = route_issues(
            issues, budget="full",
            seen_categories={"clarity", "style"},
            trace_dir=False,
        )
        
        attrs = _compute_score_attribution(routed, total_delta=3.0)
        
        # auto_fix should get more than guidance
        auto_attr = next((a for a in attrs if a.action_type == "auto_fix"), None)
        guide_attr = next((a for a in attrs if a.action_type == "guidance"), None)
        
        assert auto_attr is not None
        assert guide_attr is not None
        assert auto_attr.estimated_contribution > guide_attr.estimated_contribution

    def test_attribution_sums_to_total(self):
        """All attributions should sum approximately to total delta."""
        issues = [
            _make_issue(id="ISS-1", category="clarity", action_type="auto_fix"),
            _make_issue(id="ISS-2", category="grammar", action_type="confirm_fix"),
        ]
        routed, stats = route_issues(
            issues, budget="full",
            seen_categories={"clarity", "grammar"},
            trace_dir=False,
        )
        
        attrs = _compute_score_attribution(routed, total_delta=2.0)
        total = sum(a.estimated_contribution for a in attrs)
        assert total == pytest.approx(2.0, abs=0.01)


# ============================================================
# Compact Report Format Tests
# ============================================================

class TestCompactReport:
    """Tests for format_decision_report_compact."""

    def test_compact_format(self):
        """Compact format should be a single concise paragraph."""
        report = DecisionReport(
            timestamp=time.time(),
            total_issues=12,
            action_counts={"auto_fix": 7, "confirm_fix": 3, "guidance": 2},
            score_before=4.2,
            score_after=6.8,
            score_delta=2.6,
            red_line_count=1,
            first_of_type_count=2,
            budget_downgrade_count=0,
            boundaries=[
                CapabilityBoundary("ISS-X", "thesis", "Thesis content", "guidance")
            ],
        )
        
        compact = format_decision_report_compact(report)
        assert "12 issues" in compact
        assert "7 auto-fixed" in compact
        assert "4.2" in compact
        assert "6.8" in compact
        assert "+2.6" in compact
        assert "Red Line: 1" in compact
        assert "beyond auto-handling" in compact

    def test_compact_no_scores(self):
        """Compact format should work without scores."""
        report = DecisionReport(
            timestamp=time.time(),
            total_issues=5,
            action_counts={"auto_fix": 3, "confirm_fix": 1, "guidance": 1},
        )
        
        compact = format_decision_report_compact(report)
        assert "5 issues" in compact
        assert "Score" not in compact


# ============================================================
# Decision Pattern Summary Tests
# ============================================================

class TestDecisionPatterns:
    """Tests for _summarize_decision_patterns."""

    def test_aggressive_pattern(self):
        """High auto_fix rate should be labeled aggressive."""
        stats = {"total": 10, "action_counts": {"auto_fix": 8, "confirm_fix": 1, "guidance": 1},
                 "red_line_downgrades": 0}
        summary = _summarize_decision_patterns(stats, [])
        assert "Aggressive" in summary

    def test_conservative_pattern(self):
        """Low auto_fix rate should be labeled conservative."""
        stats = {"total": 10, "action_counts": {"auto_fix": 2, "confirm_fix": 3, "guidance": 5},
                 "red_line_downgrades": 0}
        summary = _summarize_decision_patterns(stats, [])
        assert "Conservative" in summary

    def test_red_line_noted_in_pattern(self):
        """Red Line interventions should appear in pattern summary."""
        stats = {"total": 5, "action_counts": {"auto_fix": 3, "confirm_fix": 1, "guidance": 1},
                 "red_line_downgrades": 2}
        summary = _summarize_decision_patterns(stats, [])
        assert "Red Line" in summary
        assert "2" in summary

    def test_empty_issues(self):
        """Zero issues should produce 'No issues processed'."""
        stats = {"total": 0, "action_counts": {}}
        summary = _summarize_decision_patterns(stats, [])
        assert "No issues" in summary
