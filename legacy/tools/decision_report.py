"""
tools/decision_report.py — Post-pipeline decision summary generator.

Produces a structured "decision report" after paper processing completes,
analogous to an ad system's bid explanation or a brand diagnostic report.

The report answers: "What did the Agent decide, why, and what was the impact?"

Design:
- Called after score_track in the pipeline (final observability step)
- Outputs both structured JSON (machine-readable) and Markdown (human-readable)
- Aggregates individual DecisionTraces from routing into a cohesive narrative
- Attributes score improvements to specific decision categories

Usage:
    from tools.decision_report import generate_decision_report
    
    report = generate_decision_report(
        routed_issues=routed,
        routing_stats=stats,
        score_before=4.2,
        score_after=6.8,
    )
    report.save()  # writes to .workspace/reports/
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict

from tools.action_router import RoutedIssue, DecisionTrace

WORKSPACE = Path(".workspace")


@dataclass
class ScoreAttribution:
    """Attributes score delta to a specific action category."""
    action_type: str           # "auto_fix" | "confirm_fix" | "guidance"
    issue_count: int
    estimated_contribution: float  # Estimated score points contributed
    categories: List[str]      # Which issue categories were in this group
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CapabilityBoundary:
    """An issue that exceeded the Agent's automatic handling capability."""
    issue_id: str
    category: str
    reason: str                # Why it couldn't be auto-handled
    effective_action: str      # What it was downgraded to
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DecisionReport:
    """Complete decision report for a paper processing run.
    
    Structured like a brand diagnostic report:
    - Executive summary (one-paragraph overview)
    - Processing overview (counts and categories)
    - Score attribution (what contributed to improvement)
    - Decision patterns (Red Lines, budget, first-of-type)
    - Capability boundaries (what couldn't be handled)
    - Full decision traces (for deep inspection)
    """
    # Metadata
    timestamp: float
    paper_id: Optional[str] = None
    
    # Processing overview
    total_issues: int = 0
    action_counts: Dict[str, int] = field(default_factory=dict)
    categories_processed: List[str] = field(default_factory=list)
    
    # Score attribution
    score_before: Optional[float] = None
    score_after: Optional[float] = None
    score_delta: Optional[float] = None
    attributions: List[ScoreAttribution] = field(default_factory=list)
    
    # Decision patterns
    red_line_count: int = 0
    budget_downgrade_count: int = 0
    first_of_type_count: int = 0
    decision_pattern_summary: str = ""
    
    # Capability boundaries
    boundaries: List[CapabilityBoundary] = field(default_factory=list)
    
    # Raw traces (for deep inspection)
    traces: List[Dict] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    def to_markdown(self) -> str:
        """Generate human-readable Markdown summary."""
        lines = []
        lines.append("# Decision Report")
        lines.append("")
        
        # Executive summary
        lines.append("## Executive Summary")
        lines.append("")
        lines.append(self._executive_summary())
        lines.append("")
        
        # Processing overview
        lines.append("## Processing Overview")
        lines.append("")
        lines.append(f"- **Total issues processed**: {self.total_issues}")
        for action, count in sorted(self.action_counts.items()):
            lines.append(f"  - {action}: {count}")
        if self.categories_processed:
            lines.append(f"- **Categories**: {', '.join(sorted(set(self.categories_processed)))}")
        lines.append("")
        
        # Score attribution
        if self.score_before is not None and self.score_after is not None:
            lines.append("## Score Attribution")
            lines.append("")
            lines.append(f"**Score**: {self.score_before:.1f} → {self.score_after:.1f} "
                        f"(+{self.score_delta:.1f})")
            lines.append("")
            if self.attributions:
                lines.append("| Action Type | Issues | Est. Contribution | Categories |")
                lines.append("|------------|--------|-------------------|------------|")
                for attr in self.attributions:
                    cats = ", ".join(attr.categories[:3])
                    if len(attr.categories) > 3:
                        cats += f" (+{len(attr.categories) - 3} more)"
                    lines.append(f"| {attr.action_type} | {attr.issue_count} | "
                               f"+{attr.estimated_contribution:.1f} | {cats} |")
            lines.append("")
        
        # Decision patterns
        lines.append("## Decision Patterns")
        lines.append("")
        if self.red_line_count:
            lines.append(f"- **Red Line interventions**: {self.red_line_count} "
                        f"(thesis protection + fabrication prevention)")
        if self.first_of_type_count:
            lines.append(f"- **First-of-type confirmations**: {self.first_of_type_count} "
                        f"(conservative validation for new categories)")
        if self.budget_downgrade_count:
            lines.append(f"- **Budget downgrades**: {self.budget_downgrade_count} "
                        f"(aggressiveness ceiling applied)")
        if self.decision_pattern_summary:
            lines.append(f"- **Pattern**: {self.decision_pattern_summary}")
        lines.append("")
        
        # Capability boundaries
        if self.boundaries:
            lines.append("## Capability Boundaries")
            lines.append("")
            lines.append("The following issues exceeded automatic handling capability:")
            lines.append("")
            for b in self.boundaries:
                lines.append(f"- **[{b.issue_id}]** ({b.category}): {b.reason} "
                           f"→ routed to `{b.effective_action}`")
            lines.append("")
        
        return "\n".join(lines)
    
    def _executive_summary(self) -> str:
        """Generate one-paragraph executive summary."""
        parts = []
        parts.append(f"Processed {self.total_issues} issues")
        
        auto = self.action_counts.get("auto_fix", 0)
        confirm = self.action_counts.get("confirm_fix", 0)
        guidance = self.action_counts.get("guidance", 0)
        parts.append(f"({auto} auto-fixed, {confirm} confirmed, {guidance} guidance-only)")
        
        if self.score_delta is not None:
            parts.append(f"with a score improvement of +{self.score_delta:.1f} "
                        f"({self.score_before:.1f} → {self.score_after:.1f})")
        
        if self.boundaries:
            parts.append(f". {len(self.boundaries)} issue(s) exceeded automatic "
                        f"handling capability")
        
        summary = " ".join(parts) + "."
        
        if self.red_line_count:
            summary += (f" Red Line protection activated {self.red_line_count} time(s) "
                       f"to safeguard thesis integrity.")
        
        return summary
    
    def save(self, output_dir: Optional[Path] = None) -> Tuple[Path, Path]:
        """Save report as both JSON and Markdown.
        
        Returns:
            (json_path, markdown_path)
        """
        if output_dir is None:
            output_dir = WORKSPACE / "reports"
        
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Use timestamp for filename uniqueness
        ts = int(self.timestamp)
        json_path = output_dir / f"decision_report_{ts}.json"
        md_path = output_dir / f"decision_report_{ts}.md"
        
        json_path.write_text(self.to_json(), encoding="utf-8")
        md_path.write_text(self.to_markdown(), encoding="utf-8")
        
        return json_path, md_path


