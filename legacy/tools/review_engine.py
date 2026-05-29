"""
tools/review_engine.py — Multi-role review via subagent pattern.

Each reviewer role (editor, theory, methodology, logic, literature) runs in
its own isolated context with only the relevant section(s) loaded.
This is the s04 subagent pattern applied to academic review.

Design choices:
- Each reviewer sees ONLY the section(s) relevant to their focus + the abstract
- Reviewers output structured issues (JSON), not free-form text
- Issues are anchored to specific locations (section_id + quote)
- Final consolidation happens after all reviewers finish

v3 Enhancements:
- ReviewIssue dataclass: strongly typed issue schema with root_cause_key
- Quote verification: validates LLM-generated quotes against paper text
- Rule-based deduplication (3-pass): exact key → quote overlap → title prefix
- Consensus scoring: deterministic score calculation (no LLM involved)
- Hybrid pipeline: LLM consolidation THEN rule-based post-processing
"""

from __future__ import annotations

import json
import hashlib
import asyncio
import statistics
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Union

from config import load_thresholds
from llm.client import LLMClient
from llm.router import get_model_for_task
from utils.json_repair import robust_json_parse
from tools.focus_generator import generate_focus_points

_REVIEW_CFG = load_thresholds().get("review_engine", {})
_CONSENSUS = _REVIEW_CFG.get("consensus", {})
_VERDICT_BOUNDARIES = _REVIEW_CFG.get("verdict_boundaries", {})

CONSENSUS_START_SCORE = _CONSENSUS.get("start_score", 9.0)
CONSENSUS_MAJOR_DEDUCTION = _CONSENSUS.get("major_deduction", 1.5)
CONSENSUS_MODERATE_DEDUCTION = _CONSENSUS.get("moderate_deduction", 0.7)
CONSENSUS_MINOR_DEDUCTION = _CONSENSUS.get("minor_deduction", 0.2)
CONSENSUS_SCORE_FLOOR = _CONSENSUS.get("score_floor", 1.0)
CONSENSUS_DESK_REJECT_CAP = _CONSENSUS.get("desk_reject_cap", 4.0)

VERDICT_STRONG_ACCEPT = _VERDICT_BOUNDARIES.get("strong_accept", 8.0)
VERDICT_ACCEPT = _VERDICT_BOUNDARIES.get("accept", 7.0)
VERDICT_WEAK_ACCEPT = _VERDICT_BOUNDARIES.get("weak_accept", 6.0)
VERDICT_BORDERLINE = _VERDICT_BOUNDARIES.get("borderline", 5.0)
VERDICT_WEAK_REJECT = _VERDICT_BOUNDARIES.get("weak_reject", 4.0)
VERDICT_REJECT = _VERDICT_BOUNDARIES.get("reject", 2.5)

WORKSPACE = Path(".workspace")


# ============================================================
# Structured Issue Schema
# ============================================================

