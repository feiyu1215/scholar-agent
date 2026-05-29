"""Tests for C-4 Re-audit enhancement: severity tracking + revision quality."""

import pytest
from tools.reaudit import (
    IssueDiff, ReauditReport, severity_to_numeric,
    compute_revision_quality, generate_revision_report,
)


class TestSeverityMapping:
    def test_critical(self):
        assert severity_to_numeric("critical") == 4

    def test_major(self):
        assert severity_to_numeric("major") == 3

    def test_moderate(self):
        assert severity_to_numeric("moderate") == 2

    def test_minor(self):
        assert severity_to_numeric("minor") == 1

    def test_info(self):
        assert severity_to_numeric("info") == 0

    def test_unknown(self):
        # Implementation defaults unknown severities to moderate (2)
        assert severity_to_numeric("unknown") == 2

    def test_case_insensitive(self):
        assert severity_to_numeric("Critical") == 4
        assert severity_to_numeric("MAJOR") == 3


class TestRevisionQuality:
    def test_fully_addressed(self):
        quality = compute_revision_quality(
            {"severity": "major"}, None, "FULLY_ADDRESSED"
        )
        assert quality == 1.0

    def test_not_addressed(self):
        quality = compute_revision_quality(
            {"severity": "major"}, {"severity": "major"}, "NOT_ADDRESSED"
        )
        assert quality == 0.0

    def test_partially_addressed_severity_drop(self):
        quality = compute_revision_quality(
            {"severity": "critical"},
            {"severity": "minor"},
            "PARTIALLY_ADDRESSED"
        )
        # 0.3 base + severity bonus + possible evidence bonus
        assert quality > 0.3
        assert quality <= 1.0

    def test_partially_addressed_no_drop(self):
        quality = compute_revision_quality(
            {"severity": "major"},
            {"severity": "major"},
            "PARTIALLY_ADDRESSED"
        )
        # Base 0.3 only (no severity improvement)
        assert quality >= 0.3
        assert quality <= 0.5

    def test_new_issue(self):
        quality = compute_revision_quality(
            None, {"severity": "minor"}, "NEW"
        )
        assert quality == 0.0


class TestIssueDiffEnhanced:
    def test_new_fields_exist(self):
        diff = IssueDiff(
            issue_id="I01",
            title="Test issue",
            category="clarity",
            severity="major",
            root_cause_key="test::key",
            status="FULLY_ADDRESSED",
            evidence="Fixed",
            residual_note="",
        )
        # New fields should exist with defaults
        assert hasattr(diff, 'previous_severity')
        assert hasattr(diff, 'current_severity')
        assert hasattr(diff, 'severity_delta')
        assert hasattr(diff, 'revision_quality')
        assert diff.severity_delta == 0
        assert diff.revision_quality == 0.0

    def test_severity_delta_positive(self):
        """Positive delta = got worse."""
        diff = IssueDiff(
            issue_id="I01", title="T", category="c", severity="major",
            root_cause_key="k", status="NOT_ADDRESSED", evidence="",
            residual_note="",
            previous_severity="minor", current_severity="major",
            severity_delta=2,  # minor(1) → major(3) = +2
        )
        assert diff.severity_delta == 2

    def test_severity_delta_negative(self):
        """Negative delta = improved."""
        diff = IssueDiff(
            issue_id="I01", title="T", category="c", severity="minor",
            root_cause_key="k", status="PARTIALLY_ADDRESSED", evidence="",
            residual_note="",
            previous_severity="critical", current_severity="minor",
            severity_delta=-3,  # critical(4) → minor(1) = -3
        )
        assert diff.severity_delta == -3


class TestGenerateRevisionReport:
    def _make_report(self):
        diffs = [
            IssueDiff(
                issue_id="I01", title="Missing citation", category="literature",
                severity="major", root_cause_key="k1", status="FULLY_ADDRESSED",
                evidence="Citation added", residual_note="",
                previous_severity="major", current_severity="",
                severity_delta=-3, revision_quality=1.0,
            ),
            IssueDiff(
                issue_id="I02", title="Weak argument", category="logic",
                severity="moderate", root_cause_key="k2", status="PARTIALLY_ADDRESSED",
                evidence="Some improvement", residual_note="Still needs work",
                previous_severity="critical", current_severity="moderate",
                severity_delta=-2, revision_quality=0.6,
            ),
            IssueDiff(
                issue_id="I03", title="Typo", category="format",
                severity="minor", root_cause_key="k3", status="NOT_ADDRESSED",
                evidence="", residual_note="Still present",
                previous_severity="minor", current_severity="minor",
                severity_delta=0, revision_quality=0.0,
            ),
        ]
        return ReauditReport(
            total_previous_issues=3,
            fully_addressed=1,
            partially_addressed=1,
            not_addressed=1,
            new_issues=0,
            improvement_rate=0.5,
            diffs=diffs,
            summary="Mixed results",
        )

    def test_report_structure(self):
        report = self._make_report()
        result = generate_revision_report(report)
        assert "overview" in result
        assert "per_issue" in result
        assert "summary_text" in result

    def test_report_overview(self):
        report = self._make_report()
        result = generate_revision_report(report)
        overview = result["overview"]
        assert overview["total_issues"] == 3
        assert 0.0 <= overview["resolution_rate"] <= 1.0
        assert 0.0 <= overview["avg_quality_score"] <= 1.0

    def test_report_per_issue(self):
        report = self._make_report()
        result = generate_revision_report(report)
        per_issue = result["per_issue"]
        assert len(per_issue) == 3
        first = per_issue[0]
        assert "issue_id" in first
        assert "status" in first
        assert "severity_delta" in first
        assert "revision_quality" in first

    def test_report_summary_text(self):
        report = self._make_report()
        result = generate_revision_report(report)
        assert isinstance(result["summary_text"], str)
        assert len(result["summary_text"]) > 0
