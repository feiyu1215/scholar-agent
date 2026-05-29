"""
Phase-aware Tool Filter — Reduces cognitive load on the LLM by only exposing
relevant tools for the current workflow phase.

Principle: At each phase, the LLM sees:
  1. Universal tools (always available)
  2. Phase-specific tools (primary actions for current phase)
  3. Lookahead tools (next logical step, to enable planning)

Tools NOT in the current filter are hidden from the LLM's tool list,
preventing hallucinated calls and reducing prompt tokens.
"""

from __future__ import annotations

from utils.goal_tracker import Phase


# Universal tools: always available regardless of phase
UNIVERSAL_TOOLS = {
    "read_agent_guidelines",
    "ask_user",
    "session_status",
    "read_section_index",
    "read_section",
    "diff_section",
    "read_revision_log",
    "read_issues",
    "revision_progress",
    # Goal & Plan management (always available for meta-control)
    "set_goal",
    "complete_goal",
    "save_plan",
    "load_plan",
    "advance_plan",
    "self_critique",
    # Learning & Memory (Wave 4, always available)
    "record_lesson",
    "observe_edit",
}

# Phase → set of tools available in that phase
PHASE_TOOL_MAP: dict[Phase, set[str]] = {
    Phase.IDLE: {
        "parse_paper",
    },
    Phase.PARSING: {
        "parse_paper",
        "build_voice_profile",
    },
    Phase.ANALYSIS: {
        "presubmission_check",
        "architecture_diagnosis",
        "consistency_check",
        "review_paper",
        "build_voice_profile",
        "show_author_profile",
    },
    Phase.REVIEW: {
        "review_paper",
        "consolidate_reviews",
        "run_single_reviewer",
        "route_issues",
        "search_literature",
        "verify_doi",
        "verify_citations",
        "check_citation_content",
        "check_citation_alignment",
        "verify_and_enrich_citations",
    },
    Phase.ROUTING: {
        "route_issues",
        "generate_fix_proposal",
        "approve_fix",
        "dry_run_estimate",
        # Allow jumping into revision
        "rewrite_section",
        "generate_rewrite",
        "edit_section",
    },
    Phase.REVISION: {
        "rewrite_section",
        "generate_rewrite",
        "commit_rewrite",
        "verify_rewrite_quality",
        "edit_section",
        "parallel_rewrite",
        "generate_fix_proposal",
        "approve_fix",
        # De-AI tools available during revision
        "deai_audit",
        "deai_closed_loop",
        "deai_detect",
        "deai_diagnose",
        "deai_rewrite",
        "deai_verify",
        # Can go back to check state
        "route_issues",
    },
    Phase.VERIFICATION: {
        "verify_rewrite_quality",
        "deai_audit",
        "deai_closed_loop",
        "deai_detect",
        "deai_diagnose",
        "deai_rewrite",
        "deai_verify",
        "consistency_check",
        "presubmission_check",
        # Can loop back to revision
        "rewrite_section",
        "generate_rewrite",
        "commit_rewrite",
        "edit_section",
    },
    Phase.DONE: {
        # Minimal tools for final adjustments
        "presubmission_check",
        "consistency_check",
        "edit_section",
    },
}


def filter_tools_for_phase(all_tools: list[dict], phase: Phase) -> list[dict]:
    """Filter tool schemas to only those relevant for the current phase.

    Args:
        all_tools: Full list of tool schema dicts (from TOOLS array)
        phase: Current workflow phase

    Returns:
        Filtered list of tool schemas (subset of all_tools)
    """
    allowed_names = UNIVERSAL_TOOLS | PHASE_TOOL_MAP.get(phase, set())

    return [tool for tool in all_tools if tool["name"] in allowed_names]


def get_hidden_tools_hint(all_tools: list[dict], phase: Phase) -> str:
    """Generate a compact hint about hidden tools (injected as a footnote).

    This lets the LLM know other tools exist without overwhelming it.
    """
    allowed_names = UNIVERSAL_TOOLS | PHASE_TOOL_MAP.get(phase, set())
    hidden = [t["name"] for t in all_tools if t["name"] not in allowed_names]

    if not hidden:
        return ""

    return (
        f"\n[Note: {len(hidden)} additional tools are available in later phases. "
        f"Current phase: {phase.value}. "
        f"Phase advances automatically as you complete workflow steps.]"
    )