@dataclass
class ReviewIssue:
    """Structured review issue with full provenance tracking.

    Fields:
        title: short summary (< 15 words)
        quote: exact text from the paper this issue refers to
        explanation: why this is a problem
        comment_type: taxonomy of issue
        severity: how bad (major/moderate/minor)
        confidence: reviewer certainty (high/medium/low)
        source_section: primary section where issue lives
        related_sections: other sections affected
        root_cause_key: normalized hash for cross-reviewer dedup
        review_lane: which reviewer role found this
        gate_blocker: True = submission blocker regardless of severity
        quote_verified: True = quote was validated against paper text
        suggestion: concrete fix suggestion
    """
    title: str
    quote: str
    explanation: str
    comment_type: str             # "methodology" | "claim_accuracy" | "presentation" |
                                  # "missing_information" | "statistical" | "logic" | "novelty"
    severity: str                 # "major" | "moderate" | "minor"
    confidence: str               # "high" | "medium" | "low"
    source_section: str
    related_sections: List[str] = field(default_factory=list)
    root_cause_key: str = ""
    review_lane: str = ""
    gate_blocker: bool = False
    quote_verified: bool = False
    suggestion: str = ""
    impact_dimensions: Dict[str, float] = field(default_factory=dict)  # TODO-5
    severity_assessment: Optional[Dict] = None  # TODO-5: SeverityAssessment.to_dict()

    def to_dict(self) -> Dict:
        """Serialize to dict for JSON output."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "ReviewIssue":
        """Construct from dict (JSON deserialization)."""
        return cls(
            title=data.get("title", ""),
            quote=data.get("quote", ""),
            explanation=data.get("explanation", ""),
            comment_type=data.get("comment_type", "presentation"),
            severity=data.get("severity", "minor"),
            confidence=data.get("confidence", "medium"),
            source_section=data.get("source_section", ""),
            related_sections=data.get("related_sections", []),
            root_cause_key=data.get("root_cause_key", ""),
            review_lane=data.get("review_lane", ""),
            gate_blocker=data.get("gate_blocker", False),
            quote_verified=data.get("quote_verified", False),
            suggestion=data.get("suggestion", ""),
            impact_dimensions=data.get("impact_dimensions", {}),
            severity_assessment=data.get("severity_assessment", None),
        )

    @classmethod
    def from_llm_issue(cls, issue_dict: Dict, reviewer_role: str = "") -> "ReviewIssue":
        """Convert LLM-output issue (old format) to ReviewIssue.

        Handles both the LLM consolidation format and individual reviewer format.
        """
        # Extract location info
        location = issue_dict.get("location", {})
        if isinstance(location, dict):
            source_section = location.get("section_id", "")
            quote = location.get("quote", "")
        elif isinstance(location, str):
            source_section = location
            quote = ""
        else:
            source_section = ""
            quote = ""

        title = issue_dict.get("category", issue_dict.get("title", ""))
        explanation = issue_dict.get("description", issue_dict.get("explanation", ""))
        severity = issue_dict.get("severity", "minor")
        suggestion = issue_dict.get("suggestion", "")
        comment_type = _infer_comment_type(issue_dict, reviewer_role)
        confidence = _infer_confidence(issue_dict)

        root_cause_key = generate_root_cause_key(comment_type, source_section, title)

        issue = cls(
            title=title,
            quote=quote,
            explanation=explanation,
            comment_type=comment_type,
            severity=severity,
            confidence=confidence,
            source_section=source_section,
            related_sections=[],
            root_cause_key=root_cause_key,
            review_lane=reviewer_role or issue_dict.get("reviewer", ""),
            gate_blocker=(severity == "major"),
            quote_verified=False,
            suggestion=suggestion,
        )

        # TODO-5: Multi-dimensional severity assessment
        assessment = assess_severity(issue)
        issue.impact_dimensions = assessment.impact_dimensions
        issue.severity_assessment = assessment.to_dict()
        # Reconcile: upgrade severity if computed is more severe
        reconciled = reconcile_severity(severity, assessment)
        if reconciled != severity:
            issue.severity = reconciled
            issue.gate_blocker = (reconciled == "major")

        return issue


def _infer_comment_type(issue_dict: Dict, reviewer_role: str) -> str:
    """Infer comment_type from issue content and reviewer role."""
    category = (issue_dict.get("category", "") + " " +
                issue_dict.get("description", "")).lower()

    if any(kw in category for kw in ["method", "statistical", "sample", "validity", "reproducib"]):
        return "methodology"
    if any(kw in category for kw in ["claim", "overclaim", "evidence", "support"]):
        return "claim_accuracy"
    if any(kw in category for kw in ["logic", "contradiction", "coherence", "follow"]):
        return "logic"
    if any(kw in category for kw in ["novel", "contribution", "incremental"]):
        return "novelty"
    if any(kw in category for kw in ["missing", "gap", "citation", "reference", "literature"]):
        return "missing_information"
    if any(kw in category for kw in ["statistic", "p-value", "regression", "robust"]):
        return "statistical"

    role_type_map = {
        "methodology": "methodology",
        "theory": "novelty",
        "logic": "logic",
        "literature": "missing_information",
        "editor": "presentation",
    }
    return role_type_map.get(reviewer_role, "presentation")


def _infer_confidence(issue_dict: Dict) -> str:
    """Infer confidence from issue metadata."""
    if issue_dict.get("confidence"):
        return issue_dict["confidence"]
    severity = issue_dict.get("severity", "")
    if severity == "major":
        return "high"
    if severity == "moderate":
        return "medium"
    return "low"


def generate_root_cause_key(comment_type: str, source_section: str, title: str) -> str:
    """Generate normalized root-cause key for cross-reviewer deduplication.

    Based on comment_type + source_section + first 5 words of title (hashed).
    """
    title_words = title.strip().split()[:5]
    title_prefix = " ".join(title_words).lower()
    raw = f"{comment_type}|{source_section}|{title_prefix}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ============================================================
# Multi-Dimensional Severity Assessment (TODO-5)
# ============================================================
#
# Instead of relying solely on LLM's "major/moderate/minor" string, we compute
# severity from the issue's impact across 5 academic quality dimensions.
# High-weight dimensions (academic_integrity, methodology_rigor) can auto-upgrade
# severity, making the assessment more consistent and defensible.

# Impact dimensions with weights (sum = 1.0)
SEVERITY_DIMENSIONS = {
    "argumentation_logic":  0.25,  # Reasoning chain integrity, causal logic
    "methodology_rigor":    0.25,  # Methodological soundness, reproducibility
    "expression_clarity":   0.15,  # Writing quality, readability, presentation
    "academic_integrity":   0.20,  # Citation validity, data truthfulness, ethics
    "completeness":         0.15,  # Missing information, gaps, coverage
}

# Dimensions that auto-upgrade severity to "major" when impact >= 0.7
HIGH_WEIGHT_DIMENSIONS = {"academic_integrity", "methodology_rigor"}

# comment_type → primary affected dimensions (with default impact levels)
COMMENT_TYPE_DIMENSION_MAP = {
    "methodology": {
        "methodology_rigor": 0.8,
        "argumentation_logic": 0.4,
    },
    "claim_accuracy": {
        "academic_integrity": 0.7,
        "argumentation_logic": 0.6,
    },
    "logic": {
        "argumentation_logic": 0.8,
        "methodology_rigor": 0.3,
    },
    "statistical": {
        "methodology_rigor": 0.7,
        "argumentation_logic": 0.5,
    },
    "novelty": {
        "argumentation_logic": 0.5,
        "completeness": 0.4,
    },
    "missing_information": {
        "completeness": 0.7,
        "methodology_rigor": 0.3,
    },
    "presentation": {
        "expression_clarity": 0.6,
        "completeness": 0.2,
    },
}

# Severity thresholds based on weighted impact score
SEVERITY_SCORE_THRESHOLDS = {
    "major": 0.55,      # Weighted score >= 0.55 → major
    "moderate": 0.30,   # Weighted score >= 0.30 → moderate
    # Below 0.30 → minor
}


@dataclass
class SeverityAssessment:
    """Multi-dimensional severity assessment result (TODO-5)."""
    computed_severity: str                   # "major" | "moderate" | "minor"
    impact_dimensions: Dict[str, float]      # dimension → impact score (0.0-1.0)
    weighted_score: float                    # Weighted sum of impacts
    auto_upgraded: bool = False              # True if high-weight dim triggered upgrade
    upgrade_reason: str = ""                 # Why severity was upgraded

    def to_dict(self) -> Dict:
        return {
            "computed_severity": self.computed_severity,
            "impact_dimensions": {k: round(v, 3) for k, v in self.impact_dimensions.items()},
            "weighted_score": round(self.weighted_score, 3),
            "auto_upgraded": self.auto_upgraded,
            "upgrade_reason": self.upgrade_reason,
        }


def compute_impact_dimensions(issue: ReviewIssue) -> Dict[str, float]:
    """Compute per-dimension impact scores for a review issue.

    Logic:
    - Start from COMMENT_TYPE_DIMENSION_MAP base impacts
    - Scale by confidence: high=1.0, medium=0.75, low=0.5
    - Boost if explanation is detailed (>100 chars) → +0.1 to primary dim
    - Dimensions not in map default to 0.0
    """
    # Confidence multiplier
    confidence_scale = {"high": 1.0, "medium": 0.75, "low": 0.5}
    conf_mult = confidence_scale.get(issue.confidence, 0.75)

    # Get base impacts from comment type
    base_impacts = COMMENT_TYPE_DIMENSION_MAP.get(
        issue.comment_type, {"expression_clarity": 0.4}
    )

    # Compute final impacts
    impacts = {}
    for dim in SEVERITY_DIMENSIONS:
        base = base_impacts.get(dim, 0.0)
        impacts[dim] = min(1.0, base * conf_mult)

    # Bonus for detailed explanation (signals reviewer confidence)
    if len(issue.explanation) > 100:
        primary_dim = max(base_impacts, key=base_impacts.get) if base_impacts else None
        if primary_dim:
            impacts[primary_dim] = min(1.0, impacts[primary_dim] + 0.1)

    return impacts


def assess_severity(issue: ReviewIssue) -> SeverityAssessment:
    """Compute multi-dimensional severity for a review issue (TODO-5).

    Returns SeverityAssessment with:
    - Computed severity based on weighted impact score
    - Auto-upgrade if high-weight dimensions exceed threshold
    - Full dimension breakdown for diagnostics
    """
    impacts = compute_impact_dimensions(issue)

    # Compute weighted score
    weighted_score = sum(
        impacts.get(dim, 0.0) * weight
        for dim, weight in SEVERITY_DIMENSIONS.items()
    )

    # Determine base severity from threshold
    if weighted_score >= SEVERITY_SCORE_THRESHOLDS["major"]:
        computed_severity = "major"
    elif weighted_score >= SEVERITY_SCORE_THRESHOLDS["moderate"]:
        computed_severity = "moderate"
    else:
        computed_severity = "minor"

    # Auto-upgrade check: high-weight dimensions with high impact
    auto_upgraded = False
    upgrade_reason = ""
    for dim in HIGH_WEIGHT_DIMENSIONS:
        if impacts.get(dim, 0.0) >= 0.7 and computed_severity != "major":
            auto_upgraded = True
            computed_severity = "major"
            upgrade_reason = (
                f"High impact on '{dim}' ({impacts[dim]:.2f}) "
                f"auto-upgraded severity to major"
            )
            break

    return SeverityAssessment(
        computed_severity=computed_severity,
        impact_dimensions=impacts,
        weighted_score=weighted_score,
        auto_upgraded=auto_upgraded,
        upgrade_reason=upgrade_reason,
    )


def reconcile_severity(
    llm_severity: str,
    assessment: SeverityAssessment,
    trust_llm: bool = True,
) -> str:
    """Reconcile LLM-assigned severity with computed severity.

    Policy:
    - If computed severity is MORE severe than LLM → upgrade (computed wins)
    - If computed severity is LESS severe than LLM → keep LLM (trust human-like judgment)
    - Unless trust_llm=False, in which case computed always wins

    This ensures we never downgrade a severity that the LLM flagged,
    but we DO upgrade when structured analysis reveals higher impact.
    """
    severity_rank = {"major": 3, "moderate": 2, "minor": 1}
    llm_rank = severity_rank.get(llm_severity, 1)
    computed_rank = severity_rank.get(assessment.computed_severity, 1)

    if not trust_llm:
        return assessment.computed_severity
    
    # Upgrade only — never downgrade LLM's call
    if computed_rank > llm_rank:
        return assessment.computed_severity
    return llm_severity


# ============================================================
# Quote Verification
# ============================================================

def verify_quotes(issues: List[ReviewIssue], paper_text: str) -> List[ReviewIssue]:
    """Verify each issue's quote actually exists in the paper text.

    Two-stage strategy:
    1. Normalized substring search (collapse whitespace)
    2. If substring fails, sliding-window SequenceMatcher (ratio > 0.85 = match)

    This catches LLM "creative quoting" — the #1 source of false issues.
    """
    def normalize(text: str) -> str:
        return " ".join(text.split())

    normalized_paper = normalize(paper_text)

    for issue in issues:
        if not issue.quote or not issue.quote.strip():
            issue.quote_verified = False
            continue

        normalized_quote = normalize(issue.quote)

        # Strategy 1: substring search
        if normalized_quote in normalized_paper:
            issue.quote_verified = True
            continue

        # Strategy 2: sliding window fuzzy match
        quote_len = len(normalized_quote)
        if quote_len == 0:
            issue.quote_verified = False
            continue

        best_ratio = 0.0
        step = max(1, quote_len // 4)
        for start in range(0, len(normalized_paper) - quote_len + 1, step):
            window = normalized_paper[start:start + quote_len]
            ratio = SequenceMatcher(None, normalized_quote, window).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
            if best_ratio > 0.85:
                break

        issue.quote_verified = best_ratio > 0.85

    return issues


# ============================================================
# Cross-reviewer Deduplication & Consolidation (Rule-based)
# ============================================================

def _calculate_quote_overlap(quote1: str, quote2: str) -> float:
    """Word-level Jaccard similarity between two quotes."""
    if not quote1 or not quote2:
        return 0.0
    words1 = set(quote1.lower().split())
    words2 = set(quote2.lower().split())
    if not words1 or not words2:
        return 0.0
    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union)


def _normalize_title_prefix(title: str) -> str:
    """Extract normalized first-5-words prefix for dedup comparison."""
    words = title.strip().lower().split()[:5]
    return " ".join(words)


def _merge_two_issues(primary: ReviewIssue, secondary: ReviewIssue) -> ReviewIssue:
    """Merge two duplicate issues, keeping the most valuable information.

    Policy:
    - explanation: keep longer one
    - suggestion: keep longer one
    - severity: keep highest
    - confidence: keep highest
    - gate_blocker: OR
    - quote: keep longer one
    - related_sections: union
    """
    merged_sections = list(set(primary.related_sections + secondary.related_sections))

    explanation = (secondary.explanation if len(secondary.explanation) > len(primary.explanation)
                   else primary.explanation)
    suggestion = primary.suggestion
    if not suggestion and secondary.suggestion:
        suggestion = secondary.suggestion
    elif suggestion and secondary.suggestion and len(secondary.suggestion) > len(suggestion):
        suggestion = secondary.suggestion

    severity_order = {"major": 3, "moderate": 2, "minor": 1}
    severity = (secondary.severity
                if severity_order.get(secondary.severity, 0) > severity_order.get(primary.severity, 0)
                else primary.severity)

    confidence_order = {"high": 3, "medium": 2, "low": 1}
    confidence = (secondary.confidence
                  if confidence_order.get(secondary.confidence, 0) > confidence_order.get(primary.confidence, 0)
                  else primary.confidence)

    gate_blocker = primary.gate_blocker or secondary.gate_blocker

    merged_issue = ReviewIssue(
        title=primary.title,
        quote=primary.quote if len(primary.quote) >= len(secondary.quote) else secondary.quote,
        explanation=explanation,
        comment_type=primary.comment_type,
        severity=severity,
        confidence=confidence,
        source_section=primary.source_section,
        related_sections=merged_sections,
        root_cause_key=primary.root_cause_key,
        review_lane=primary.review_lane,
        gate_blocker=gate_blocker,
        quote_verified=primary.quote_verified or secondary.quote_verified,
        suggestion=suggestion,
    )

    # Re-assess severity for the merged issue (TODO-5)
    assessment = assess_severity(merged_issue)
    merged_issue.impact_dimensions = assessment.impact_dimensions
    merged_issue.severity_assessment = assessment.to_dict()
    reconciled = reconcile_severity(severity, assessment)
    if reconciled != severity:
        merged_issue.severity = reconciled
        merged_issue.gate_blocker = (reconciled == "major")

    return merged_issue


def consolidate_issues(all_issues: List[ReviewIssue]) -> List[ReviewIssue]:
    """Three-pass rule-based deduplication across multiple reviewers.

    Pass 1: Exact duplicate by root_cause_key
            (same key → merge, unless explanations differ by >3x length)
    Pass 2: Quote overlap > 70% Jaccard → merge
    Pass 3: Same section + same comment_type + same title prefix → merge

    Filtering:
    - gate_blocker=True issues are ALWAYS kept
    - singleton low-confidence issues are dropped (likely noise)

    Returns:
        Deduplicated issues sorted by severity desc → confidence desc.
    """
    if not all_issues:
        return []

    merged_flags = [False] * len(all_issues)

    # --- Pass 1: Exact duplicate by root_cause_key ---
    root_cause_groups: Dict[str, List[int]] = {}
    for idx, issue in enumerate(all_issues):
        key = issue.root_cause_key
        if key:
            root_cause_groups.setdefault(key, []).append(idx)

    for key, indices in root_cause_groups.items():
        if len(indices) <= 1:
            continue

        # Check if explanations are substantively different
        explanations = [(idx, all_issues[idx].explanation) for idx in indices]
        explanations.sort(key=lambda x: len(x[1]), reverse=True)

        # Group: explanations within 3x length ratio are "same problem"
        sub_groups: List[List[int]] = []
        used: set = set()
        for i, (idx_i, exp_i) in enumerate(explanations):
            if idx_i in used:
                continue
            group = [idx_i]
            used.add(idx_i)
            for j, (idx_j, exp_j) in enumerate(explanations):
                if idx_j in used:
                    continue
                len_i = max(len(exp_i), 1)
                len_j = max(len(exp_j), 1)
                ratio = max(len_i, len_j) / min(len_i, len_j)
                if ratio < 3.0:
                    group.append(idx_j)
                    used.add(idx_j)
            sub_groups.append(group)

        for group in sub_groups:
            if len(group) <= 1:
                continue
            severity_order = {"major": 3, "moderate": 2, "minor": 1}
            group.sort(
                key=lambda idx: severity_order.get(all_issues[idx].severity, 0),
                reverse=True,
            )
            primary_idx = group[0]
            merged_issue = all_issues[primary_idx]
            for secondary_idx in group[1:]:
                merged_issue = _merge_two_issues(merged_issue, all_issues[secondary_idx])
                merged_flags[secondary_idx] = True
            all_issues[primary_idx] = merged_issue

    # --- Pass 2: Quote overlap > 70% ---
    for i in range(len(all_issues)):
        if merged_flags[i]:
            continue
        for j in range(i + 1, len(all_issues)):
            if merged_flags[j]:
                continue
            overlap = _calculate_quote_overlap(all_issues[i].quote, all_issues[j].quote)
            if overlap > 0.70:
                all_issues[i] = _merge_two_issues(all_issues[i], all_issues[j])
                merged_flags[j] = True

    # --- Pass 3: Same section + same comment_type + similar title ---
    for i in range(len(all_issues)):
        if merged_flags[i]:
            continue
        for j in range(i + 1, len(all_issues)):
            if merged_flags[j]:
                continue
            issue_i = all_issues[i]
            issue_j = all_issues[j]

            if (issue_i.source_section == issue_j.source_section
                    and issue_i.comment_type == issue_j.comment_type):
                prefix_i = _normalize_title_prefix(issue_i.title)
                prefix_j = _normalize_title_prefix(issue_j.title)
                if prefix_i and prefix_j and prefix_i == prefix_j:
                    all_issues[i] = _merge_two_issues(all_issues[i], all_issues[j])
                    merged_flags[j] = True

    # --- Collect survivors ---
    result: List[ReviewIssue] = []
    for i in range(len(all_issues)):
        if merged_flags[i]:
            continue
        issue = all_issues[i]
        if issue.gate_blocker:
            result.append(issue)
        elif issue.confidence == "low":
            key = issue.root_cause_key
            group_size = len(root_cause_groups.get(key, [key]))
            if group_size <= 1:
                continue  # Drop singleton low-confidence
            else:
                result.append(issue)
        else:
            result.append(issue)

    # --- Sort: severity desc → confidence desc ---
    severity_order = {"major": 3, "moderate": 2, "minor": 1}
    confidence_order = {"high": 3, "medium": 2, "low": 1}
    result.sort(
        key=lambda issue: (
            severity_order.get(issue.severity, 0),
            confidence_order.get(issue.confidence, 0),
        ),
        reverse=True,
    )

    return result


# ============================================================
# Consensus Scoring (deterministic, no LLM)
# ============================================================

def calculate_consensus_score(issues: List[ReviewIssue], desk_reject: bool = False) -> Dict:
    """Calculate consensus score from structured issues.

    Formula: start at 9.0
    - Per major issue: -1.5
    - Per moderate issue: -0.7
    - Per minor issue: -0.2
    - Floor at 1.0
    - If desk_reject=True, cap at 4.0

    Returns scoring breakdown with verdict mapping:
        ≥8.0: strong_accept | ≥7.0: accept | ≥6.0: weak_accept |
        ≥5.0: borderline | ≥4.0: weak_reject | ≥2.5: reject | <2.5: strong_reject
    """
    major_count = sum(1 for i in issues if i.severity == "major")
    moderate_count = sum(1 for i in issues if i.severity == "moderate")
    minor_count = sum(1 for i in issues if i.severity == "minor")

    major_deduction = major_count * CONSENSUS_MAJOR_DEDUCTION
    moderate_deduction = moderate_count * CONSENSUS_MODERATE_DEDUCTION
    minor_deduction = minor_count * CONSENSUS_MINOR_DEDUCTION

    score = CONSENSUS_START_SCORE - major_deduction - moderate_deduction - minor_deduction
    score = max(CONSENSUS_SCORE_FLOOR, score)

    if desk_reject:
        score = min(score, CONSENSUS_DESK_REJECT_CAP)

    if score >= VERDICT_STRONG_ACCEPT:
        verdict = "strong_accept"
    elif score >= VERDICT_ACCEPT:
        verdict = "accept"
    elif score >= VERDICT_WEAK_ACCEPT:
        verdict = "weak_accept"
    elif score >= VERDICT_BORDERLINE:
        verdict = "borderline"
    elif score >= VERDICT_WEAK_REJECT:
        verdict = "weak_reject"
    elif score >= VERDICT_REJECT:
        verdict = "reject"
    else:
        verdict = "strong_reject"

    gate_blockers = [i.title for i in issues if i.gate_blocker]

    return {
        "score": round(score, 2),
        "breakdown": {
            "major_count": major_count,
            "moderate_count": moderate_count,
            "minor_count": minor_count,
            "major_deduction": round(major_deduction, 2),
            "moderate_deduction": round(moderate_deduction, 2),
            "minor_deduction": round(minor_deduction, 2),
        },
        "verdict": verdict,
        "gate_blockers": gate_blockers,
        "desk_reject": desk_reject,
    }


# ============================================================
# Reviewer Role Configuration
# ============================================================

REVIEWER_ROLES = {
    "editor": {
        "focus": "Desk-reject screening: novelty, scope, presentation quality, fatal flaws",
        "reads": ["abstract", "introduction"],
    },
    "theory": {
        "focus": "Theoretical contribution: novelty of argument, dialogue with existing theory, logical rigor of claims",
        "reads": ["introduction", "model", "theory", "discussion"],
    },
    "methodology": {
        "focus": "Methods transparency: reproducibility, validity threats, sample adequacy, statistical rigor",
        "reads": ["methodology", "methods", "data", "results"],
    },
    "logic": {
        "focus": "Argument coherence: do claims follow from evidence? Internal contradictions? Overclaims?",
        "reads": ["introduction", "results", "discussion", "conclusion"],
    },
    "literature": {
        "focus": "Literature dialogue: is the gap genuine? Selective citation? Missing key references?",
        "reads": ["introduction", "related_work", "literature_review", "discussion"],
    },
}


# ============================================================
# LLM Prompts
# ============================================================

REVIEW_SYSTEM_PROMPT = """You are an academic reviewer ({role}) evaluating a research paper.

