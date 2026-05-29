"""
tools/action_router.py — Issue Action Router with Red Line enforcement.

Takes consolidated review issues (each with action_type from LLM classification)
and applies hard-coded Red Line checks + budget-mode downgrading.

Design choices:
- Red Lines are CODE-ENFORCED, never delegated to model judgment
- Budget mode acts as a "ceiling" — can only downgrade action_type, never upgrade
- First-of-type validation: first auto_fix in a category requires confirm
- Decision observability: each routing decision produces a structured trace
  explaining WHY this path was chosen (not just what happened)
- Output: routed issues ready for execution by the revision loop
"""

from __future__ import annotations

import re
import json
import time
from pathlib import Path
from typing import Any, List, Dict, Optional, Tuple, Union
from dataclasses import dataclass, field, asdict

from core.tool_metadata import get_tool_meta, assess_risk_level

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
# These MUST be anchored to first-person or demonstrative references to THIS paper
# to avoid false positives when quoting OTHER papers (e.g. "Smith et al. argue...")
THESIS_PATTERNS = [
    r"(?i)\b(this paper argues|we argue that|our (main |core |central )?contribution)",
    r"(?i)\b(this study (shows|demonstrates|proves|establishes) that)",
    r"(?i)\b(the (key|main|central|primary) (finding|result|contribution) of this)",
    r"(?i)\b(we (propose|introduce|present) a (new |novel ))",
    r"(?i)\b(our (causal |main )?finding|the causal (effect|mechanism) we identify)",
]

# Patterns that indicate fabricated content (for Red Line 2 enforcement)
CITE_PATTERN = re.compile(r"\\cite\{[^}]+\}")
NUMBER_PATTERN = re.compile(r"\b\d+\.?\d*%|\bp\s*[<>=]\s*\d|\bN\s*=\s*\d")


@dataclass
class DecisionTrace:
    """Structured trace of a single routing decision — the 'bid explanation'.
    
    Records not just what was decided, but WHY alternatives were rejected.
    Designed for observability: can be serialized to JSONL for post-hoc analysis.
    """
    issue_id: str
    timestamp: float
    original_action: str
    final_action: str
    checks_applied: List[Dict[str, Any]]  # Each: {"check": str, "triggered": bool, "reason": str}
    risk_factors: Dict[str, Any]            # {"meta_risk": str, "touches_thesis": bool, ...}
    decision_summary: str        # One-line human-readable explanation

    def to_dict(self) -> dict:
        return asdict(self)

    def to_jsonl_entry(self) -> str:
        """Serialize for .workspace/trace/routing_decisions.jsonl"""
        return json.dumps(self.to_dict(), ensure_ascii=False)


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
    decision_trace: Optional[DecisionTrace] = None  # Full structured trace
    needs_statistical_verification: bool = False
    needs_latex_verification: bool = False
    first_of_type: bool = False   # First auto_fix in its category
    deai_priority: bool = False   # True = expression issue, DeAI-first path

    def to_dict(self) -> dict:
        # asdict() recursively converts nested dataclasses (DecisionTrace) to dicts
        return asdict(self)


