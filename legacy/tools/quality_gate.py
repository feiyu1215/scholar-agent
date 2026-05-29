"""
tools/quality_gate.py - Review quality meta-assessment (Quality Gate).

Evaluates the quality of a review itself - not the paper, but whether the
reviewer did a thorough job. Inspired by Open Design's Critique Theater pattern.

Architecture:
    - Rule-based layer (zero LLM cost): specificity, coverage, actionability, evidence
    - Optional LLM layer: calibration check (severity appropriateness)
    - Dual gate: Composite Score >= threshold AND must_fix issues resolved

Integration:
    - Called after review_paper consolidation
    - Drives iterative review: if gate fails, triggers deep_review on weak dimensions
    - Feeds into score_tracker for quality trajectory monitoring

Design decisions:
    - 5 dimensions with configurable weights and thresholds
    - Gate verdict: "ship" (pass), "deepen" (iterate), "restart" (review too shallow)
    - must_fix_check identifies blocking issues regardless of composite score
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set

from config import load_thresholds

_CFG = load_thresholds().get("quality_gate", {})


# ============================================================
# Data Classes
# ============================================================

@dataclass
class GateVerdict:
    """Result of quality gate evaluation."""
    passed: bool
    composite_score: float  # 0.0 - 1.0
    must_fix_count: int
    dimension_scores: Dict[str, float]  # dimension_name -> 0.0-1.0
    weak_dimensions: List[str]  # dimensions below their threshold
    recommendation: str  # "ship" | "deepen" | "restart"
    details: Dict[str, str] = field(default_factory=dict)


@dataclass
class ReviewIssueProxy:
    """Minimal interface for ReviewIssue (avoids circular import)."""
    title: str = ""
    quote: str = ""
    explanation: str = ""
    comment_type: str = ""
    severity: str = ""
    confidence: str = ""
    source_section: str = ""
    gate_blocker: bool = False
    suggestion: str = ""
    quote_verified: bool = False


# ============================================================
# Gate Dimension Configuration
# ============================================================

_DIM_CFG = _CFG.get("dimensions", {})

GATE_DIMENSIONS = {
    "specificity": {
        "weight": _DIM_CFG.get("specificity", {}).get("weight", 0.25),
        "threshold": _DIM_CFG.get("specificity", {}).get("threshold", 0.6),
        "description": "Each issue has specific quote + location + actionable suggestion",
    },
    "coverage": {
        "weight": _DIM_CFG.get("coverage", {}).get("weight", 0.20),
        "threshold": _DIM_CFG.get("coverage", {}).get("threshold", 0.5),
        "description": "Issues cover the paper's core sections (not just intro/conclusion)",
    },
    "actionability": {
        "weight": _DIM_CFG.get("actionability", {}).get("weight", 0.20),
        "threshold": _DIM_CFG.get("actionability", {}).get("threshold", 0.6),
        "description": "Suggestions are concrete and executable (not generic advice)",
    },
    "calibration": {
        "weight": _DIM_CFG.get("calibration", {}).get("weight", 0.15),
        "threshold": _DIM_CFG.get("calibration", {}).get("threshold", 0.5),
        "description": "Severity ratings are reasonable (not all major, not all minor)",
    },
    "evidence": {
        "weight": _DIM_CFG.get("evidence", {}).get("weight", 0.20),
        "threshold": _DIM_CFG.get("evidence", {}).get("threshold", 0.6),
        "description": "Each issue is supported by textual evidence from the paper",
    },
}

GATE_PASS_THRESHOLD = _CFG.get("gate_pass_threshold", 0.65)

CORE_SECTION_PREFIXES = {
    "introduction", "intro", "method", "methodology", "approach",
    "experiment", "results", "discussion", "evaluation", "analysis",
    "related work", "background", "model", "framework",
}


# ============================================================
# Dimension Scoring Functions (Rule-based, Zero LLM Cost)
# ============================================================

def _score_specificity(issues: List[ReviewIssueProxy]) -> float:
    """Specificity: proportion of issues that have a non-empty quote."""
    if not issues:
        return 0.0
    has_quote = sum(1 for i in issues if i.quote and len(i.quote.strip()) > 10)
    return has_quote / len(issues)


def _score_coverage(issues: List[ReviewIssueProxy], paper_sections: List[str]) -> float:
    """Coverage: proportion of core paper sections that have at least one issue."""
    if not paper_sections:
        return 0.5

    core_sections = set()
    for section in paper_sections:
        section_lower = section.lower().strip()
        for prefix in CORE_SECTION_PREFIXES:
            if prefix in section_lower:
                core_sections.add(section)
                break

    if not core_sections:
        core_sections = set(paper_sections)

    covered_sections: Set[str] = set()
    for issue in issues:
        src = issue.source_section.lower().strip() if issue.source_section else ""
        for cs in core_sections:
            if cs.lower() in src or src in cs.lower():
                covered_sections.add(cs)
                break

    return len(covered_sections) / len(core_sections) if core_sections else 0.5


def _score_actionability(issues: List[ReviewIssueProxy]) -> float:
    """Actionability: proportion of issues with a non-trivial suggestion."""
    if not issues:
        return 0.0

    GENERIC_SUGGESTIONS = {
        "please revise", "needs improvement", "consider revising",
        "should be improved", "needs to be fixed", "please fix",
    }

    actionable = 0
    for issue in issues:
        suggestion = (issue.suggestion or "").strip().lower()
        if len(suggestion) > 20:
            is_generic = any(g in suggestion for g in GENERIC_SUGGESTIONS)
            if not is_generic:
                actionable += 1

    return actionable / len(issues)


def _score_calibration(issues: List[ReviewIssueProxy]) -> float:
    """Calibration: severity distribution should not be extreme."""
    if not issues:
        return 0.5

    counts = {"major": 0, "moderate": 0, "minor": 0}
    for issue in issues:
        severity = issue.severity.lower() if issue.severity else "moderate"
        if severity in counts:
            counts[severity] += 1
        else:
            counts["moderate"] += 1

    total = len(issues)
    major_ratio = counts["major"] / total
    minor_ratio = counts["minor"] / total

    score = 1.0
    if major_ratio > 0.7:
        score -= 0.4
    elif major_ratio > 0.5:
        score -= 0.2
    if minor_ratio > 0.7:
        score -= 0.4
    elif minor_ratio > 0.5:
        score -= 0.2
    if total == 1:
        score -= 0.1
    if all(c > 0 for c in counts.values()):
        score += 0.1

    return max(0.0, min(1.0, score))


def _score_evidence(issues: List[ReviewIssueProxy]) -> float:
    """Evidence: proportion of issues whose quote has been verified."""
    if not issues:
        return 0.0

    verified = 0.0
    for issue in issues:
        if issue.quote_verified:
            verified += 1.0
        elif issue.quote and len(issue.quote.strip()) > 15:
            verified += 0.7

    return verified / len(issues)


# ============================================================
# Must-Fix Detection
# ============================================================

def must_fix_check(issues: List[ReviewIssueProxy]) -> List[ReviewIssueProxy]:
    """Identify must-fix blocking issues regardless of composite score."""
    CRITICAL_TYPES = {"logic", "claim_accuracy", "statistical", "methodology"}

    must_fix = []
    for issue in issues:
        if issue.gate_blocker:
            must_fix.append(issue)
        elif (issue.severity == "major" and
              issue.comment_type.lower() in CRITICAL_TYPES):
            must_fix.append(issue)

    return must_fix


# ============================================================
# Main Gate Evaluation
# ============================================================

def evaluate_review_quality(
    issues: List[ReviewIssueProxy],
    paper_sections: Optional[List[str]] = None,
) -> GateVerdict:
    """
    Evaluate the quality of a review (meta-review).

    Args:
        issues: List of review issues (from review_engine consolidation)
        paper_sections: List of section identifiers in the paper

    Returns:
        GateVerdict with pass/fail, scores, and recommendation
    """
    sections = paper_sections or []

    dimension_scores = {
        "specificity": _score_specificity(issues),
        "coverage": _score_coverage(issues, sections),
        "actionability": _score_actionability(issues),
        "calibration": _score_calibration(issues),
        "evidence": _score_evidence(issues),
    }

    composite = sum(
        dimension_scores[dim] * GATE_DIMENSIONS[dim]["weight"]
        for dim in GATE_DIMENSIONS
    )

    weak_dimensions = [
        dim for dim, score in dimension_scores.items()
        if score < GATE_DIMENSIONS[dim]["threshold"]
    ]

    must_fixes = must_fix_check(issues)

    _restart_threshold = _CFG.get("restart_threshold", 0.35)
    _restart_weak_dim_count = _CFG.get("restart_weak_dimension_count", 4)

    if composite >= GATE_PASS_THRESHOLD and not must_fixes:
        recommendation = "ship"
        passed = True
    elif composite < _restart_threshold or len(weak_dimensions) >= _restart_weak_dim_count:
        recommendation = "restart"
        passed = False
    else:
        recommendation = "deepen"
        passed = False

    details = {}
    for dim, score in dimension_scores.items():
        threshold = GATE_DIMENSIONS[dim]["threshold"]
        if score < threshold:
            details[dim] = (
                f"Score {score:.2f} below threshold {threshold:.2f}. "
                f"{GATE_DIMENSIONS[dim]['description']}"
            )
        else:
            details[dim] = f"Score {score:.2f} - adequate."

    return GateVerdict(
        passed=passed,
        composite_score=composite,
        must_fix_count=len(must_fixes),
        dimension_scores=dimension_scores,
        weak_dimensions=weak_dimensions,
        recommendation=recommendation,
        details=details,
    )


# ============================================================
# Formatting for Agent Output
# ============================================================

def format_gate_verdict(verdict: GateVerdict) -> str:
    """Format gate verdict for display to the agent/user."""
    status = "PASSED" if verdict.passed else "NOT PASSED"
    lines = [
        f"## Quality Gate {status}",
        f"Composite Score: {verdict.composite_score:.2f} / 1.00 "
        f"(threshold: {GATE_PASS_THRESHOLD:.2f})",
        f"Recommendation: **{verdict.recommendation}**",
        f"Must-Fix Issues: {verdict.must_fix_count}",
        "",
        "### Dimension Scores:",
    ]

    for dim in GATE_DIMENSIONS:
        score = verdict.dimension_scores[dim]
        threshold = GATE_DIMENSIONS[dim]["threshold"]
        indicator = "[OK]" if score >= threshold else "[LOW]"
        lines.append(f"  {indicator} {dim}: {score:.2f} (threshold: {threshold:.2f})")

    if verdict.weak_dimensions:
        lines.append("")
        lines.append("### Weak Dimensions (need deepening):")
        for dim in verdict.weak_dimensions:
            lines.append(f"  - {dim}: {verdict.details.get(dim, '')}")

    if verdict.recommendation == "deepen":
        lines.append("")
        lines.append(
            "-> Triggering deep review on weak dimensions: "
            + ", ".join(verdict.weak_dimensions)
        )
    elif verdict.recommendation == "restart":
        lines.append("")
        lines.append(
            "-> Review quality is too low across most dimensions. "
            "Consider re-running the full review with adjusted prompts."
        )

    return "\n".join(lines)