Your focus: {focus}

You must output a JSON array of issues found. Each issue MUST have these fields:
- "title": short issue title (< 15 words)
- "quote": EXACT text from the paper this issue refers to (copy-paste, not paraphrase)
- "explanation": why this is a problem
- "severity": "major" | "moderate" | "minor"
- "suggestion": concrete suggestion for improvement
- "source_section": which section this is in

If you find no issues, return an empty array [].

CRITICAL: The "quote" field must be VERBATIM text from the paper. Do not paraphrase or reconstruct from memory.

Be rigorous but fair. Anchor every finding to specific text. Do not fabricate problems.
Do not comment on formatting/typos unless they impede understanding."""

CONSOLIDATION_PROMPT = """You are a senior editor consolidating reviews from 5 independent reviewers.

You have received separate review outputs from: Editor, Theory, Methodology, Logic, Literature reviewers.

Your job:
1. Merge duplicate/overlapping issues (keep the more detailed version)
2. Assign final severity: major (submission blocker), moderate (should fix), minor (nice to fix)
3. For EACH issue, classify an action_type using these rules:
   - "guidance": issue requires information NOT in the paper (new data, experiments, references the author must find)
   - "confirm_fix": issue touches core argument framing, author's subjective choices, or causal claims
   - "auto_fix": issue is clearly fixable from existing text (grammar, structure, citation format, logical connectors, hedging)