def route_issues(
    issues: List[Dict],
    budget: str = "full",
    seen_categories: Optional[set] = None,
    trace_dir: Union[Path, bool, None] = None,
) -> Tuple[List[RoutedIssue], Dict]:
    """
    Route consolidated issues through Red Line checks and budget ceiling.
    
    Args:
        issues: List of issue dicts from consolidation (with action_type)
        budget: "full" | "medium" | "minimal"
        seen_categories: Categories that have been previously auto_fixed 
                        (for first-of-type tracking). Modified in-place.
        trace_dir: Directory to write decision traces.
                   - None: use default (.workspace/trace/)
                   - Path: use specified directory
                   - False: disable trace writing entirely
    
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

    # Write decision traces to JSONL
    if trace_dir is not False:
        _write_decision_traces(routed, trace_dir)

    return routed, stats


def _write_decision_traces(
    routed: List[RoutedIssue], trace_dir: Optional[Path] = None
) -> None:
    """Persist decision traces to .workspace/trace/routing_decisions.jsonl."""
    if trace_dir is None:
        trace_dir = WORKSPACE / "trace"
    
    try:
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_file = trace_dir / "routing_decisions.jsonl"
        
        with open(trace_file, "a", encoding="utf-8") as f:
            for issue in routed:
                if issue.decision_trace:
                    f.write(issue.decision_trace.to_jsonl_entry() + "\n")
    except (OSError, IOError):
        # Trace writing is best-effort — never block the pipeline
        pass


def _route_single_issue(
    issue: Dict, budget: str, seen_categories: set, stats: Dict
) -> RoutedIssue:
    """Route a single issue. Applies checks in order: Red Lines → First-of-type → Budget.
    
    Produces a full DecisionTrace recording WHY each check passed/failed,
    enabling post-hoc observability (like a bid explanation in ad systems).
    """
    
    original_action = issue.get("action_type", "guidance")
    effective = original_action
    notes = []
    checks_applied = []  # Structured trace of each check

    # --- Gather risk factors for trace ---
    category = issue.get("category", "unknown")
    touches_thesis = _touches_thesis(issue)
    introduces_claims = _might_introduce_new_claims(issue)
    meta_risk = _get_meta_risk_for_category(category)
    
    risk_factors = {
        "meta_risk": meta_risk,
        "touches_thesis": touches_thesis,
        "might_introduce_claims": introduces_claims,
        "category": category,
        "budget": budget,
        "category_previously_seen": category in seen_categories,
    }

    # --- Red Line 1: Never modify core thesis ---
    if touches_thesis and effective in ("auto_fix", "confirm_fix"):
        effective = "guidance"
        notes.append("RED_LINE_1: touches thesis/causal claim → forced guidance")
        checks_applied.append({
            "check": "RED_LINE_1_THESIS",
            "triggered": True,
            "reason": f"Issue in thesis-bearing section with causal/contribution "
                      f"language; original '{original_action}' forced to 'guidance' "
                      f"because core arguments must never be auto-modified",
        })
        stats["red_line_downgrades"] += 1
    else:
        checks_applied.append({
            "check": "RED_LINE_1_THESIS",
            "triggered": False,
            "reason": "Not in thesis section or no thesis-pattern match" 
                      if not touches_thesis 
                      else f"Touches thesis but action is '{effective}' (already guidance)",
        })

    # --- Red Line 2: Flag potential fabrication risk ---
    if introduces_claims:
        if effective == "auto_fix":
            effective = "confirm_fix"
            notes.append("RED_LINE_2_PRECAUTION: fix might introduce new claims → confirm_fix")
            checks_applied.append({
                "check": "RED_LINE_2_FABRICATION",
                "triggered": True,
                "reason": f"Category '{category}' or suggestion implies adding new "
                          f"citations/data; downgraded from auto_fix to confirm_fix "
                          f"to prevent hallucinated references",
            })
            stats["red_line_downgrades"] += 1
        else:
            checks_applied.append({
                "check": "RED_LINE_2_FABRICATION",
                "triggered": False,
                "reason": f"Issue might introduce claims but action is '{effective}' "
                          f"(not auto_fix), no downgrade needed",
            })
    else:
        checks_applied.append({
            "check": "RED_LINE_2_FABRICATION",
            "triggered": False,
            "reason": "Suggestion does not imply adding new citations/data/evidence",
        })

    # --- First-of-type validation ---
    if effective == "auto_fix" and category not in seen_categories:
        effective = "confirm_fix"
        notes.append(f"FIRST_OF_TYPE: first auto_fix in category '{category}' → confirm for validation")
        checks_applied.append({
            "check": "FIRST_OF_TYPE",
            "triggered": True,
            "reason": f"Category '{category}' has never been auto_fixed before; "
                      f"requiring human confirmation on first instance to validate "
                      f"that auto_fix quality is acceptable for this category",
        })
        stats["first_of_type_confirms"] += 1
        # Don't add to seen_categories yet — only add after user confirms
    elif effective == "auto_fix" and category in seen_categories:
        notes.append(f"CATEGORY_SEEN: '{category}' previously confirmed → auto_fix allowed")
        checks_applied.append({
            "check": "FIRST_OF_TYPE",
            "triggered": False,
            "reason": f"Category '{category}' previously confirmed by user; "
                      f"auto_fix permitted without additional validation",
        })
    else:
        checks_applied.append({
            "check": "FIRST_OF_TYPE",
            "triggered": False,
            "reason": f"Action is '{effective}' (not auto_fix); first-of-type check N/A",
        })

    # --- Budget ceiling ---
    budget_effective = BUDGET_CEILING.get((effective, budget), "guidance")
    if budget_effective != effective:
        notes.append(f"BUDGET_{budget.upper()}: {effective} → {budget_effective}")
        checks_applied.append({
            "check": "BUDGET_CEILING",
            "triggered": True,
            "reason": f"Budget mode '{budget}' caps '{effective}' to '{budget_effective}'; "
                      f"budget acts as maximum aggressiveness ceiling",
        })
        effective = budget_effective
        stats["budget_downgrades"] += 1
    else:
        checks_applied.append({
            "check": "BUDGET_CEILING",
            "triggered": False,
            "reason": f"Budget '{budget}' allows '{effective}'; no downgrade needed",
        })

    # --- Statistical verification flag ---
    needs_stata = _needs_statistical_verification(issue)

    # --- LaTeX verification flag ---
    needs_latex = _needs_latex_verification(issue)

    # --- DeAI priority: expression/presentation issues get DeAI-first routing ---
    is_deai_priority = _is_deai_priority(issue, category)
    if is_deai_priority:
        notes.append("DEAI_PRIORITY: expression/style issue → DeAI-aware path")

    # --- Build decision summary (one-line human-readable) ---
    decision_summary = _build_decision_summary(
        original_action, effective, category, checks_applied, 
        seen_categories, budget, meta_risk
    )

    # --- Construct DecisionTrace ---
    issue_id = issue.get("id", "ISS-???")
    trace = DecisionTrace(
        issue_id=issue_id,
        timestamp=time.time(),
        original_action=original_action,
        final_action=effective,
        checks_applied=checks_applied,
        risk_factors=risk_factors,
        decision_summary=decision_summary,
    )

    return RoutedIssue(
        id=issue_id,
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
        decision_trace=trace,
        needs_statistical_verification=needs_stata,
        needs_latex_verification=needs_latex,
        first_of_type=(effective == "confirm_fix" and 
                       "FIRST_OF_TYPE" in " ".join(notes)),
        deai_priority=is_deai_priority,
    )


def _build_decision_summary(
    original: str, final: str, category: str,
    checks: List[Dict], seen_categories: set,
    budget: str, meta_risk: str,
) -> str:
    """Build a one-line human-readable decision explanation.
    
    Examples:
        "auto_fix chosen: category 'clarity' previously confirmed, low risk, no Red Line triggers"
        "downgraded to guidance: RED_LINE_1 (thesis content) overrides original auto_fix"
    """
    triggered = [c["check"] for c in checks if c["triggered"]]
    
    if original == final and not triggered:
        # No changes — explain why it was safe
        reasons = []
        if category in seen_categories:
            reasons.append(f"category '{category}' previously confirmed")
        reasons.append(f"{meta_risk} risk")
        reasons.append("no Red Line triggers")
        return f"{final} chosen: {', '.join(reasons)}"
    
    elif original == final:
        # Checks fired but didn't change outcome (e.g., was already guidance)
        return (f"{final} unchanged: checks [{', '.join(triggered)}] fired "
                f"but action was already at or below threshold")
    
    else:
        # Downgraded — explain what caused it
        return (f"downgraded {original} → {final}: "
                f"{', '.join(triggered)} applied; "
                f"budget='{budget}', meta_risk='{meta_risk}'")


def _get_meta_risk_for_category(category: str) -> str:
    """Get tool metadata risk level relevant to a category.
    
    Maps issue categories to their most likely tool and returns the
    risk level from tool_metadata. Returns 'unknown' if no mapping.
    """
    # Map categories to their primary execution tool
    category_tool_map = {
        "clarity": "rewrite_section",
        "presentation": "rewrite_section",
        "writing_quality": "rewrite_section",
        "style": "rewrite_section",
        "grammar": "rewrite_section",
        "missing_reference": "literature_verify",
        "missing_citation": "literature_verify",
        "structure": "architecture_diagnosis",
        "methodology": "rewrite_section",
        "data_gap": "rewrite_section",
    }
    tool_name = category_tool_map.get(category)
    if tool_name:
        return assess_risk_level(tool_name)
    return "unknown"


def _assess_risk_from_meta(tool_name: str) -> Optional[str]:
    """Assess whether a tool call should be downgraded based on its metadata.

    Returns suggested action_type override, or None if metadata doesn't
    dictate a specific action. This provides a fallback risk assessment
    for tools not explicitly handled by the per-case Red Line logic.

    Risk level → action mapping:
        high   → "confirm_fix" (never auto-execute high-risk)
        medium → respect original action (no override)
        low    → respect original action (no override)
    """
    meta = get_tool_meta(tool_name)
    if meta is None:
        return None

    # Forced confirmation from metadata
    if meta.requires_confirmation:
        return "confirm_fix"

    # High risk write operations should not be auto-fixed
    risk = assess_risk_level(tool_name)
    if risk == "high":
        return "confirm_fix"

    return None


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


def _is_deai_priority(issue: Dict, category: str) -> bool:
    """Check if this issue is expression/style-related and should use DeAI-aware path.
    
    DeAI-priority issues get reviewer context passed to the DeAI engine during
    the post-rewrite audit, enabling more targeted AI-writing detection.
    """
    # Direct category match
    if category in ("presentation", "writing_quality", "clarity", "style"):
        return True
    
    # Check comment_type field
    comment_type = issue.get("comment_type", "").lower()
    if comment_type == "presentation":
        return True
    
    # Check impact_dimensions for expression_clarity
    impact = issue.get("impact_dimensions", {})
    if impact.get("expression_clarity", 0) >= 0.4:
        return True
    
    # Keyword detection in description
    desc = (issue.get("description", "") + " " + issue.get("suggestion", "")).lower()
    expression_keywords = [
        "writing quality", "readability", "unclear", "verbose",
        "wordy", "repetitive", "awkward phrasing", "style",
        "ai-like", "sounds generated", "monotonous",
    ]
    return any(kw in desc for kw in expression_keywords)


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


def _needs_latex_verification(issue: Dict) -> bool:
    """Check if this issue would benefit from LaTeX compilation verification."""
    category = issue.get("category", "").lower()
    description = issue.get("description", "").lower()

    latex_categories = {
        "formatting", "format", "citation_format", "compilation",
        "latex_error", "package", "cross_reference", "bibliography",
    }
    if category in latex_categories:
        return True

    latex_keywords = [
        "latex", "compilation", "undefined reference", "missing package",
        "bibtex", "biblatex", "citation format", "cross-reference",
        "\\usepackage", "documentclass", "figure float", "table placement",
        "overfull", "underfull", "hbox", "bibliography",
    ]
    return any(kw in description for kw in latex_keywords)


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
        if r.needs_latex_verification:
            lines.append(f"    📄 Needs LaTeX verification (compilation/bib)")
        if r.deai_priority:
            lines.append(f"    🔍 DeAI-priority: reviewer context will inform DeAI audit")

    return "\n".join(lines)
