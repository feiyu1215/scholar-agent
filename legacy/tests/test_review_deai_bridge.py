"""
Unit tests for tools/review_deai_bridge.py — Review→DeAI integration bridge.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.review_deai_bridge import (
    ReviewHint,
    extract_review_hints,
    format_hints_for_prompt,
    compute_dimension_bias,
    _is_expression_issue,
    _infer_dimension,
)


# ─── Test Data ────────────────────────────────────────────────────────────────

PRESENTATION_ISSUE = {
    "id": "ISS-003",
    "title": "Awkward phrasing in results paragraph",
    "quote": "The results demonstrate that our approach significantly outperforms...",
    "explanation": "This sentence uses promotional language typical of AI-generated text.",
    "comment_type": "presentation",
    "severity": "moderate",
    "confidence": "high",
    "source_section": "04_results",
    "suggestion": "Rewrite with more measured language: 'Our approach shows improvement over...'",
    "impact_dimensions": {"expression_clarity": 0.6, "completeness": 0.2},
}

METHODOLOGY_ISSUE = {
    "id": "ISS-001",
    "title": "Unclear experimental setup",
    "quote": "We ran the experiment...",
    "explanation": "The experimental setup lacks critical details.",
    "comment_type": "methodology",
    "severity": "major",
    "confidence": "high",
    "source_section": "03_methodology",
    "suggestion": "Specify the number of trials and randomization procedure.",
    "impact_dimensions": {"completeness": 0.7, "expression_clarity": 0.1},
}

STYLE_ISSUE_NO_COMMENT_TYPE = {
    "id": "ISS-005",
    "title": "Verbose and wordy transitions",
    "quote": "Furthermore, moreover, it is additionally worth noting...",
    "explanation": "The writing is verbose with redundant transitions.",
    "comment_type": "logic",  # Not "presentation" but has expression keywords
    "severity": "minor",
    "source_section": "02_introduction",
    "suggestion": "Simplify the wordy transitions.",
    "impact_dimensions": {"expression_clarity": 0.5},
}

EXPRESSION_CLARITY_HIGH = {
    "id": "ISS-007",
    "title": "Monotonous sentence structure",
    "quote": "This is important. This is significant. This is notable.",
    "explanation": "Repetitive sentence structure reduces readability",
    "comment_type": "logic",  # Not presentation
    "severity": "moderate",
    "source_section": "04_results",
    "suggestion": "Vary sentence openings",
    "impact_dimensions": {"expression_clarity": 0.45},
}

NO_QUOTE_ISSUE = {
    "id": "ISS-010",
    "title": "General clarity concern",
    "quote": "",
    "explanation": "Overall section needs polish",
    "comment_type": "presentation",
    "severity": "minor",
    "source_section": "05_discussion",
}


# ─── Tests: _is_expression_issue ──────────────────────────────────────────────

def test_is_expression_issue_by_comment_type():
    assert _is_expression_issue(PRESENTATION_ISSUE) is True


def test_is_not_expression_issue_methodology():
    assert _is_expression_issue(METHODOLOGY_ISSUE) is False


def test_is_expression_issue_by_impact_dimensions():
    assert _is_expression_issue(EXPRESSION_CLARITY_HIGH) is True


def test_is_expression_issue_by_keywords():
    assert _is_expression_issue(STYLE_ISSUE_NO_COMMENT_TYPE) is True


# ─── Tests: _infer_dimension ──────────────────────────────────────────────────

def test_infer_dimension_promotional():
    assert _infer_dimension(PRESENTATION_ISSUE) == "vocabulary"


def test_infer_dimension_transition():
    assert _infer_dimension(STYLE_ISSUE_NO_COMMENT_TYPE) == "connectors"


def test_infer_dimension_repetitive():
    assert _infer_dimension(EXPRESSION_CLARITY_HIGH) == "rhythm"


# ─── Tests: extract_review_hints ──────────────────────────────────────────────

def test_extract_only_expression_issues():
    issues = [PRESENTATION_ISSUE, METHODOLOGY_ISSUE, STYLE_ISSUE_NO_COMMENT_TYPE]
    hints = extract_review_hints(issues)
    # Should include PRESENTATION_ISSUE and STYLE_ISSUE_NO_COMMENT_TYPE, not METHODOLOGY
    assert len(hints) == 2
    hint_ids = [h.issue_id for h in hints]
    assert "ISS-003" in hint_ids
    assert "ISS-005" in hint_ids
    assert "ISS-001" not in hint_ids


def test_extract_with_section_filter():
    issues = [PRESENTATION_ISSUE, STYLE_ISSUE_NO_COMMENT_TYPE]
    # Only results section
    hints = extract_review_hints(issues, section_filter="04_results")
    assert len(hints) == 1
    assert hints[0].issue_id == "ISS-003"


def test_extract_skips_no_quote():
    issues = [NO_QUOTE_ISSUE]
    hints = extract_review_hints(issues)
    assert len(hints) == 0  # No quote → not useful as hint


def test_extract_includes_high_expression_clarity():
    issues = [EXPRESSION_CLARITY_HIGH]
    hints = extract_review_hints(issues)
    assert len(hints) == 1
    assert hints[0].suggested_dimension == "rhythm"


# ─── Tests: format_hints_for_prompt ───────────────────────────────────────────

def test_format_empty_hints():
    assert format_hints_for_prompt([]) == ""


def test_format_hints_contains_context_disclaimer():
    hints = [ReviewHint(
        quote="This is a test",
        concern="Awkward phrasing",
        source_section="04_results",
        suggested_dimension="vocabulary",
        severity="moderate",
        issue_id="ISS-003",
    )]
    result = format_hints_for_prompt(hints)
    assert "REVIEWER CONTEXT" in result
    assert "independent judgment" in result
    assert "VOCABULARY" in result
    assert "This is a test" in result


def test_format_hints_max_five():
    hints = [
        ReviewHint(
            quote=f"Quote {i}",
            concern=f"Concern {i}",
            source_section="04_results",
            suggested_dimension="vocabulary",
            severity="minor",
            issue_id=f"ISS-{i:03d}",
        )
        for i in range(8)
    ]
    result = format_hints_for_prompt(hints)
    assert "Quote 0" in result
    assert "Quote 4" in result
    assert "Quote 5" not in result  # 6th hint should be truncated
    assert "3 more expression concerns" in result


# ─── Tests: compute_dimension_bias ────────────────────────────────────────────

def test_bias_empty():
    assert compute_dimension_bias([]) == {}


def test_bias_single_dimension():
    hints = [
        ReviewHint(quote="x", concern="y", source_section="s",
                   suggested_dimension="rhythm", severity="minor"),
        ReviewHint(quote="x", concern="y", source_section="s",
                   suggested_dimension="rhythm", severity="minor"),
    ]
    biases = compute_dimension_bias(hints)
    assert "rhythm" in biases
    assert 0 < biases["rhythm"] <= 0.05  # Max bias cap


def test_bias_multi_dimension():
    hints = [
        ReviewHint(quote="x", concern="y", source_section="s",
                   suggested_dimension="rhythm", severity="minor"),
        ReviewHint(quote="x", concern="y", source_section="s",
                   suggested_dimension="vocabulary", severity="minor"),
        ReviewHint(quote="x", concern="y", source_section="s",
                   suggested_dimension="vocabulary", severity="minor"),
    ]
    biases = compute_dimension_bias(hints)
    assert biases["vocabulary"] > biases["rhythm"]  # More vocab hints = higher bias


# ─── Tests: ReviewHint dataclass ──────────────────────────────────────────────

def test_review_hint_to_dict():
    hint = ReviewHint(
        quote="Test quote",
        concern="Test concern",
        source_section="04_results",
        suggested_dimension="vocabulary",
        severity="moderate",
        issue_id="ISS-003",
    )
    d = hint.to_dict()
    assert d["quote"] == "Test quote"
    assert d["suggested_dimension"] == "vocabulary"
    assert d["issue_id"] == "ISS-003"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