4. Order by severity then by section order
5. Produce a revision roadmap: what to fix first, what can wait
6. Give an overall score (1-10) using: start at 9.0, subtract 1.5 per major, 0.7 per moderate, 0.2 per minor, floor at 1.0

Each issue in the output MUST include these fields:
- "id": sequential ID like "ISS-001"
- "title": short issue title
- "severity": "major" | "moderate" | "minor"
- "category": brief category name
- "location": {"section_id": "...", "quote": "..."}
- "description": clear explanation
- "suggestion": concrete suggestion
- "action_type": "auto_fix" | "confirm_fix" | "guidance"
- "action_rationale": one sentence explaining WHY this action_type was chosen
- "fix_complexity": "sentence_level" | "paragraph_level" | "section_level" | "cross_section"

Output format:
{
  "overall_score": <float>,
  "verdict": "accept" | "minor_revision" | "major_revision" | "reject",
  "total_issues": {"major": N, "moderate": N, "minor": N},
  "action_summary": {"auto_fix": N, "confirm_fix": N, "guidance": N},
  "issues": [<consolidated issue list with action_type>],
  "revision_roadmap": [<ordered list of what to fix>],
  "strengths": [<what the paper does well>]
}"""


# ============================================================
# Reviewer Selection (configurable subset)
# ============================================================

# Mapping from focus dimension keywords to the most relevant reviewer roles.
# Used when reviewer_count is set to select the most appropriate subset.
DIMENSION_TO_ROLES = {
    "clarity": ["editor", "logic"],
    "writing": ["editor", "literature"],
    "methodology": ["methodology", "logic"],
    "rigor": ["methodology", "theory"],
    "novelty": ["theory", "editor"],
    "contribution": ["theory", "editor"],
    "structure": ["editor", "logic"],
    "organization": ["editor", "logic"],
    "logic": ["logic", "theory"],
    "coherence": ["logic", "theory"],
    "literature": ["literature", "theory"],
    "citations": ["literature", "methodology"],
    "theory": ["theory", "literature"],
}


def _select_reviewers(
    reviewer_count: int = None,
    focus_dimensions: List[str] = None,
) -> Dict[str, dict]:
    """Select a subset of REVIEWER_ROLES based on count and focus dimensions.

    If reviewer_count is None or >= len(REVIEWER_ROLES), returns all roles.
    If focus_dimensions is provided, ranks roles by relevance and picks top N.
    If no focus_dimensions, takes the first N roles in definition order.
    """
    if reviewer_count is None or reviewer_count >= len(REVIEWER_ROLES):
        return dict(REVIEWER_ROLES)

    reviewer_count = max(1, reviewer_count)

    if focus_dimensions:
        # Score each role by how many focus dimensions it's relevant to
        role_scores: Dict[str, int] = {role: 0 for role in REVIEWER_ROLES}
        for dim in focus_dimensions:
            dim_lower = dim.lower().strip()
            relevant_roles = DIMENSION_TO_ROLES.get(dim_lower, [])
            for role in relevant_roles:
                if role in role_scores:
                    role_scores[role] += 1

        # Sort by score descending, break ties by original order
        role_order = list(REVIEWER_ROLES.keys())
        ranked = sorted(
            role_order,
            key=lambda r: (-role_scores[r], role_order.index(r)),
        )
        selected_names = ranked[:reviewer_count]
    else:
        # No focus dimensions — take first N in definition order
        selected_names = list(REVIEWER_ROLES.keys())[:reviewer_count]

    # Preserve original order for consistency
    return {name: REVIEWER_ROLES[name] for name in REVIEWER_ROLES if name in selected_names}


# ============================================================
# Listwise Comparative Calibration
# ============================================================

def _listwise_calibrate(reviewer_outputs: list, consolidated: dict) -> dict:
    """Apply listwise calibration to dimension scores to combat score clustering.

    When multiple reviewers all give similar scores (std < 1.0) for a dimension,
    this forces differentiation by re-mapping scores relative to the median.

    Args:
        reviewer_outputs: List of dicts, each with a 'scores' key mapping
            dimension names to numeric scores.
        consolidated: Dict with 'dimension_scores' (dim→avg) and 'overall_score'.

    Returns:
        Updated consolidated dict with calibration metadata:
        - 'calibration_applied': bool
        - 'dimensions_calibrated': list of dimension names that were spread
        - Updated 'dimension_scores' and 'overall_score'
    """
    dimension_scores = consolidated.get("dimension_scores", {})
    if not dimension_scores or not reviewer_outputs:
        consolidated["calibration_applied"] = False
        consolidated["dimensions_calibrated"] = []
        return consolidated

    # Collect all dimensions present across reviewers
    all_dimensions = set(dimension_scores.keys())

    # Build score matrix: dimension → list of reviewer scores
    score_matrix: Dict[str, List[float]] = {dim: [] for dim in all_dimensions}
    for reviewer in reviewer_outputs:
        scores = reviewer.get("scores", {})
        for dim in all_dimensions:
            if dim in scores:
                score_matrix[dim].append(float(scores[dim]))

    dimensions_calibrated = []
    calibrated_scores = dict(dimension_scores)  # copy

    for dim, scores_list in score_matrix.items():
        if len(scores_list) < 2:
            continue

        stdev = statistics.stdev(scores_list)
        if stdev < 1.0:
            # Scores are clustered — apply spread based on rank relative to median
            median_val = statistics.median(scores_list)
            n = len(scores_list)
            # Sort scores and assign spread offsets
            sorted_scores = sorted(scores_list)
            # Map: lowest → -1.5, highest → +1.5, linearly interpolate middle
            if n == 1:
                spread_offsets = [0.0]
            else:
                spread_offsets = [
                    -1.5 + (3.0 * i / (n - 1)) for i in range(n)
                ]

            # Compute new calibrated average: median + mean of spread offsets
            # (mean of symmetric offsets = 0, so calibrated = median + weighted shift)
            # More useful: compute calibrated score as median + average offset
            # Since offsets are symmetric around 0, the new mean = median
            # But we want the consolidated dimension score to reflect the spread:
            # Use the original consolidated score + shift based on where it falls
            # relative to the reviewer scores.
            #
            # Simpler approach: recompute the consolidated dimension score as
            # the mean of the re-mapped reviewer scores.
            remapped = []
            for score in scores_list:
                rank_idx = sorted_scores.index(score)
                remapped.append(median_val + spread_offsets[rank_idx])

            new_dim_score = statistics.mean(remapped)
            # Clamp to reasonable bounds (1.0 - 10.0)
            new_dim_score = max(1.0, min(10.0, new_dim_score))
            calibrated_scores[dim] = round(new_dim_score, 2)
            dimensions_calibrated.append(dim)

    # Recompute overall score as mean of calibrated dimension scores
    if dimensions_calibrated and calibrated_scores:
        new_overall = statistics.mean(calibrated_scores.values())
        new_overall = max(1.0, min(10.0, new_overall))
        consolidated["overall_score"] = round(new_overall, 2)

    consolidated["dimension_scores"] = calibrated_scores
    consolidated["calibration_applied"] = len(dimensions_calibrated) > 0
    consolidated["dimensions_calibrated"] = dimensions_calibrated
    return consolidated


def _generate_comparative_prompt(sections_summary: str, dimension_scores: dict) -> str:
    """Generate an LLM prompt for comparative dimension calibration.

    This produces a prompt that asks the LLM to look at all dimension scores
    together and adjust them so the spread between the best and worst dimension
    is at least 2.0 points, forcing meaningful differentiation.

    Args:
        sections_summary: Summary of the paper's sections content.
        dimension_scores: Dict mapping dimension names to current scores.

    Returns:
        A prompt string suitable for sending to an LLM.
    """
    scores_display = "\n".join(
        f"  - {dim}: {score:.1f}/10" for dim, score in dimension_scores.items()
    )

    prompt = f"""You are calibrating review dimension scores for comparative accuracy.

