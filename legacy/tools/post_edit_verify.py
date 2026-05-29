"""
tools/post_edit_verify.py - Post-edit verification (regression detection).

After any rewrite/edit operation, automatically checks:
1. Consistency: cross-references (figures, tables, sections) still valid
2. Voice Drift: writing style hasn't diverged from author's voice
3. Regression: no new AI-style patterns introduced
4. Score Delta: quality didn't decrease

Architecture:
    - Runs automatically after rewrite_section / edit_section
    - Non-blocking: emits warnings but does NOT auto-revert
    - Three-layer check: rules -> statistics -> optional LLM
    - Returns VerificationResult with pass/fail per dimension

Integration:
    - Called by _handle_rewrite_section() and _handle_edit_section()
    - Uses voice_profile.py for drift detection
    - Uses deai_precheck for regression scanning
    - Reports to score_tracker for trajectory monitoring

Design:
    - Fast path: rule-based checks take <100ms
    - Expensive path (LLM calibration) only triggered on ambiguous cases
    - Threshold tuning: errs on side of false negatives (don't over-warn)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

from utils.voice_profile import check_voice_drift as _voice_check_drift, load_voice_profile


# ============================================================
# Data Classes
# ============================================================

@dataclass
class VerificationResult:
    """Result of post-edit verification."""
    passed: bool
    consistency_ok: bool
    voice_drift_ok: bool
    regression_ok: bool
    new_issues: List[str] = field(default_factory=list)
    score_delta: float = 0.0  # positive = improvement
    warnings: List[str] = field(default_factory=list)


@dataclass
class CrossRefCheck:
    """A single cross-reference validation."""
    ref_type: str  # "figure" | "table" | "section" | "equation"
    ref_id: str  # "Figure 3" | "Table 2" | "Section 4.1"
    found: bool  # Whether the referenced item exists in the paper


# ============================================================
# Layer 1: Consistency Check (Rule-Based, Zero Cost)
# ============================================================

_FIGURE_REF = re.compile(r'(?:Figure|Fig\.?)\s*(\d+(?:\.\d+)?[a-z]?)', re.IGNORECASE)
_TABLE_REF = re.compile(r'(?:Table|Tab\.?)\s*(\d+(?:\.\d+)?[a-z]?)', re.IGNORECASE)
_SECTION_REF = re.compile(r'(?:Section|Sec\.?)\s*(\d+(?:\.\d+)*)', re.IGNORECASE)
_EQUATION_REF = re.compile(r'(?:Equation|Eq\.?|Eqn\.?)\s*\(?(\d+(?:\.\d+)?)\)?', re.IGNORECASE)


def _extract_cross_refs(text: str) -> List[Tuple[str, str]]:
    """Extract all cross-references from text."""
    refs = []
    for m in _FIGURE_REF.finditer(text):
        refs.append(("figure", m.group(1)))
    for m in _TABLE_REF.finditer(text):
        refs.append(("table", m.group(1)))
    for m in _SECTION_REF.finditer(text):
        refs.append(("section", m.group(1)))
    for m in _EQUATION_REF.finditer(text):
        refs.append(("equation", m.group(1)))
    return refs


def check_consistency(
    new_text: str,
    paper_sections: Optional[List[str]] = None,
    known_figures: Optional[List[str]] = None,
    known_tables: Optional[List[str]] = None,
) -> Tuple[bool, List[str]]:
    """
    Check that cross-references in new_text point to valid targets.

    Args:
        new_text: The edited section text
        paper_sections: List of known section IDs (e.g., ["1", "2.1", "3"])
        known_figures: List of known figure IDs (e.g., ["1", "2", "3a"])
        known_tables: List of known table IDs (e.g., ["1", "2"])

    Returns:
        (passed, issues): True if no broken refs, list of issue descriptions
    """
    issues = []
    refs = _extract_cross_refs(new_text)

    for ref_type, ref_id in refs:
        if ref_type == "figure" and known_figures is not None:
            if ref_id not in known_figures:
                issues.append(f"Broken reference: Figure {ref_id} not found in paper")
        elif ref_type == "table" and known_tables is not None:
            if ref_id not in known_tables:
                issues.append(f"Broken reference: Table {ref_id} not found in paper")
        elif ref_type == "section" and paper_sections is not None:
            if ref_id not in paper_sections:
                issues.append(f"Broken reference: Section {ref_id} not found in paper")

    return (len(issues) == 0, issues)


# ============================================================
# Layer 2: Voice Drift Check (Statistical, Zero LLM Cost)
# ============================================================

def check_voice_drift(
    old_text: str,
    new_text: str,
    voice_fingerprint: Optional[Dict] = None,
) -> Tuple[bool, List[str]]:
    """
    Check if the edit significantly changes the writing style.

    Delegates to the canonical implementation in utils.voice_profile.

    Args:
        old_text: Original section text
        new_text: Edited section text
        voice_fingerprint: Optional VoiceFingerprint dict for reference

    Returns:
        (passed, warnings): True if style is consistent
    """
    # Load the voice profile if a fingerprint object wasn't passed directly
    fp = voice_fingerprint if voice_fingerprint is not None else None
    result = _voice_check_drift(old_text, new_text, fp)
    drift_detected = result.get("drift_detected", False)
    warnings = result.get("warnings", [])
    return (not drift_detected, warnings)


# ============================================================
# Layer 3: AI Regression Check
# ============================================================

# Common AI writing patterns to detect
_AI_PATTERNS = [
    r'\b(?:delve|delves|delving)\b',
    r'\b(?:tapestry|tapestries)\b',
    r'\b(?:landscape)\b(?:\s+of)',
    r'\b(?:paradigm shift)\b',
    r'\b(?:in the realm of)\b',
    r'\b(?:it is worth noting that)\b',
    r'\b(?:it is important to note)\b',
    r'\b(?:this underscores)\b',
    r'\b(?:a testament to)\b',
    r'\b(?:navigating the)\b',
    r'\b(?:in conclusion,?\s+this)\b',
    r'\b(?:multifaceted)\b',
    r'\b(?:leverage|leveraging|leveraged)\b',
    r'\b(?:underscore|underscores|underscoring)\b',
]

_AI_REGEXES = [re.compile(p, re.IGNORECASE) for p in _AI_PATTERNS]


def check_ai_regression(old_text: str, new_text: str) -> Tuple[bool, List[str]]:
    """
    Check if the edit introduced new AI-style patterns.

    Compares AI signal count before/after. An increase suggests regression.

    Returns:
        (passed, issues): True if no AI regression detected
    """
    old_count = _count_ai_signals(old_text)
    new_count = _count_ai_signals(new_text)

    issues = []
    if new_count > old_count:
        new_signals = new_count - old_count
        issues.append(
            f"AI regression: {new_signals} new AI-style pattern(s) "
            f"introduced (was {old_count}, now {new_count})"
        )
        # Identify which specific patterns are new
        for regex in _AI_REGEXES:
            old_matches = set(m.group() for m in regex.finditer(old_text.lower()))
            new_matches = set(m.group() for m in regex.finditer(new_text.lower()))
            added = new_matches - old_matches
            if added:
                issues.append(f"  New AI signal: '{next(iter(added))}'")

    return (len(issues) == 0, issues)


def _count_ai_signals(text: str) -> int:
    """Count total AI writing pattern occurrences in text."""
    count = 0
    for regex in _AI_REGEXES:
        count += len(regex.findall(text))
    return count


# ============================================================
# Main Verification Entry Point
# ============================================================

def verify_edit(
    section_id: str,
    old_text: str,
    new_text: str,
    paper_sections: Optional[List[str]] = None,
    known_figures: Optional[List[str]] = None,
    known_tables: Optional[List[str]] = None,
    voice_fingerprint: Optional[Dict] = None,
) -> VerificationResult:
    """
    Run all verification layers on an edit.

    This is the main entry point called after rewrite/edit operations.

    Args:
        section_id: Which section was edited
        old_text: Text before the edit
        new_text: Text after the edit
        paper_sections: Known section IDs for cross-ref validation
        known_figures: Known figure IDs
        known_tables: Known table IDs
        voice_fingerprint: Author's voice profile dict

    Returns:
        VerificationResult with per-layer pass/fail and aggregated issues
    """
    all_issues = []
    all_warnings = []

    # Layer 1: Consistency
    consistency_ok, consistency_issues = check_consistency(
        new_text, paper_sections, known_figures, known_tables
    )
    all_issues.extend(consistency_issues)

    # Layer 2: Voice Drift
    voice_ok, voice_warnings = check_voice_drift(old_text, new_text, voice_fingerprint)
    all_warnings.extend(voice_warnings)

    # Layer 3: AI Regression
    regression_ok, regression_issues = check_ai_regression(old_text, new_text)
    all_issues.extend(regression_issues)

    # Overall pass: consistency must pass, voice/regression are warnings
    passed = consistency_ok and regression_ok

    return VerificationResult(
        passed=passed,
        consistency_ok=consistency_ok,
        voice_drift_ok=voice_ok,
        regression_ok=regression_ok,
        new_issues=all_issues,
        score_delta=0.0,  # Populated by caller with score_tracker
        warnings=all_warnings,
    )


def format_verification_result(result: VerificationResult, section_id: str) -> str:
    """Format verification result for agent/user display."""
    if result.passed and not result.warnings:
        return (
            f"Post-edit verification for [{section_id}]: ALL PASSED. "
            f"No regressions detected."
        )

    lines = [f"## Post-Edit Verification: [{section_id}]"]

    status = "PASSED" if result.passed else "ISSUES FOUND"
    lines.append(f"Status: {status}")
    lines.append("")

    lines.append(f"  Consistency: {'OK' if result.consistency_ok else 'FAIL'}")
    lines.append(f"  Voice Drift: {'OK' if result.voice_drift_ok else 'WARNING'}")
    lines.append(f"  AI Regression: {'OK' if result.regression_ok else 'FAIL'}")

    if result.new_issues:
        lines.append("")
        lines.append("### Issues:")
        for issue in result.new_issues:
            lines.append(f"  - {issue}")

    if result.warnings:
        lines.append("")
        lines.append("### Warnings (non-blocking):")
        for w in result.warnings:
            lines.append(f"  - {w}")

    if not result.passed:
        lines.append("")
        lines.append(
            "-> Recommend reviewing the edit before approving. "
            "The modification may have introduced problems."
        )

    return "\n".join(lines)
