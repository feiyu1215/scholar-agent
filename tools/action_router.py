"""
tools/action_router.py — Issue Action Router with Red Line enforcement.

Takes consolidated review issues (each with action_type from LLM classification)
and applies hard-coded Red Line checks + budget-mode downgrading.

Design choices:
- Red Lines are CODE-ENFORCED, never delegated to model judgment
- Budget mode acts as a "ceiling" — can only downgrade action_type, never upgrade
- First-of-type validation: first auto_fix in a category requires confirm
- Output: routed issues ready for execution by the revision loop
"""

from __future__ import annotations

import re
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict

WORKSPACE = Path(".workspace")

# Budget mode ceiling: maps (original_action_type, budget) → effective_action_type
BUDGET_CEILING = {
    ("auto_fix", "full"): "auto_fix",
    ("auto_fix", "medium"): "auto_fix",
    ("auto_fix", "minimal"): "guidance",
    ("confirm_fix", "full"): "confirm_fix",
    ("confirm_fix", "medium"): "guidance",
    ("confirm_fix", "minimal"): "guidance",
    ("guidance", "full"): "guidance",
    ("guidance", "medium"): "guidance",
    ("guidance", "minimal"): "guidance",
}

# Patterns that indicate thesis-level content (for Red Line 1 enforcement)
THESIS_PATTERNS = [
    r"(?i)(this paper argues|we argue|our (main |core |central )?contribution)",
    r"(?i)(this study (shows|demonstrates|proves|establishes))",
    r"(?i)(the (key|main|central|primary) (finding|result|contribution))",
    r"(?i)(we (propose|introduce|present) a (new|novel))",
    r"(?i)(causal (effect|impact|relationship|link|mechanism))",
]

# Patterns that indicate fabricated content (for Red Line 2 enforcement)
CITE_PATTERN = re.compile(r"\\cite\{[^}]+\}")
NUMBER_PATTERN = re.compile(r"\b\d+\.?\d*%|\bp\s*[<>=]\s*\d|\bN\s*=\s*\d")


@dataclass
class RoutedIssue:
    """An issue after routing: has effective action_type + routing metadata."""
    id: str
    severity: str
    category: str
    location: Dict
    description: str
    suggestion: str
    action_type: str              # Original from consolidation LLM
    effective_action: str         # After Red Line + budget adjustment
    action_rationale: str
    fix_complexity: str
    routing_notes: List[str] = field(default_factory=list)
    needs_statistical_verification: bool = False
    first_of_type: bool = False   # First auto_fix in its category

    def to_dict(self) -> dict:
        return asdict(self)


def route_issues(
    issues: List[Dict],
    budget: str = "full",
    seen_categories: Optional[set] = None,
) -> Tuple[List[RoutedIssue], Dict]:
    """
    Route consolidated issues through Red Line checks and budget ceiling.
    
    Args:
        issues: List of issue dicts from consolidation (with action_type)
        budget: "full" | "medium" | "minimal"
        seen_categories: Categories that have been previously auto_fixed 
                        (for first-of-type tracking). Modified in-place.
    
    Returns:
        (routed_issues, routing_stats)
    """
    if seen_categories is None:
        seen_categories = set()

    routed = []
    stats = {"red_line_downgrades": 0, "budget_downgrades": 0, 
             "first_of_type_confirms": 0, "total": len(issues)}

    for issue in issues:
        routed_issue = _route_single_issue(issue, budget, seen_categories, stats)
        routed.append(routed_issue)

    # Compute action summary
    stats["action_counts"] = {
        "auto_fix": sum(1 for r in routed if r.effective_action == "auto_fix"),
        "confirm_fix": sum(1 for r in routed if r.effective_action == "confirm_fix"),
        "guidance": sum(1 for r in routed if r.effective_action == "guidance"),
    }

    return routed, stats


def _route_single_issue(
    issue: Dict, budget: str, seen_categories: set, stats: Dict
) -> RoutedIssue:
    """Route a single issue. Applies checks in order: Red Lines → First-of-type → Budget."""
    
    original_action = issue.get("action_type", "guidance")
    effective = original_action
    notes = []

    # --- Red Line 1: Never modify core thesis ---
    if _touches_thesis(issue) and effective in ("auto_fix", "confirm_fix"):
        effective = "guidance"
        notes.append("RED_LINE_1: touches thesis/causal claim → forced guidance")
        stats["red_line_downgrades"] += 1

    # --- Red Line 2: Flag potential fabrication risk ---
    # (This is a pre-check; actual enforcement happens post-rewrite in write_engine)
    if _might_introduce_new_claims(issue):
        if effective == "auto_fix":
            effective = "confirm_fix"
            notes.append("RED_LINE_2_PRECAUTION: fix might introduce new claims → confirm_fix")
            stats["red_line_downgrades"] += 1

    # --- First-of-type validation ---
    category = issue.get("category", "unknown")
    if effective == "auto_fix" and category not in seen_categories:
        effective = "confirm_fix"
        notes.append(f"FIRST_OF_TYPE: first auto_fix in category '{category}' → confirm for validation")
        stats["first_of_type_confirms"] += 1
        # Don't add to seen_categories yet — only add after user confirms
    elif effective == "auto_fix" and category in seen_categories:
        notes.append(f"CATEGORY_SEEN: '{category}' previously confirmed → auto_fix allowed")

    # --- Budget ceiling ---
    budget_effective = BUDGET_CEILING.get((effective, budget), "guidance")
    if budget_effective != effective:
        notes.append(f"BUDGET_{budget.upper()}: {effective} → {budget_effective}")
        effective = budget_effective
        stats["budget_downgrades"] += 1

    # --- Statistical verification flag ---
    needs_stata = _needs_statistical_verification(issue)

    return RoutedIssue(
        id=issue.get("id", "ISS-???"),
        severity=issue.get("severity", "minor"),
        category=category,
        location=issue.get("location", {}),
        description=issue.get("description", ""),
        suggestion=issue.get("suggestion", ""),
        action_type=original_action,
        effective_action=effective,
        action_rationale=issue.get("action_rationale", ""),
        fix_complexity=issue.get("fix_complexity", "sentence_level"),
        routing_notes=notes,
        needs_statistical_verification=needs_stata,
        first_of_type=(effective == "confirm_fix" and 
                       f"FIRST_OF_TYPE" in " ".join(notes)),
    )


