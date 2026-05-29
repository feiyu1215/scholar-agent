"""
tools/reaudit.py — Re-audit module: structured version comparison for iterative review.

When an author revises their paper after receiving review feedback, this module
compares the new version's issues against the previous review's issue bundle,
producing a structured diff with status labels:

    FULLY_ADDRESSED   — Previous issue is completely resolved
    PARTIALLY_ADDRESSED — Some improvement, but residual problems remain
    NOT_ADDRESSED     — No meaningful change to fix this issue
    NEW               — Issue not present in previous review (regression or new detection)

Core mechanism: matching by `root_cause_key` — a normalized identifier
assigned during consolidation that groups issues by underlying cause rather
than surface manifestation.

Architecture:
    - Zero external dependencies
    - Works with review_engine.py's issue output format
    - Integrates into agent loop as a new tool: `reaudit`
    - Pure rule-based matching + optional LLM for nuanced partial-address detection
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from pathlib import Path

WORKSPACE = Path(".workspace")


# ============================================================
# Data Classes
# ============================================================

@dataclass
class IssueDiff:
    """Diff result for a single issue from the previous review."""
    issue_id: str
    title: str
    category: str
    severity: str
    root_cause_key: str
    status: str             # FULLY_ADDRESSED | PARTIALLY_ADDRESSED | NOT_ADDRESSED | NEW
    evidence: str           # What evidence supports this status determination
    residual_note: str = "" # For PARTIALLY_ADDRESSED: what remains
    # C-4: Severity change tracking
    previous_severity: str = ""   # severity in the previous review
    current_severity: str = ""    # severity in the new review (empty if fully addressed)
    severity_delta: int = 0       # numeric change (-2 = critical→minor = improved a lot; +1 = got worse)
    # C-4: Revision quality score
    revision_quality: float = 0.0  # 0.0 to 1.0 quality of the revision


@dataclass
class ReauditReport:
    """Full re-audit comparison report."""
    total_previous_issues: int
    fully_addressed: int
    partially_addressed: int
    not_addressed: int
    new_issues: int
    improvement_rate: float  # (fully + 0.5*partially) / total_previous
    diffs: List[IssueDiff] = field(default_factory=list)
    summary: str = ""


# ============================================================
# Root Cause Key Generation
# ============================================================

def generate_root_cause_key(issue: Dict) -> str:
    """Generate a normalized root_cause_key for an issue.

    The key groups issues by their underlying cause:
    - Same logical problem in different sentences → same key
    - Same category + same section + similar description → same key

    Strategy: category::section::normalized_core_phrase
    """
    # If already has a root_cause_key, use it
    if issue.get("root_cause_key"):
        return issue["root_cause_key"]

    category = (issue.get("category") or "unknown").lower().strip()
    section = (issue.get("section") or issue.get("source_section") or "unknown").lower().strip()

    # Extract core phrase from title or description
    title = issue.get("title") or issue.get("description") or ""
    core = _normalize_phrase(title)

    return f"{category}::{section}::{core}"


def _normalize_phrase(text: str) -> str:
    """Normalize a phrase for matching: lowercase, strip stopwords, truncate."""
    text = text.lower().strip()
    # Remove common stopwords
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "in", "on", "at",
        "to", "for", "of", "with", "by", "this", "that", "it", "and", "or",
        "but", "not", "from", "as", "be", "has", "have", "had", "do", "does",
    }
    words = [w for w in re.findall(r"[a-z\u4e00-\u9fff]+", text) if w not in stopwords]
    # Take first 6 meaningful words as the core
    core = "_".join(words[:6])
    return core or "generic"


# ============================================================
# Issue Matching Engine
# ============================================================

def match_issues(
    previous_issues: List[Dict],
    current_issues: List[Dict],
) -> Tuple[List[Tuple[Dict, Optional[Dict]]], List[Dict]]:
    """Match previous issues to current issues by root_cause_key.

    Returns:
        matched: List of (previous_issue, current_issue_or_None) pairs
        new_issues: Current issues with no previous match
    """
    # Build key → issue mapping for current
    current_by_key: Dict[str, List[Dict]] = {}
    for issue in current_issues:
        key = generate_root_cause_key(issue)
        issue["_matched_key"] = key
        current_by_key.setdefault(key, []).append(issue)

    matched: List[Tuple[Dict, Optional[Dict]]] = []
    matched_current_ids = set()

    for prev_issue in previous_issues:
        prev_key = generate_root_cause_key(prev_issue)
        prev_issue["_matched_key"] = prev_key

        # Exact key match
        if prev_key in current_by_key and current_by_key[prev_key]:
            current_match = current_by_key[prev_key].pop(0)
            matched.append((prev_issue, current_match))
            matched_current_ids.add(id(current_match))
        else:
            # Try fuzzy match: same category + section, similar core
            fuzzy_match = _fuzzy_match(prev_issue, current_issues, matched_current_ids)
            if fuzzy_match:
                matched.append((prev_issue, fuzzy_match))
                matched_current_ids.add(id(fuzzy_match))
            else:
                # No match → issue was addressed (or detection changed)
                matched.append((prev_issue, None))

    # Remaining unmatched current issues → NEW
    new_issues = [i for i in current_issues if id(i) not in matched_current_ids]

    return matched, new_issues


def _fuzzy_match(
    prev_issue: Dict,
    current_issues: List[Dict],
    already_matched: set,
) -> Optional[Dict]:
    """Fuzzy match: same category + overlapping key words."""
    prev_cat = (prev_issue.get("category") or "").lower()
    prev_section = (prev_issue.get("section") or prev_issue.get("source_section") or "").lower()
    prev_title_words = set(re.findall(
        r"[a-z\u4e00-\u9fff]+",
        (prev_issue.get("title") or prev_issue.get("description") or "").lower()
    ))

    best_match = None
    best_score = 0

    for curr_issue in current_issues:
        if id(curr_issue) in already_matched:
            continue

        curr_cat = (curr_issue.get("category") or "").lower()
        curr_section = (curr_issue.get("section") or curr_issue.get("source_section") or "").lower()

        # Must share category
        if curr_cat != prev_cat:
            continue

        # Same section is a bonus
        section_bonus = 0.3 if curr_section == prev_section else 0

        # Word overlap
        curr_title_words = set(re.findall(
            r"[a-z\u4e00-\u9fff]+",
            (curr_issue.get("title") or curr_issue.get("description") or "").lower()
        ))

        if not prev_title_words or not curr_title_words:
            continue

        overlap = len(prev_title_words & curr_title_words)
        union = len(prev_title_words | curr_title_words)
        jaccard = overlap / union if union > 0 else 0

        score = jaccard + section_bonus

        if score > best_score and score > 0.4:  # Threshold
            best_score = score
            best_match = curr_issue

    return best_match


# ============================================================
# Status Determination
# ============================================================

def determine_status(
    prev_issue: Dict,
    current_match: Optional[Dict],
    current_paper_text: str = "",
) -> IssueDiff:
    """Determine the resolution status of a previous issue.

    Logic:
    - No current match + issue's quoted text changed → FULLY_ADDRESSED
    - No current match + quote still exists → suspicious, check severity
    - Current match with lower severity → PARTIALLY_ADDRESSED
    - Current match with same/higher severity → NOT_ADDRESSED
    """
    issue_id = prev_issue.get("id") or prev_issue.get("_matched_key", "unknown")
    title = prev_issue.get("title") or prev_issue.get("description") or "Untitled"
    category = prev_issue.get("category") or "unknown"
    severity = prev_issue.get("severity") or "unknown"
    root_key = prev_issue.get("_matched_key") or generate_root_cause_key(prev_issue)

    # C-4: Compute severity tracking fields
    prev_numeric = _severity_to_numeric(severity)

    if current_match is None:
        # Issue not found in current review — likely addressed
        status_label = "FULLY_ADDRESSED"
        curr_sev_label = ""
        sev_delta = -prev_numeric  # fully removed = maximum improvement
        quality = _compute_revision_quality(prev_issue, current_match, status_label)

        # Double-check: is the original problematic quote still in the paper?
        quote = prev_issue.get("quote") or ""
        if quote and current_paper_text:
            # Normalize for comparison (allow minor whitespace changes)
            normalized_quote = " ".join(quote.lower().split())
            normalized_paper = " ".join(current_paper_text.lower().split())

            if normalized_quote in normalized_paper:
                # Quote still exists but issue not re-detected
                return IssueDiff(
                    issue_id=issue_id,
                    title=title,
                    category=category,
                    severity=severity,
                    root_cause_key=root_key,
                    status=status_label,
                    evidence=(
                        "Original text preserved but issue not re-detected in current review. "
                        "Likely addressed through context changes or reclassified."
                    ),
                    previous_severity=severity,
                    current_severity=curr_sev_label,
                    severity_delta=sev_delta,
                    revision_quality=quality,
                )
            else:
                return IssueDiff(
                    issue_id=issue_id,
                    title=title,
                    category=category,
                    severity=severity,
                    root_cause_key=root_key,
                    status=status_label,
                    evidence="Original problematic text has been revised.",
                    previous_severity=severity,
                    current_severity=curr_sev_label,
                    severity_delta=sev_delta,
                    revision_quality=quality,
                )
        else:
            return IssueDiff(
                issue_id=issue_id,
                title=title,
                category=category,
                severity=severity,
                root_cause_key=root_key,
                status=status_label,
                evidence="Issue not reproduced in current review.",
                previous_severity=severity,
                current_severity=curr_sev_label,
                severity_delta=sev_delta,
                revision_quality=quality,
            )
    else:
        # Issue still exists in current review — check if improved
        prev_severity_rank = _severity_rank(severity)
        curr_severity = current_match.get("severity") or "unknown"
        curr_severity_rank = _severity_rank(curr_severity)
        curr_numeric = _severity_to_numeric(curr_severity)
        sev_delta = curr_numeric - prev_numeric  # negative = improvement

        if curr_severity_rank < prev_severity_rank:
            # Severity downgraded → partially addressed
            status_label = "PARTIALLY_ADDRESSED"
            quality = _compute_revision_quality(prev_issue, current_match, status_label)
            return IssueDiff(
                issue_id=issue_id,
                title=title,
                category=category,
                severity=severity,
                root_cause_key=root_key,
                status=status_label,
                evidence=(
                    f"Issue persists but severity reduced: {severity} → {curr_severity}."
                ),
                residual_note=current_match.get("title") or current_match.get("description") or "",
                previous_severity=severity,
                current_severity=curr_severity,
                severity_delta=sev_delta,
                revision_quality=quality,
            )
        else:
            # Same or worse → not addressed
            status_label = "NOT_ADDRESSED"
            quality = _compute_revision_quality(prev_issue, current_match, status_label)
            return IssueDiff(
                issue_id=issue_id,
                title=title,
                category=category,
                severity=severity,
                root_cause_key=root_key,
                status=status_label,
                evidence=(
                    f"Issue persists at {curr_severity} severity. "
                    f"Current: {current_match.get('title') or current_match.get('description') or 'same issue'}"
                ),
                previous_severity=severity,
                current_severity=curr_severity,
                severity_delta=sev_delta,
                revision_quality=quality,
            )


def severity_to_numeric(severity: str) -> int:
    """Map severity label to numeric value for delta computation.

    critical=4, major=3, moderate=2, minor=1, info=0
    """
    mapping = {"critical": 4, "major": 3, "moderate": 2, "minor": 1, "info": 0}
    return mapping.get(severity.lower().strip(), 2)


# Backward-compatible aliases
_severity_to_numeric = severity_to_numeric
_severity_rank = severity_to_numeric


def compute_revision_quality(
    prev_issue: Dict,
    current_match: Optional[Dict],
    status: str,
) -> float:
    """Compute revision quality score (0.0–1.0) for a single issue diff.

    Logic:
    - FULLY_ADDRESSED: 1.0
    - PARTIALLY_ADDRESSED: 0.3 + 0.3*(severity_dropped) + evidence_length_bonus(0.0-0.2)
    - NOT_ADDRESSED: 0.0
    - NEW: 0.0 (N/A)
    """
    if status == "FULLY_ADDRESSED":
        return 1.0
    elif status == "PARTIALLY_ADDRESSED" and current_match is not None:
        if prev_issue is None:
            return 0.3  # Partial without prior context → baseline partial score
        prev_sev = _severity_to_numeric(prev_issue.get("severity") or "moderate")
        curr_sev = _severity_to_numeric(current_match.get("severity") or "moderate")
        max_drop = max(prev_sev, 1)  # avoid division by zero
        severity_dropped = max(0.0, (prev_sev - curr_sev) / max_drop)  # 0.0-1.0 normalized
        # Evidence length bonus: longer residual description = more nuanced revision
        residual = current_match.get("title") or current_match.get("description") or ""
        evidence_bonus = min(0.2, len(residual) / 500.0)
        return min(1.0, 0.3 + 0.3 * severity_dropped + evidence_bonus)
    else:
        # NOT_ADDRESSED or NEW
        return 0.0


# Backward-compatible alias
_compute_revision_quality = compute_revision_quality


# ============================================================
# Main Re-audit Pipeline
# ============================================================

def run_reaudit(
    previous_issues_path: str = None,
    current_paper_text: str = "",
) -> ReauditReport:
    """Run full re-audit: compare current review against previous.

    Prerequisites:
    - Previous review must exist (from prior review_paper run, saved as issues)
    - Current review must have been run on the revised paper

    Args:
        previous_issues_path: Path to previous issues JSON. If None, looks in workspace.
        current_paper_text: Full text of current paper (for quote verification).
    """
    # Load previous issues
    if previous_issues_path:
        prev_path = Path(previous_issues_path)
    else:
        prev_path = WORKSPACE / "review" / "previous_issues.json"

    if not prev_path.exists():
        return ReauditReport(
            total_previous_issues=0,
            fully_addressed=0,
            partially_addressed=0,
            not_addressed=0,
            new_issues=0,
            improvement_rate=0.0,
            summary="Error: No previous issues found. Save previous review before re-auditing.",
        )

    previous_issues = json.loads(prev_path.read_text(encoding="utf-8"))
    if isinstance(previous_issues, dict):
        previous_issues = previous_issues.get("issues", [])

    # Load current issues
    current_path = WORKSPACE / "review" / "consolidated.json"
    if not current_path.exists():
        return ReauditReport(
            total_previous_issues=len(previous_issues),
            fully_addressed=0,
            partially_addressed=0,
            not_addressed=0,
            new_issues=0,
            improvement_rate=0.0,
            summary="Error: No current review found. Run review_paper on revised paper first.",
        )

    current_data = json.loads(current_path.read_text(encoding="utf-8"))
    current_issues = current_data.get("issues", []) if isinstance(current_data, dict) else current_data

    # Match and determine status
    matched, new_issues_list = match_issues(previous_issues, current_issues)

    diffs: List[IssueDiff] = []
    for prev_issue, current_match in matched:
        diff = determine_status(prev_issue, current_match, current_paper_text)
        diffs.append(diff)

    # Add NEW issues
    for new_issue in new_issues_list:
        diffs.append(IssueDiff(
            issue_id=new_issue.get("id") or "NEW",
            title=new_issue.get("title") or new_issue.get("description") or "New issue",
            category=new_issue.get("category") or "unknown",
            severity=new_issue.get("severity") or "unknown",
            root_cause_key=generate_root_cause_key(new_issue),
            status="NEW",
            evidence="Issue not present in previous review.",
        ))

    # Compute stats
    fully = sum(1 for d in diffs if d.status == "FULLY_ADDRESSED")
    partially = sum(1 for d in diffs if d.status == "PARTIALLY_ADDRESSED")
    not_addr = sum(1 for d in diffs if d.status == "NOT_ADDRESSED")
    new_count = sum(1 for d in diffs if d.status == "NEW")
    total_prev = len(previous_issues)

    improvement_rate = 0.0
    if total_prev > 0:
        improvement_rate = (fully + 0.5 * partially) / total_prev

    # Generate summary
    if improvement_rate >= 0.8:
        summary = "Excellent revision — most issues have been addressed."
    elif improvement_rate >= 0.5:
        summary = "Solid progress — majority of issues improved, but some remain."
    elif improvement_rate >= 0.3:
        summary = "Partial revision — significant issues still unresolved."
    else:
        summary = "Minimal progress — most original issues persist."

    if new_count > 0:
        summary += f" Note: {new_count} new issue(s) introduced."

    return ReauditReport(
        total_previous_issues=total_prev,
        fully_addressed=fully,
        partially_addressed=partially,
        not_addressed=not_addr,
        new_issues=new_count,
        improvement_rate=improvement_rate,
        diffs=diffs,
        summary=summary,
    )


def save_previous_issues() -> str:
    """Save current consolidated issues as 'previous' for future re-audit.

    Should be called after review_paper completes, before author revises.
    """
    current_path = WORKSPACE / "review" / "consolidated.json"
    if not current_path.exists():
        return "Error: No current review to save. Run review_paper first."

    prev_path = WORKSPACE / "review" / "previous_issues.json"
    data = json.loads(current_path.read_text(encoding="utf-8"))

    # Ensure all issues have root_cause_keys
    issues = data.get("issues", []) if isinstance(data, dict) else data
    for issue in issues:
        if not issue.get("root_cause_key"):
            issue["root_cause_key"] = generate_root_cause_key(issue)

    prev_path.write_text(
        json.dumps(issues, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return f"Saved {len(issues)} issues as baseline for future re-audit."


# ============================================================
# Structured Revision Report (C-4)
# ============================================================

def generate_revision_report(report: ReauditReport) -> dict:
    """Generate a structured revision report dictionary from a ReauditReport.

    Returns a dict with:
    - overview: aggregate metrics
    - per_issue: list of per-issue status dicts
    - summary_text: human-readable one-paragraph summary
    """
    total = report.total_previous_issues or 1  # avoid division by zero
    resolution_rate = (report.fully_addressed + 0.5 * report.partially_addressed) / total

    # Compute avg quality score across all diffs (excluding NEW)
    addressable = [d for d in report.diffs if d.status != "NEW"]
    avg_quality = (
        sum(d.revision_quality for d in addressable) / len(addressable)
        if addressable else 0.0
    )

    # Compute avg severity improvement for addressed issues (FULLY + PARTIALLY)
    addressed = [d for d in report.diffs if d.status in ("FULLY_ADDRESSED", "PARTIALLY_ADDRESSED")]
    severity_improvement = (
        sum(d.severity_delta for d in addressed) / len(addressed)
        if addressed else 0.0
    )

    per_issue = [
        {
            "issue_id": d.issue_id,
            "title": d.title,
            "status": d.status,
            "previous_severity": d.previous_severity,
            "current_severity": d.current_severity,
            "severity_delta": d.severity_delta,
            "revision_quality": round(d.revision_quality, 3),
        }
        for d in report.diffs
    ]

    # Build human-readable summary
    summary_parts = []
    summary_parts.append(
        f"Of {report.total_previous_issues} previously identified issues, "
        f"{report.fully_addressed} were fully resolved and "
        f"{report.partially_addressed} were partially addressed "
        f"(resolution rate: {resolution_rate:.0%})."
    )
    if report.not_addressed > 0:
        summary_parts.append(
            f"{report.not_addressed} issue(s) remain unaddressed."
        )
    if report.new_issues > 0:
        summary_parts.append(
            f"{report.new_issues} new issue(s) were introduced in the revision."
        )
    summary_parts.append(
        f"Average revision quality score: {avg_quality:.2f}/1.0; "
        f"mean severity delta for addressed issues: {severity_improvement:+.2f}."
    )
    summary_text = " ".join(summary_parts)

    return {
        "overview": {
            "total_issues": report.total_previous_issues,
            "resolution_rate": round(resolution_rate, 4),
            "avg_quality_score": round(avg_quality, 4),
            "severity_improvement": round(severity_improvement, 4),
        },
        "per_issue": per_issue,
        "summary_text": summary_text,
    }


# ============================================================
# Report Formatting
# ============================================================

def format_reaudit_report(report: ReauditReport) -> str:
    """Format the re-audit report for display."""
    lines = []
    lines.append("=" * 60)
    lines.append("RE-AUDIT REPORT — Revision Progress Analysis")
    lines.append("=" * 60)

    # Stats
    lines.append(f"\nPrevious issues: {report.total_previous_issues}")
    lines.append(f"Improvement rate: {report.improvement_rate:.0%}")
    lines.append(f"\n  ✓ Fully addressed:     {report.fully_addressed}")
    lines.append(f"  ◐ Partially addressed: {report.partially_addressed}")
    lines.append(f"  ✗ Not addressed:       {report.not_addressed}")
    lines.append(f"  ★ New issues:          {report.new_issues}")
    lines.append(f"\n{report.summary}")

    # Not addressed (highest priority)
    not_addressed = [d for d in report.diffs if d.status == "NOT_ADDRESSED"]
    if not_addressed:
        lines.append("\n" + "─" * 40)
        lines.append("✗ NOT ADDRESSED (requires attention)")
        lines.append("─" * 40)
        for d in not_addressed:
            lines.append(f"\n  [{d.severity.upper()}] {d.title}")
            lines.append(f"    Category: {d.category}")
            if d.previous_severity and d.current_severity:
                lines.append(f"    Severity: {d.previous_severity} → {d.current_severity} (delta: {d.severity_delta:+d})")
            lines.append(f"    Evidence: {d.evidence}")

    # Partially addressed
    partial = [d for d in report.diffs if d.status == "PARTIALLY_ADDRESSED"]
    if partial:
        lines.append("\n" + "─" * 40)
        lines.append("◐ PARTIALLY ADDRESSED (improved but incomplete)")
        lines.append("─" * 40)
        for d in partial:
            lines.append(f"\n  [{d.severity.upper()}] {d.title}")
            if d.previous_severity and d.current_severity:
                lines.append(f"    Severity: {d.previous_severity} → {d.current_severity} (delta: {d.severity_delta:+d})")
            lines.append(f"    Quality: {d.revision_quality:.2f}")
            lines.append(f"    Evidence: {d.evidence}")
            if d.residual_note:
                lines.append(f"    Remaining: {d.residual_note}")

    # New issues
    new_issues = [d for d in report.diffs if d.status == "NEW"]
    if new_issues:
        lines.append("\n" + "─" * 40)
        lines.append("★ NEW ISSUES (introduced in revision)")
        lines.append("─" * 40)
        for d in new_issues:
            lines.append(f"\n  [{d.severity.upper()}] {d.title}")
            lines.append(f"    Category: {d.category}")

    # Fully addressed (brief summary)
    fully = [d for d in report.diffs if d.status == "FULLY_ADDRESSED"]
    if fully:
        lines.append("\n" + "─" * 40)
        lines.append(f"✓ FULLY ADDRESSED ({len(fully)} issues resolved)")
        lines.append("─" * 40)
        for d in fully[:5]:  # Show top 5
            lines.append(f"  ✓ {d.title}")
        if len(fully) > 5:
            lines.append(f"  ... and {len(fully) - 5} more")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)