def generate_decision_report(
    routed_issues: List[RoutedIssue],
    routing_stats: Dict,
    score_before: Optional[float] = None,
    score_after: Optional[float] = None,
    paper_id: Optional[str] = None,
) -> DecisionReport:
    """Generate a decision report from routing results and score tracking.
    
    Args:
        routed_issues: List of RoutedIssue from route_issues()
        routing_stats: Stats dict from route_issues()
        score_before: Paper score before processing (from score_track)
        score_after: Paper score after processing (from score_track)
        paper_id: Optional paper identifier
    
    Returns:
        DecisionReport with full analysis
    """
    report = DecisionReport(
        timestamp=time.time(),
        paper_id=paper_id,
        total_issues=routing_stats.get("total", len(routed_issues)),
        action_counts=routing_stats.get("action_counts", {}),
        red_line_count=routing_stats.get("red_line_downgrades", 0),
        budget_downgrade_count=routing_stats.get("budget_downgrades", 0),
        first_of_type_count=routing_stats.get("first_of_type_confirms", 0),
    )
    
    # Extract categories
    report.categories_processed = [r.category for r in routed_issues]
    
    # Score attribution
    if score_before is not None and score_after is not None:
        report.score_before = score_before
        report.score_after = score_after
        report.score_delta = score_after - score_before
        report.attributions = _compute_score_attribution(
            routed_issues, report.score_delta
        )
    
    # Decision pattern summary
    report.decision_pattern_summary = _summarize_decision_patterns(
        routing_stats, routed_issues
    )
    
    # Capability boundaries
    report.boundaries = _identify_boundaries(routed_issues)
    
    # Collect traces
    report.traces = [
        r.decision_trace.to_dict() 
        for r in routed_issues 
        if r.decision_trace is not None
    ]
    
    return report