def _touches_thesis(issue: Dict) -> bool:
    """Check if issue location/description involves core thesis content."""
    location = issue.get("location", {})
    section_id = str(location.get("section_id", "")).lower()
    quote = str(location.get("quote", "")).lower()
    description = issue.get("description", "").lower()
    suggestion = issue.get("suggestion", "").lower()

    # Check if in thesis-bearing sections
    thesis_sections = {"abstract", "introduction", "01_abstract", "02_introduction"}
    in_thesis_section = any(ts in section_id for ts in thesis_sections)

    if not in_thesis_section:
        return False

    # Check if the content matches thesis patterns
    combined = f"{quote} {description} {suggestion}"
    for pattern in THESIS_PATTERNS:
        if re.search(pattern, combined):
            return True

    # Check if suggestion involves changing causal direction
    causal_change_hints = [
        "change from causal to correlational",
        "reframe the relationship",
        "reverse the direction",
        "remove the causal claim",
        "weaken to association",
    ]
    for hint in causal_change_hints:
        if hint in combined:
            return True

    return False


def _might_introduce_new_claims(issue: Dict) -> bool:
    """Check if fixing this issue might introduce new citations/data."""
    suggestion = issue.get("suggestion", "")
    category = issue.get("category", "").lower()

    # Categories that inherently need new information
    risky_categories = {"missing_reference", "insufficient_evidence", 
                       "data_gap", "missing_citation"}
    if category in risky_categories:
        return True

    # Check if suggestion implies adding new references/data
    add_patterns = [
        r"(?i)(add|cite|include|reference|mention)\s+(a |the |more |additional )?("
        r"study|paper|work|finding|data|evidence|result|source)",
        r"(?i)(provide|show|present)\s+(additional |more |supporting )?("
        r"evidence|data|statistics|numbers)",
    ]
    for pattern in add_patterns:
        if re.search(pattern, suggestion):
            return True

    return False


def _needs_statistical_verification(issue: Dict) -> bool:
    """Check if this issue would benefit from Stata/R verification."""
    category = issue.get("category", "").lower()
    description = issue.get("description", "").lower()

    stat_categories = {
        "sample_size", "statistical_test", "regression_specification",
        "robustness", "power_analysis", "endogeneity", "selection_bias",
        "heteroskedasticity", "multicollinearity",
    }
    if category in stat_categories:
        return True

    stat_keywords = [
        "sample size", "statistical significance", "p-value", "regression",
        "robustness check", "power analysis", "standard error", "confidence interval",
        "heteroskedast", "endogene", "instrument variable", "diff-in-diff",
    ]
    return any(kw in description for kw in stat_keywords)


def mark_category_confirmed(seen_categories: set, category: str) -> None:
    """Mark a category as confirmed by user (enables future auto_fix for same category)."""
    seen_categories.add(category)


def format_routing_report(routed: List[RoutedIssue], stats: Dict) -> str:
    """Format a human-readable routing report."""
    lines = []
    lines.append("=" * 60)
    lines.append("ISSUE ROUTING REPORT")
    lines.append("=" * 60)
    
    counts = stats["action_counts"]
    lines.append(f"\nTotal: {stats['total']} issues")
    lines.append(f"  → auto_fix: {counts['auto_fix']}")
    lines.append(f"  → confirm_fix: {counts['confirm_fix']}")
    lines.append(f"  → guidance: {counts['guidance']}")
    
    if stats["red_line_downgrades"]:
        lines.append(f"\n⚠ Red Line downgrades: {stats['red_line_downgrades']}")
    if stats["first_of_type_confirms"]:
        lines.append(f"⚡ First-of-type confirms: {stats['first_of_type_confirms']}")
    if stats["budget_downgrades"]:
        lines.append(f"💰 Budget downgrades: {stats['budget_downgrades']}")

    lines.append("\n" + "-" * 60)
    for r in routed:
        action_display = r.effective_action
        if r.effective_action != r.action_type:
            action_display = f"{r.action_type} → {r.effective_action}"
        
        lines.append(f"\n[{r.id}] [{r.severity.upper()}] {r.category}")
        lines.append(f"  Action: {action_display}")
        lines.append(f"  {r.description[:100]}")
        if r.routing_notes:
            for note in r.routing_notes:
                lines.append(f"    ⤷ {note}")
        if r.needs_statistical_verification:
            lines.append(f"    📊 Needs statistical verification (Stata)")

    return "\n".join(lines)