Here is a summary of the paper's sections:
{sections_summary}

Current dimension scores:
{scores_display}

Current spread (max - min): {max(dimension_scores.values()) - min(dimension_scores.values()):.1f} points

TASK: Looking at these scores together, determine which dimensions are relatively 
strongest and weakest for THIS paper. Adjust the scores so that:
1. The spread between the best and worst dimension is at least 2.0 points
2. Relative ordering reflects genuine quality differences between dimensions
3. Scores remain on a 1-10 scale

For each dimension, provide your calibrated score and a one-sentence justification.

Output JSON format:
{{
  "calibrated_scores": {{
    "<dimension>": <float>,
    ...
  }},
  "justifications": {{
    "<dimension>": "<one sentence>",
    ...
  }},
  "overall_score": <float>
}}"""

    return prompt


# ============================================================
# Main Pipeline
# ============================================================

async def review_paper(
    provider: str = None,
    model: str = None,
    reviewer_count: int = None,
    focus_dimensions: List[str] = None,
    custom_criteria: str = None,
    calibrate_scores: Union[bool, str] = True,
) -> str:
    """Run full multi-role review with hybrid pipeline.

    Pipeline:
    1. Run N reviewers in parallel (LLM) — N defaults to all 5, configurable via reviewer_count
    2. LLM consolidation (merge + route)
    3. Listwise calibration (optional, combats score clustering)
    4. Post-processing (rule-based):
       a. Convert to ReviewIssue objects
       b. Verify quotes against paper text
       c. Rule-based deduplication (3-pass)
       d. Deterministic consensus scoring
       e. Flag unverified quotes

    Args:
        provider: LLM provider override.
        model: Model name override.
        reviewer_count: If provided, select only this many reviewers (most relevant
            based on focus_dimensions, or first N if no focus specified).
        focus_dimensions: e.g. ["clarity", "methodology", "novelty"]. If provided,
            adds a focus instruction to each reviewer's prompt.
        custom_criteria: Free-form additional criteria for reviewers to consider.
        calibrate_scores: Controls listwise calibration to combat score clustering.
            True = apply statistical calibration (free, no LLM calls).
            "llm" = apply LLM-based comparative calibration (1 extra LLM call).
            False = no calibration (legacy behavior).

    Returns formatted summary for agent context.
    """
    index = _load_index()
    if not index:
        return "Error: No paper parsed. Use parse_paper first."

    # ── Checkpoint setup ──
    from utils.checkpoint import Checkpoint
    _metadata_for_cp = _load_metadata() or {}
    cp = Checkpoint("review_paper", paper_id=str(_metadata_for_cp.get("source_file", "unknown")))
    state = cp.start(total_steps_estimate=4)  # focus, reviewers, consolidate, postprocess

    try:
        return await _review_paper_inner(
            cp, state, index, provider=provider, model=model,
            reviewer_count=reviewer_count, focus_dimensions=focus_dimensions,
            custom_criteria=custom_criteria, calibrate_scores=calibrate_scores,
        )
    except Exception as e:
        cp.mark_failed(str(e))
        raise


async def _review_paper_inner(
    cp, state, index,
    provider=None, model=None, reviewer_count=None,
    focus_dimensions=None, custom_criteria=None, calibrate_scores=True,
):
    """Inner pipeline, separated for checkpoint wrapping."""

    # ── Stage 0: Recall memory context ──
    # Load metadata for source_file (index is a list, not dict)
    _memory_context = ""
    _metadata = _load_metadata()
    try:
        from utils.memory.integration import recall_paper_context, recall_field_patterns, get_paper_id
        _source_file = _metadata.get("source_file", "") if _metadata else ""
        _paper_id = get_paper_id(_source_file)
        _mem = recall_paper_context(_paper_id)
        if _mem:
            _memory_context = _mem
        # Also check field-level patterns if metadata has field info
        _field = _metadata.get("field", "") if _metadata else ""
        if _field:
            _patterns = recall_field_patterns(_field, limit=3)
            if _patterns:
                _memory_context += f"\n   Common {_field} issues: {'; '.join(_patterns[:3])}"
    except Exception:
        pass  # Non-fatal

    import os
    max_conc = int(os.environ.get("SCHOLAR_MAX_CONCURRENT", "2"))
    client = LLMClient(model=model, max_concurrent=max_conc, provider=provider)

    # ── Stage 0.5: Select reviewer subset based on parameters ──
    selected_roles = _select_reviewers(
        reviewer_count=reviewer_count,
        focus_dimensions=focus_dimensions,
    )

    # ── Stage 0.7: Generate Focus Points (non-fatal) ──
    reviewer_injection: Dict[str, str] = {}
    if state.completed_step < 0:
        cp.begin_step(0, "focus_generation")
        try:
            meta_path = WORKSPACE / "paper" / "metadata.json"
            if meta_path.exists():
                focus_metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            else:
                focus_metadata = _metadata or {}

            # Build section_summaries from the first ~500 chars of key sections
            section_summaries: Dict[str, str] = {}
            for target_section in ("abstract", "introduction", "methodology", "results"):
                for entry in index:
                    slug = entry.get("slug", "").lower()
                    title_lower = entry.get("title", "").lower()
                    if target_section in slug or target_section in title_lower:
                        sec_path = Path(entry["file"])
                        if sec_path.exists():
                            section_summaries[target_section] = sec_path.read_text(encoding="utf-8")[:500]
                        break

            focus_result = generate_focus_points(focus_metadata, section_summaries)
            reviewer_injection = focus_result.get("reviewer_injection", {})
        except Exception:
            pass  # Non-fatal: continue without focus injection
        cp.complete_step(0, "focus_generation", "Generated focus points")

    # ── Stage 1: Run all reviewers (concurrency controlled by semaphore) ──
    review_dir = WORKSPACE / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    pipeline_warnings: List[str] = []

    if state.completed_step < 1:
        cp.begin_step(1, "reviewers")
        tasks = []
        for role_name, role_config in selected_roles.items():
            role_criteria = custom_criteria or ""
            injection = reviewer_injection.get(role_name, "")
            if injection:
                role_criteria = injection + ("\n\n" + role_criteria if role_criteria else "")
            tasks.append(_run_reviewer(
                client, role_name, role_config, index,
                focus_dimensions=focus_dimensions,
                custom_criteria=role_criteria or None,
            ))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_reviewer_outputs = {}
        error_count = 0
        for role_name, result in zip(selected_roles.keys(), results):
            if isinstance(result, Exception):
                all_reviewer_outputs[role_name] = f"Error: {result}"
                error_count += 1
                pipeline_warnings.append(
                    f"Reviewer '{role_name}' failed: {result}. "
                    f"Results are from {len(selected_roles) - error_count}/{len(selected_roles)} reviewers."
                )
            else:
                all_reviewer_outputs[role_name] = result

        # Save individual reviewer outputs
        for role_name, issues in all_reviewer_outputs.items():
            (review_dir / f"reviewer_{role_name}.json").write_text(
                json.dumps(issues, indent=2, ensure_ascii=False) if isinstance(issues, list)
                else json.dumps({"error": str(issues)}),
                encoding="utf-8"
            )
        cp.complete_step(1, "reviewers", f"{len(selected_roles)} reviewers completed",
                         data_update={"reviewer_outputs_saved": True},
                         llm_calls=len(selected_roles))
    else:
        # Resume: load saved reviewer outputs
        all_reviewer_outputs = {}
        for role_name in selected_roles:
            rpath = review_dir / f"reviewer_{role_name}.json"
            if rpath.exists():
                all_reviewer_outputs[role_name] = json.loads(rpath.read_text(encoding="utf-8"))

    # ── Stage 2: LLM Consolidation ──
    if state.completed_step < 2:
        cp.begin_step(2, "consolidation")
        consolidated = await _consolidate(client, all_reviewer_outputs, memory_context=_memory_context)

        if consolidated.get("parse_error"):
            pipeline_warnings.append(
                "LLM consolidation output was unparseable. Using raw reviewer outputs only."
            )

        # Save LLM consolidated output (raw)
        (review_dir / "consolidated_raw.json").write_text(
            json.dumps(consolidated, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        cp.complete_step(2, "consolidation", "LLM consolidation done", llm_calls=1)
    else:
        # Resume: load saved consolidation
        consolidated = json.loads((review_dir / "consolidated_raw.json").read_text(encoding="utf-8"))

    # ── Stage 2.5: Listwise Calibration (combat score clustering) ──
    if calibrate_scores:
        # Build reviewer_outputs in the format _listwise_calibrate expects
        _reviewer_score_list = []
        # Map reviewer roles to their primary scoring dimensions
        ROLE_TO_DIMENSIONS = {
            "editor": ["structure", "presentation"],
            "methodology": ["methodology", "rigor"],
            "theory": ["novelty", "contribution"],
            "logic": ["coherence", "argumentation"],
            "literature": ["coverage", "gap_authenticity"],
        }
        for role_name, output in all_reviewer_outputs.items():
            if isinstance(output, list) and output:
                # Compute implied score from issue severity distribution
                n_major = sum(1 for iss in output if isinstance(iss, dict) and iss.get("severity") == "major")
                n_moderate = sum(1 for iss in output if isinstance(iss, dict) and iss.get("severity") == "moderate")
                n_minor = sum(1 for iss in output if isinstance(iss, dict) and iss.get("severity") == "minor")
                implied_score = max(1.0, 10.0 - (n_major * 1.5 + n_moderate * 0.7 + n_minor * 0.2))

                # Assign this score to the dimensions this reviewer covers
                role_scores = {}
                for dim in ROLE_TO_DIMENSIONS.get(role_name, ["general"]):
                    role_scores[dim] = implied_score
                _reviewer_score_list.append({"scores": role_scores, "role": role_name})

        # If consolidated has dimension_scores, apply calibration
        if consolidated.get("dimension_scores") or consolidated.get("overall_score"):
            # Ensure dimension_scores exists for calibration
            if not consolidated.get("dimension_scores"):
                # Create dimension_scores from quality_gate-style dimensions if not present
                consolidated.setdefault("dimension_scores", {})

            if calibrate_scores == "llm":
                # LLM-based comparative calibration (expensive: 1 extra call)
                sections_summary = _gather_sections(
                    ["abstract", "introduction", "methodology", "results"],
                    index,
                )[:2000]  # Truncate for prompt
                dim_scores = consolidated.get("dimension_scores", {})
                if dim_scores:
                    comp_prompt = _generate_comparative_prompt(sections_summary, dim_scores)
                    try:
                        llm_response = await client.chat(
                            system="You are a calibration assistant. Output valid JSON only.",
                            user=comp_prompt,
                            max_tokens=1500,
                            temperature=0.0,
                            model=get_model_for_task("consolidate_review"),
                        )
                        llm_cal = robust_json_parse(llm_response)
                        if not llm_cal["is_fallback"] and isinstance(llm_cal["data"], dict):
                            cal_data = llm_cal["data"]
                            if "calibrated_scores" in cal_data:
                                consolidated["dimension_scores"] = {
                                    k: round(float(v), 2)
                                    for k, v in cal_data["calibrated_scores"].items()
                                }
                                if "overall_score" in cal_data:
                                    consolidated["overall_score"] = round(float(cal_data["overall_score"]), 2)
                                consolidated["calibration_applied"] = True
                                consolidated["calibration_method"] = "llm"
                                consolidated["dimensions_calibrated"] = list(
                                    cal_data["calibrated_scores"].keys()
                                )
                    except Exception:
                        # Fallback to statistical calibration on LLM failure
                        consolidated = _listwise_calibrate(_reviewer_score_list, consolidated)
                        consolidated["calibration_method"] = "statistical_fallback"
            else:
                # Statistical calibration (free, no LLM)
                consolidated = _listwise_calibrate(_reviewer_score_list, consolidated)
                if consolidated.get("calibration_applied"):
                    consolidated["calibration_method"] = "statistical"

    # ── Stage 3: Rule-based Post-processing ──
    cp.begin_step(3, "postprocess")
    llm_issues = consolidated.get("issues", [])

    # 3a: Convert to ReviewIssue objects
    structured_issues: List[ReviewIssue] = []
    for issue_dict in llm_issues:
        reviewer_role = issue_dict.get("reviewer", "")
        structured_issue = ReviewIssue.from_llm_issue(issue_dict, reviewer_role)
        structured_issues.append(structured_issue)

    # 3b: Verify quotes against paper text
    paper_text = _load_full_paper_text(index)
    if paper_text and structured_issues:
        verify_quotes(structured_issues, paper_text)

    # 3c: Rule-based deduplication
    deduped_issues = consolidate_issues(structured_issues)

    # 3d: Deterministic consensus scoring
    desk_reject = any(
        role_name == "editor" and isinstance(output, list) and
        any(i.get("severity") == "major" for i in output if isinstance(i, dict))
        for role_name, output in all_reviewer_outputs.items()
    )
    scoring = calculate_consensus_score(deduped_issues, desk_reject=desk_reject)

    # 3e: Build final consolidated output
    unverified_count = sum(1 for i in deduped_issues if not i.quote_verified and i.quote)
    final_issues = [i.to_dict() for i in deduped_issues]

    # Merge LLM action_type info back into structured issues
    _merge_action_types(final_issues, llm_issues)

    final_consolidated = {
        "overall_score": scoring["score"],
        "verdict": scoring["verdict"],
        "total_issues": scoring["breakdown"],
        "action_summary": consolidated.get("action_summary", {}),
        "issues": final_issues,
        "revision_roadmap": consolidated.get("revision_roadmap", []),
        "strengths": consolidated.get("strengths", []),
        "gate_blockers": scoring["gate_blockers"],
        "quote_verification": {
            "total_with_quotes": sum(1 for i in deduped_issues if i.quote),
            "verified": sum(1 for i in deduped_issues if i.quote_verified),
            "unverified": unverified_count,
        },
        "dedup_stats": {
            "raw_from_llm": len(llm_issues),
            "after_dedup": len(deduped_issues),
            "removed": len(llm_issues) - len(deduped_issues),
        },
    }

    # ── Stage 4: Quality Gate ──
    # Evaluate whether the review itself is thorough enough
    from tools.quality_gate import ReviewIssueProxy, evaluate_review_quality

    gate_proxies = [
        ReviewIssueProxy(
            title=i.title,
            quote=i.quote,
            explanation=i.explanation,
            comment_type=i.comment_type,
            severity=i.severity,
            confidence=i.confidence,
            source_section=i.source_section,
            gate_blocker=i.gate_blocker,
            suggestion=i.suggestion,
            quote_verified=i.quote_verified,
        )
        for i in deduped_issues
    ]

    paper_sections = [entry.get("section_id", entry.get("title", ""))
                      for entry in index]
    gate_verdict = evaluate_review_quality(gate_proxies, paper_sections)

    final_consolidated["quality_gate"] = {
        "passed": gate_verdict.passed,
        "composite_score": round(gate_verdict.composite_score, 3),
        "recommendation": gate_verdict.recommendation,
        "must_fix_count": gate_verdict.must_fix_count,
        "dimension_scores": {k: round(v, 3) for k, v in gate_verdict.dimension_scores.items()},
        "weak_dimensions": gate_verdict.weak_dimensions,
        "deepening_needed": not gate_verdict.passed,
    }

    # ── Stage 5: Score Tracking ──
    # Record this review's score for trajectory analysis
    import time as _time
    from utils.score_tracker import ScoreSnapshot, record_score

    snapshot = ScoreSnapshot(
        timestamp=_time.strftime("%Y-%m-%dT%H:%M:%S"),
        overall_score=scoring["score"],
        dimension_scores=gate_verdict.dimension_scores,
        issues_remaining=len(deduped_issues),
        must_fix_remaining=gate_verdict.must_fix_count,
        trigger="review_paper",
    )
    record_score(snapshot)

    # Save final output
    (review_dir / "consolidated.json").write_text(
        json.dumps(final_consolidated, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (review_dir / "issues.json").write_text(
        json.dumps(final_consolidated.get("issues", []), indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # ── Stage 6: Memory persistence ──
    # Record review results for cross-session continuity
    try:
        from utils.memory.integration import remember_review, get_paper_id
        _source_file = _metadata.get("source_file", "") if _metadata else ""
        _paper_id = get_paper_id(_source_file)
        # Derive title from first section title or source filename
        _title = index[0]["title"] if index else Path(_source_file).stem
        _field = _metadata.get("field", "") if _metadata else ""
        remember_review(
            paper_id=_paper_id,
            title=_title,
            field=_field,
            issues=final_consolidated.get("issues", []),
            strengths=final_consolidated.get("strengths", []),
            overall_score=scoring["score"],
            verdict=scoring["verdict"],
        )
    except Exception:
        pass  # Non-fatal

    # Return compact summary for agent context
    cp.complete_step(3, "postprocess", "Post-processing complete")
    cp.clear()
    summary = _format_review_summary(final_consolidated, client.stats(), warnings=pipeline_warnings)
    return summary


async def _run_reviewer(
    client: LLMClient,
    role_name: str,
    role_config: dict,
    index: list,
    focus_dimensions: List[str] = None,
    custom_criteria: str = None,
) -> list:
    """Run a single reviewer in isolated context. Returns list of issues."""
    # Gather relevant sections for this reviewer
    sections_text = _gather_sections(role_config["reads"], index)
    if not sections_text:
        return []

    system = REVIEW_SYSTEM_PROMPT.format(role=role_name, focus=role_config["focus"])

    # Inject focus dimensions if provided
    if focus_dimensions:
        system += f"\n\nFOCUS PRIORITY: Pay special attention to these aspects: {', '.join(focus_dimensions)}"

    # Inject custom criteria if provided
    if custom_criteria:
        system += f"\n\nADDITIONAL CRITERIA: {custom_criteria}"

    user = f"Review the following paper sections:\n\n{sections_text}"

    response = await client.chat(system=system, user=user, max_tokens=3000, temperature=0.1,
                                 model=get_model_for_task("review_paper"))

    # Parse JSON from response using robust 4-layer parser
    parsed = robust_json_parse(response)
    
    if not parsed["is_fallback"] and isinstance(parsed["data"], list):
        issues = parsed["data"]
        # Tag each issue with reviewer role
        for issue in issues:
            if isinstance(issue, dict):
                issue["reviewer"] = role_name
        return issues
    elif not parsed["is_fallback"] and isinstance(parsed["data"], dict):
        # Some LLMs wrap issues in {"issues": [...]}
        issues = parsed["data"].get("issues", [parsed["data"]])
        for issue in issues:
            if isinstance(issue, dict):
                issue["reviewer"] = role_name
        return issues

    return [{"severity": "note", "category": "parse_error",
             "description": f"Could not parse {role_name} output (layer {parsed['layer']})",
             "raw": response[:500]}]


async def _consolidate(client: LLMClient, all_issues: dict, memory_context: str = "") -> dict:
    """Consolidate all reviewer outputs into a single assessment via LLM."""
    reviewer_summary = json.dumps(all_issues, indent=2, ensure_ascii=False)

    user_content = f"Reviewer outputs:\n\n{reviewer_summary}"
    if memory_context:
        user_content += f"\n\n--- Previous Review Context ---\n{memory_context}"

    response = await client.chat(
        system=CONSOLIDATION_PROMPT,
        user=user_content,
        max_tokens=8000,
        temperature=0.0,
        model=get_model_for_task("consolidate_review"),
    )

    parsed = robust_json_parse(
        response,
        expected_keys=["overall_score", "verdict", "issues"],
    )
    
    if not parsed["is_fallback"] and isinstance(parsed["data"], dict):
        return parsed["data"]
    
    # Fallback: return error structure with whatever was extracted
    fallback_data = parsed["data"] if isinstance(parsed["data"], dict) else {}
    return {
        "overall_score": fallback_data.get("overall_score", 0),
        "verdict": fallback_data.get("verdict", "error"),
        "parse_error": True,
        "raw_response": response[:2000],
        "issues": fallback_data.get("issues", []),
    }


# ============================================================
# Helpers
# ============================================================

def _merge_action_types(final_issues: List[Dict], llm_issues: List[Dict]) -> None:
    """Merge action_type/fix_complexity from LLM consolidation into final issues.

    Since rule-based dedup may reorder/merge, we match by title similarity.
    Falls back to default action_type="confirm_fix" if no match found.
    """
    for final in final_issues:
        title = final.get("title", "").lower()
        best_match = None
        best_score = 0.0

        for llm_issue in llm_issues:
            llm_title = (llm_issue.get("title", "") or llm_issue.get("category", "")).lower()
            if not llm_title:
                continue
            # Simple word overlap
            words_f = set(title.split())
            words_l = set(llm_title.split())
            if not words_f or not words_l:
                continue
            score = len(words_f & words_l) / max(len(words_f | words_l), 1)
            if score > best_score:
                best_score = score
                best_match = llm_issue

        if best_match and best_score > 0.3:
            final.setdefault("action_type", best_match.get("action_type", "confirm_fix"))
            final.setdefault("action_rationale", best_match.get("action_rationale", ""))
            final.setdefault("fix_complexity", best_match.get("fix_complexity", "paragraph_level"))
            final.setdefault("id", best_match.get("id", ""))
        else:
            final.setdefault("action_type", "confirm_fix")
            final.setdefault("action_rationale", "No matching LLM classification found")
            final.setdefault("fix_complexity", "paragraph_level")

    # Ensure all issues have sequential IDs
    for idx, issue in enumerate(final_issues):
        if not issue.get("id"):
            issue["id"] = f"ISS-{idx + 1:03d}"


def _gather_sections(target_slugs: List[str], index: list) -> str:
    """Load sections matching target slugs. Fuzzy match on slug/title."""
    texts = []
    for entry in index:
        slug = entry["slug"].lower()
        title = entry["title"].lower()
        for target in target_slugs:
            target_lower = target.lower()
            if target_lower in slug or target_lower in title:
                sec_path = Path(entry["file"])
                if sec_path.exists():
                    content = sec_path.read_text(encoding="utf-8")
                    texts.append(f"=== {entry['title']} ===\n{content}")
                break
    return "\n\n".join(texts)


def _load_index() -> list:
    index_path = WORKSPACE / "paper" / "section_index.json"
    if not index_path.exists():
        return []
    return json.loads(index_path.read_text(encoding="utf-8"))


def _load_metadata() -> Optional[Dict]:
    """Load metadata.json for paper-level info (source_file, format, etc.)."""
    meta_path = WORKSPACE / "paper" / "metadata.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_full_paper_text(index: list) -> str:
    """Load full paper text for quote verification."""
    parts = []
    for entry in index:
        sec_path = Path(entry["file"])
        if sec_path.exists():
            parts.append(sec_path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def _format_review_summary(consolidated: dict, stats: dict, warnings: List[str] = None) -> str:
    """Format review results for display. Compact but informative."""
    lines = []

    # Surface warnings to the Agent first
    if warnings:
        lines.append("⚠️ WARNINGS (Agent: consider these for decision-making):")
        for w in warnings:
            lines.append(f"  - {w}")
        lines.append("")

    lines.append("=" * 60)
    lines.append("REVIEW COMPLETE")
    lines.append("=" * 60)

    score = consolidated.get("overall_score", "N/A")
    verdict = consolidated.get("verdict", "N/A")
    totals = consolidated.get("total_issues", {})
    actions = consolidated.get("action_summary", {})

    lines.append(f"\nScore: {score}/10 | Verdict: {verdict}")
    lines.append(f"Issues: {totals.get('major', 0)} major, {totals.get('moderate', 0)} moderate, {totals.get('minor', 0)} minor")
    if actions:
        lines.append(f"Actions: {actions.get('auto_fix', 0)} auto_fix, "
                     f"{actions.get('confirm_fix', 0)} confirm_fix, "
                     f"{actions.get('guidance', 0)} guidance")

    # Show consensus scoring details if available
    overall_score = consolidated.get("overall_score")
    if overall_score is not None:
        lines.append(f"\nConsensus Score: {overall_score}/9.0 | "
                     f"Verdict: {consolidated.get('verdict', 'N/A')}")
        gate_blockers = consolidated.get("gate_blockers", [])
        if gate_blockers:
            lines.append(f"\u26d4 Gate Blockers: {'; '.join(gate_blockers)}")

    # Quality Gate results
    gate = consolidated.get("quality_gate")
    if gate:
        gate_icon = "✅" if gate.get("passed") else "⚠️"
        lines.append(f"\n{gate_icon} Quality Gate: {gate.get('recommendation', '?').upper()} "
                     f"(composite: {gate.get('composite_score', 0):.2f})")
        weak = gate.get("weak_dimensions", [])
        if weak:
            lines.append(f"  Weak dimensions: {', '.join(weak)}")
        if gate.get("deepening_needed"):
            lines.append(f"  → Review deepening needed before proceeding to fixes.")

    # Quote verification stats
    quote_stats = consolidated.get("quote_verification")
    if quote_stats:
        lines.append(f"\nQuote Verification: {quote_stats.get('verified', 0)}/{quote_stats.get('total', 0)} quotes confirmed in paper text")
        unverified = quote_stats.get("unverified_issues", [])
        if unverified:
            lines.append(f"  \u26a0 Unverified quotes in: {', '.join(unverified[:5])}")

    # Deduplication stats
    dedup = consolidated.get("dedup_stats")
    if dedup:
        lines.append(f"\nDeduplication: {dedup.get('raw_from_llm', 0)} \u2192 {dedup.get('after_dedup', 0)} "
                     f"({dedup.get('removed', 0)} duplicates merged)")

    strengths = consolidated.get("strengths", [])
    if strengths:
        lines.append(f"\nStrengths: {'; '.join(strengths[:3])}")

    issues = consolidated.get("issues", [])
    if issues:
        lines.append("\nTop Issues:")
        for i, issue in enumerate(issues[:5]):
            sev = issue.get("severity", "?")
            cat = issue.get("category", "")
            desc = issue.get("description", "")[:100]
            verified = "\u2713" if issue.get("quote_verified") else "?"
            lines.append(f"  [{sev.upper()}][{verified}] {cat}: {desc}")
        if len(issues) > 5:
            lines.append(f"  ... and {len(issues) - 5} more (see .workspace/review/consolidated.json)")

    roadmap = consolidated.get("revision_roadmap", [])
    if roadmap:
        lines.append("\nRevision Roadmap (priority order):")
        for i, item in enumerate(roadmap[:5]):
            if isinstance(item, str):
                lines.append(f"  {i+1}. {item}")
            elif isinstance(item, dict):
                lines.append(f"  {i+1}. {item.get('action', item)}")

    lines.append(f"\n[LLM Stats: {stats['total_calls']} calls, "
                 f"{stats['total_input_tokens']} in / {stats['total_output_tokens']} out tokens, "
                 f"~${stats['estimated_cost_usd']}]")

    return "\n".join(lines)