def _compute_score_attribution(
    routed_issues: List[RoutedIssue],
    total_delta: float,
) -> List[ScoreAttribution]:
    """Estimate how much each action type contributed to score improvement.
    
    Attribution model (heuristic):
    - auto_fix issues contribute proportionally more (fully executed)
    - confirm_fix issues contribute moderately (executed with oversight)
    - guidance issues contribute minimally (only advice given)
    
    Weights: auto_fix=1.0, confirm_fix=0.6, guidance=0.1
    """
    # Group by effective action
    groups: Dict[str, List[RoutedIssue]] = {
        "auto_fix": [],
        "confirm_fix": [],
        "guidance": [],
    }
    for r in routed_issues:
        action = r.effective_action
        if action in groups:
            groups[action].append(r)
    
    # Weighted attribution
    weights = {"auto_fix": 1.0, "confirm_fix": 0.6, "guidance": 0.1}
    total_weighted = sum(
        len(issues) * weights[action] 
        for action, issues in groups.items()
    )
    
    attributions = []
    for action, issues in groups.items():
        if not issues:
            continue
        weighted_share = (len(issues) * weights[action]) / max(total_weighted, 0.001)
        contribution = total_delta * weighted_share
        categories = list(set(r.category for r in issues))
        
        attributions.append(ScoreAttribution(
            action_type=action,
            issue_count=len(issues),
            estimated_contribution=round(contribution, 2),
            categories=categories,
        ))
    
    # Sort by contribution descending
    attributions.sort(key=lambda a: a.estimated_contribution, reverse=True)
    return attributions


def _summarize_decision_patterns(
    stats: Dict, routed_issues: List[RoutedIssue]
) -> str:
    """Generate a one-line pattern summary of this run's decisions."""
    total = stats.get("total", 0)
    if total == 0:
        return "No issues processed"
    
    auto_pct = (stats.get("action_counts", {}).get("auto_fix", 0) / total) * 100
    
    if auto_pct > 70:
        style = "Aggressive (>70% auto-fixed)"
    elif auto_pct > 40:
        style = "Balanced (40-70% auto-fixed)"
    elif auto_pct > 10:
        style = "Conservative (10-40% auto-fixed)"
    else:
        style = "Highly conservative (<10% auto-fixed)"
    
    red_lines = stats.get("red_line_downgrades", 0)
    if red_lines > 0:
        style += f"; {red_lines} Red Line intervention(s)"
    
    return style


def _identify_boundaries(
    routed_issues: List[RoutedIssue],
) -> List[CapabilityBoundary]:
    """Identify issues that exceeded automatic handling capability.
    
    An issue is "beyond capability" if:
    - It was downgraded from auto_fix to guidance (skipped 2 levels)
    - It was originally auto_fix but Red Line forced guidance
    - It explicitly needs statistical verification (external tool required)
    """
    boundaries = []
    
    for r in routed_issues:
        reason = None
        
        # Case 1: Skipped 2 levels (auto_fix → guidance)
        if r.action_type == "auto_fix" and r.effective_action == "guidance":
            # Determine why from decision trace
            if r.decision_trace:
                triggered = [
                    c["check"] for c in r.decision_trace.checks_applied 
                    if c["triggered"]
                ]
                if "RED_LINE_1_THESIS" in triggered:
                    reason = "Touches core thesis — modification forbidden"
                elif "BUDGET_CEILING" in triggered:
                    reason = "Budget constraints prevent execution"
                else:
                    reason = f"Multiple constraints: {', '.join(triggered)}"
            else:
                reason = "Downgraded across 2 levels (cause unknown)"
        
        # Case 2: Needs statistical verification (external tool)
        elif r.needs_statistical_verification and r.effective_action != "guidance":
            reason = "Requires external statistical verification (Stata/R)"
        
        if reason:
            boundaries.append(CapabilityBoundary(
                issue_id=r.id,
                category=r.category,
                reason=reason,
                effective_action=r.effective_action,
            ))
    
    return boundaries


def format_decision_report_compact(report: DecisionReport) -> str:
    """Format a compact single-paragraph summary for inline display.
    
    Example output:
        "Processed 12 issues: 7 auto-fixed, 3 confirmed, 2 guidance-only.
         Score: 4.2 → 6.8 (+2.6). Red Line: 1 intervention. 
         2 issues beyond auto-handling capability."
    """
    parts = []
    
    auto = report.action_counts.get("auto_fix", 0)
    confirm = report.action_counts.get("confirm_fix", 0)
    guidance = report.action_counts.get("guidance", 0)
    
    parts.append(f"Processed {report.total_issues} issues: "
                f"{auto} auto-fixed, {confirm} confirmed, {guidance} guidance-only.")
    
    if report.score_delta is not None:
        parts.append(f"Score: {report.score_before:.1f} → {report.score_after:.1f} "
                    f"(+{report.score_delta:.1f}).")
    
    interventions = []
    if report.red_line_count:
        interventions.append(f"Red Line: {report.red_line_count}")
    if report.first_of_type_count:
        interventions.append(f"First-of-type: {report.first_of_type_count}")
    if report.budget_downgrade_count:
        interventions.append(f"Budget: {report.budget_downgrade_count}")
    if interventions:
        parts.append(f"Interventions: {', '.join(interventions)}.")
    
    if report.boundaries:
        parts.append(f"{len(report.boundaries)} issue(s) beyond auto-handling capability.")
    
    return " ".join(parts)
