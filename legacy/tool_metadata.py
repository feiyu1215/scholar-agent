"""
core/tool_metadata.py — Declarative tool metadata for risk assessment.

Each tool declares its operational properties:
- operation: "read" | "write" | "verify" | "meta"
- scope: "sentence" | "paragraph" | "section" | "paper" | "external" | "system"
- reversible: whether the operation can be undone
- requires_confirmation: whether to force user confirmation regardless of budget

The action_router uses this metadata to automatically assess risk for any tool,
eliminating the need for per-tool hard-coded checks when adding new tools.

Design: Metadata is kept SEPARATE from tool_schemas.py because:
1. tool_schemas are sent to the LLM — meta fields would waste tokens
2. Metadata is only consumed by the internal router/planner
3. New tools can be added to schemas without touching router logic

Usage:
    from core.tool_metadata import get_tool_meta, assess_risk_level

    meta = get_tool_meta("rewrite_section")
    risk = assess_risk_level("rewrite_section")  # "high" | "medium" | "low"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class ToolMeta:
    """Metadata for a single tool's operational properties."""
    operation: str      # "read" | "write" | "verify" | "meta"
    scope: str          # "sentence" | "paragraph" | "section" | "paper" | "external" | "system"
    reversible: bool    # Can this operation be undone?
    requires_confirmation: bool = False  # Force confirmation regardless of budget?


# ============================================================
# Tool Metadata Registry
# ============================================================

TOOL_META: Dict[str, ToolMeta] = {
    # --- Agent Self-Navigation (meta operations) ---
    "read_agent_guidelines": ToolMeta("read", "system", True),
    "set_goal": ToolMeta("meta", "system", True),
    "complete_goal": ToolMeta("meta", "system", True),
    "save_plan": ToolMeta("meta", "system", True),
    "load_plan": ToolMeta("read", "system", True),
    "advance_plan": ToolMeta("meta", "system", True),
    "session_status": ToolMeta("read", "system", True),
    "load_skill": ToolMeta("read", "system", True),

    # --- Paper Management (read operations) ---
    "parse_paper": ToolMeta("read", "paper", True),
    "read_section_index": ToolMeta("read", "paper", True),
    "read_section": ToolMeta("read", "section", True),
    "diff_section": ToolMeta("read", "section", True),
    "read_revision_log": ToolMeta("read", "paper", True),

    # --- Structural Analysis (verify/read, zero risk) ---
    "architecture_diagnosis": ToolMeta("verify", "paper", True),
    "consistency_check": ToolMeta("verify", "paper", True),
    "analyze_figures": ToolMeta("verify", "paper", True),
    "presubmission_check": ToolMeta("verify", "paper", True),

    # --- Review Pipeline (read/verify) ---
    "review_paper": ToolMeta("verify", "paper", True),
    "run_single_reviewer": ToolMeta("verify", "paper", True),
    "consolidate_reviews": ToolMeta("write", "paper", True),
    "read_issues": ToolMeta("read", "paper", True),
    "save_previous_issues": ToolMeta("write", "paper", True),
    "route_issues": ToolMeta("meta", "paper", True),
    "generate_focus_points": ToolMeta("read", "paper", True),

    # --- Rewrite Pipeline (write operations, higher risk) ---
    "rewrite_section": ToolMeta("write", "section", True),
    "generate_rewrite": ToolMeta("write", "section", True),
    "parallel_rewrite": ToolMeta("write", "paper", True),
    "edit_section": ToolMeta("write", "paragraph", True),
    "commit_rewrite": ToolMeta("write", "section", False),  # Not easily reversible
    "generate_fix_proposal": ToolMeta("write", "paragraph", True),
    "approve_fix": ToolMeta("write", "paragraph", False, requires_confirmation=True),

    # --- Verification (post-write quality checks) ---
    "verify_rewrite_quality": ToolMeta("verify", "section", True),
    "self_critique": ToolMeta("verify", "section", True),
    "reaudit": ToolMeta("verify", "paper", True),
    "revision_progress": ToolMeta("read", "paper", True),

    # --- De-AI Pipeline (write, sentence-level) ---
    "deai_detect": ToolMeta("verify", "section", True),
    "deai_diagnose": ToolMeta("verify", "sentence", True),
    "deai_rewrite": ToolMeta("write", "sentence", True),
    "deai_verify": ToolMeta("verify", "sentence", True),
    "deai_audit": ToolMeta("verify", "section", True),
    "deai_closed_loop": ToolMeta("write", "section", True),

    # --- Citation & Literature (external queries) ---
    "verify_citations": ToolMeta("verify", "paper", True),
    "check_citation_alignment": ToolMeta("verify", "paragraph", True),
    "check_citation_content": ToolMeta("verify", "paragraph", True),
    "verify_and_enrich_citations": ToolMeta("write", "paper", True),
    "verify_doi": ToolMeta("verify", "external", True),
    "search_literature": ToolMeta("read", "external", True),
    "search_local_bibliography": ToolMeta("read", "external", True),
    "find_uncited_relevant": ToolMeta("read", "external", True),

    # --- Statistical Verification ---
    "stata_verify": ToolMeta("verify", "external", True),

    # --- LaTeX / Bibliography Verification (C-2) ---
    "latex_verify": ToolMeta("verify", "external", True),
    "bib_verify": ToolMeta("verify", "external", True),

    # --- Learning & Memory ---
    "record_lesson": ToolMeta("write", "system", True),
    "observe_edit": ToolMeta("write", "system", True),
    "show_author_profile": ToolMeta("read", "system", True),

    # --- Estimation (pure read/meta) ---
    "dry_run_estimate": ToolMeta("read", "paper", True),
    "estimate_single_operation": ToolMeta("read", "section", True),
    "list_checkpoints": ToolMeta("read", "system", True),

    # --- User Interaction ---
    "ask_user": ToolMeta("meta", "system", True),

    # --- Voice Profile ---
    "build_voice_profile": ToolMeta("write", "paper", True),
}


# ============================================================
# Risk Assessment
# ============================================================

def get_tool_meta(tool_name: str) -> Optional[ToolMeta]:
    """Get metadata for a tool. Returns None if tool not registered."""
    return TOOL_META.get(tool_name)


def assess_risk_level(tool_name: str) -> str:
    """Assess risk level of a tool from its metadata.

    Returns: "high" | "medium" | "low"

    Risk matrix:
        high   = write + (paper|external) + not reversible
        medium = write + any scope, OR not reversible
        low    = read | verify | meta, OR write + sentence + reversible
    """
    meta = TOOL_META.get(tool_name)
    if meta is None:
        # Unknown tool → conservative assessment
        return "medium"

    # Forced confirmation = always high risk
    if meta.requires_confirmation:
        return "high"

    # Read/verify/meta operations are inherently low risk
    if meta.operation in ("read", "verify", "meta"):
        return "low"

    # Write operations: risk depends on scope and reversibility
    if meta.operation == "write":
        if not meta.reversible:
            return "high"
        if meta.scope in ("paper", "external"):
            return "medium"
        if meta.scope in ("section",):
            return "medium"
        # sentence/paragraph level writes that are reversible
        return "low"

    return "medium"


def get_risk_summary() -> Dict[str, int]:
    """Get counts of tools by risk level."""
    summary = {"high": 0, "medium": 0, "low": 0}
    for tool_name in TOOL_META:
        level = assess_risk_level(tool_name)
        summary[level] += 1
    return summary


def get_tools_by_operation(operation: str) -> list:
    """Get all tools of a given operation type."""
    return [name for name, meta in TOOL_META.items() if meta.operation == operation]
