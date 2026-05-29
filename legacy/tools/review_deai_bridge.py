"""
tools/review_deai_bridge.py — Bridge between Review Engine and DeAI Engine.

Extracts expression/style-related issues from review output and converts them
into structured "hints" that the DeAI audit engine can use as prior context.

This integration allows DeAI to:
1. Know WHICH sentences the reviewer already flagged as problematic
2. Prioritize dimensions that align with reviewer concerns
3. Avoid redundant detection on sentences already rewritten for other reasons

Design principle: DeAI remains independent (examiner ≠ examinee) but gets
contextual hints — it still makes its own judgment, not rubber-stamping review.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, field, asdict

WORKSPACE = Path(".workspace")


@dataclass
class ReviewHint:
    """A single hint extracted from a review issue for DeAI consumption.
    
    These are NOT directives — DeAI uses them as contextual awareness,
    not as instructions to find problems.
    """
    quote: str                    # The exact text the reviewer flagged
    concern: str                  # Brief description of the expression concern
    source_section: str           # Section where this appears
    suggested_dimension: str      # Which DeAI dimension this maps to
    severity: str                 # From review: "major" / "moderate" / "minor"
    issue_id: str = ""            # Original issue ID for traceability
    
    def to_dict(self) -> Dict:
        return asdict(self)


# ─── Mapping: Review concerns → DeAI dimensions ─────────────────────────────

# Keywords in review issue descriptions that suggest specific DeAI dimensions
_CONCERN_TO_DIMENSION = {
    # vocabulary dimension
    "jargon": "vocabulary",
    "buzzword": "vocabulary",
    "vague": "vocabulary",
    "unclear term": "vocabulary",
    "promotional": "vocabulary",
    "hyperbolic": "vocabulary",
    "inflated": "vocabulary",
    "cliché": "vocabulary",
    "overuse": "vocabulary",
    
    # rhythm dimension
    "monotonous": "rhythm",
    "repetitive structure": "rhythm",
    "sentence length": "rhythm",
    "uniform": "rhythm",
    "choppy": "rhythm",
    "run-on": "rhythm",
    "listy": "rhythm",
    "parallel": "rhythm",
    
    # connectors dimension
    "transition": "connectors",
    "however": "connectors",
    "moreover": "connectors",
    "furthermore": "connectors",
    "filler": "connectors",
    "wordy": "connectors",
    "verbose": "connectors",
    "hedge": "connectors",
    "weak opener": "connectors",
    
    # punctuation dimension
    "em dash": "punctuation",
    "dash": "punctuation",
    "semicolon": "punctuation",
    "colon": "punctuation",
    "formatting": "punctuation",
    
    # voice dimension
    "passive": "voice",
    "voice": "voice",
    "register": "voice",
    "tone": "voice",
    "inconsistent style": "voice",
    "awkward": "voice",
}


def _infer_dimension(issue: Dict) -> str:
    """Infer which DeAI dimension a review issue maps to based on content."""
    searchable = (
        issue.get("description", "") + " " + 
        issue.get("explanation", "") + " " +
        issue.get("suggestion", "") + " " +
        issue.get("title", "")
    ).lower()
    
    for keyword, dimension in _CONCERN_TO_DIMENSION.items():
        if keyword in searchable:
            return dimension
    
    # Default: if it's a presentation issue, most likely vocabulary or connectors
    return "vocabulary"


def extract_review_hints(
    issues: List[Dict],
    section_filter: Optional[str] = None,
) -> List[ReviewHint]:
    """
    Extract DeAI-relevant hints from review issues.
    
    Filters to only expression/presentation issues (comment_type == "presentation"
    or high expression_clarity impact dimension).
    
    Args:
        issues: List of review issue dicts (from consolidated.json or routed_issues.json)
        section_filter: If provided, only return hints for this section
    
    Returns:
        List of ReviewHint objects for DeAI context injection
    """
    hints = []
    
    for issue in issues:
        # Filter: only expression/style issues
        if not _is_expression_issue(issue):
            continue
        
        # Filter: section-specific if requested
        if section_filter:
            issue_section = _get_issue_section(issue)
            if section_filter.lower() not in issue_section.lower():
                continue
        
        # Extract quote
        quote = _extract_quote(issue)
        if not quote:
            continue  # No specific text reference → not useful as hint
        
        # Build hint
        hint = ReviewHint(
            quote=quote[:300],  # Truncate long quotes
            concern=_build_concern_summary(issue),
            source_section=_get_issue_section(issue),
            suggested_dimension=_infer_dimension(issue),
            severity=issue.get("severity", "minor"),
            issue_id=issue.get("id", ""),
        )
        hints.append(hint)
    
    return hints


def _is_expression_issue(issue: Dict) -> bool:
    """Determine if a review issue is about expression/style (DeAI-relevant)."""
    # Direct match: comment_type == "presentation"
    comment_type = issue.get("comment_type", "").lower()
    if comment_type == "presentation":
        return True
    
    # Check category field (from routed issues)
    category = issue.get("category", "").lower()
    if category in ("presentation", "writing_quality", "clarity", "style"):
        return True
    
    # Check impact_dimensions if available
    impact = issue.get("impact_dimensions", {})
    if impact.get("expression_clarity", 0) >= 0.4:
        return True
    
    # Check description for expression-related keywords
    desc = (issue.get("description", "") + " " + issue.get("title", "")).lower()
    expression_keywords = [
        "writing quality", "clarity", "readability", "style",
        "awkward", "unclear expression", "poorly written",
        "grammatical", "verbose", "wordy", "repetitive",
        "ai-generated", "ai writing", "sounds artificial",
    ]
    return any(kw in desc for kw in expression_keywords)


def _get_issue_section(issue: Dict) -> str:
    """Extract section ID from issue (handles various formats)."""
    # Direct field
    if "source_section" in issue:
        return issue["source_section"]
    
    # Location dict
    location = issue.get("location", {})
    if isinstance(location, dict):
        return location.get("section_id", "")
    elif isinstance(location, str):
        return location
    
    return ""


def _extract_quote(issue: Dict) -> str:
    """Extract the quoted text from a review issue."""
    # Direct quote field
    if issue.get("quote"):
        return issue["quote"]
    
    # Location.quote
    location = issue.get("location", {})
    if isinstance(location, dict) and location.get("quote"):
        return location["quote"]
    
    return ""


def _build_concern_summary(issue: Dict) -> str:
    """Build a brief concern description for the hint."""
    title = issue.get("title", "")
    if title:
        return title[:100]
    
    explanation = issue.get("explanation", issue.get("description", ""))
    if explanation:
        # First sentence only
        first_sentence = explanation.split(".")[0]
        return first_sentence[:100]
    
    return "Expression/style concern flagged by reviewer"


def format_hints_for_prompt(hints: List[ReviewHint]) -> str:
    """
    Format review hints as context text for the DeAI audit prompt.
    
    This is injected into the DeAI audit system prompt so the LLM is
    AWARE of reviewer concerns but makes independent judgments.
    """
    if not hints:
        return ""
    
    lines = [
        "\n[REVIEWER CONTEXT — for awareness, not directives]\n"
        "A paper reviewer previously flagged these expression concerns in this text. "
        "Use this as additional context when evaluating, but make your own independent "
        "judgment about AI-writing signals. The reviewer's concerns may or may not "
        "align with DeAI signals.\n"
    ]
    
    for i, hint in enumerate(hints[:5], 1):  # Max 5 hints to avoid prompt bloat
        lines.append(
            f"  {i}. [{hint.suggested_dimension.upper()}] \"{hint.quote[:80]}...\" "
            f"— {hint.concern} (severity: {hint.severity})"
        )
    
    if len(hints) > 5:
        lines.append(f"  ... and {len(hints) - 5} more expression concerns.")
    
    lines.append("")  # trailing newline
    return "\n".join(lines)


def compute_dimension_bias(hints: List[ReviewHint]) -> Dict[str, float]:
    """
    Compute dimension weight biases based on reviewer concerns.
    
    If the reviewer flagged many rhythm issues, slightly boost rhythm dimension
    weight during scoring. This is a SOFT bias (max ±0.05 per dimension).
    
    Returns: dict of dimension → bias value (positive = boost importance)
    """
    if not hints:
        return {}
    
    MAX_BIAS = 0.05  # Never shift more than 5% weight
    
    # Count hints per dimension
    dimension_counts: Dict[str, int] = {}
    for hint in hints:
        dim = hint.suggested_dimension
        dimension_counts[dim] = dimension_counts.get(dim, 0) + 1
    
    total_hints = len(hints)
    if total_hints == 0:
        return {}
    
    # Convert counts to proportional bias (capped at MAX_BIAS)
    biases = {}
    for dim, count in dimension_counts.items():
        proportion = count / total_hints
        biases[dim] = min(proportion * 0.1, MAX_BIAS)  # 10% of proportion, max 5%
    
    return biases


def load_hints_for_section(section_id: str) -> List[ReviewHint]:
    """
    Load review hints for a specific section from the workspace.
    
    Reads consolidated.json or routed_issues.json and extracts expression hints.
    This is the primary entry point for write_engine integration.
    """
    # Try routed issues first (more structured)
    routed_path = WORKSPACE / "review" / "routed_issues.json"
    if routed_path.exists():
        try:
            issues = json.loads(routed_path.read_text(encoding="utf-8"))
            return extract_review_hints(issues, section_filter=section_id)
        except (json.JSONDecodeError, IOError):
            pass
    
    # Fall back to consolidated.json
    consolidated_path = WORKSPACE / "review" / "consolidated.json"
    if consolidated_path.exists():
        try:
            data = json.loads(consolidated_path.read_text(encoding="utf-8"))
            issues = data.get("issues", [])
            return extract_review_hints(issues, section_filter=section_id)
        except (json.JSONDecodeError, IOError):
            pass
    
    return []
