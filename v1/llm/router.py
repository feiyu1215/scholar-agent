"""
llm/router.py — Three-tier model routing for cost optimization.

Maps tasks to model tiers (HIGH/MEDIUM/LOW) based on cognitive complexity:
- HIGH: Core quality tasks (review, rewrite, proposal generation)
- MEDIUM: Structured detection/rewriting (de-AI audit, signal fix, consolidation)
- LOW: Classification, summarization (action_type routing, context compression)

Models per tier are configurable via environment variables.
Default: all tiers use the same model (graceful no-op if env not set).
"""

import os
from typing import Optional

# ============================================================
# Tier Configuration (override via env vars)
# ============================================================

# Default model from environment (same as client.py's DEFAULT_MODEL)
_DEFAULT = os.environ.get("LLM_MODEL", "gpt-4.1-mini")

MODEL_TIERS = {
    "high": os.environ.get("LLM_MODEL_HIGH", _DEFAULT),
    "medium": os.environ.get("LLM_MODEL_MEDIUM", _DEFAULT),
    "low": os.environ.get("LLM_MODEL_LOW", _DEFAULT),
}

# ============================================================
# Task → Tier Mapping
# ============================================================

TASK_TIER_MAP = {
    # HIGH: Core output quality (deep reasoning, writing)
    "review_paper": "high",
    "rewrite_section": "high",
    "generate_fix_proposal": "high",
    "fix_proposal": "high",

    # MEDIUM: Structured analysis/detection (less creative, more evaluative)
    "deai_audit": "medium",
    "deai_fix": "medium",
    "fix_ai_signals": "medium",
    "consolidate": "medium",
    "consolidate_review": "medium",
    "stata_verify": "medium",

    # MEDIUM: Literature & Figure verification (structured analysis)
    "literature_verify": "medium",
    "figure_analysis": "medium",

    # LOW: Classification, formatting, summarization
    "classify_action_type": "low",
    "auto_compact_summary": "low",
    "route_issues": "low",
}

# Complexity-based overrides (fine-grained tier selection)
COMPLEXITY_DOWNGRADES = {
    "rewrite_section": {
        "sentence_level": "medium",   # Simple sentence fix → medium is enough
        "word_level": "low",          # Single word substitution → low
        "formatting": "low",          # Formatting-only edits → low
    },
    "generate_fix_proposal": {
        "minor": "medium",            # Minor issue proposals → medium
        "formatting": "low",          # Formatting fix proposals → low
    },
    "deai_audit": {
        "recheck": "medium",          # Re-audit after fix → medium (no creative work)
    },
    "literature_verify": {
        "doi_only": "low",            # Pure DOI existence check → low
    },
    "consolidate_review": {
        "summary_only": "low",        # Just summarize existing reviews → low
    },
}


# ============================================================
# Public API
# ============================================================

def get_model_for_task(task_name: str, complexity: Optional[str] = None) -> str:
    """
    Get the appropriate model for a given task.

    Args:
        task_name: Tool/task identifier (e.g., "rewrite_section", "deai_audit")
        complexity: Optional fix_complexity for fine-grained downgrade

    Returns:
        Model name string to pass to LLMClient
    """
    tier = TASK_TIER_MAP.get(task_name, "high")  # Default to high (safe)

    # Fine-grained downgrade based on complexity
    if complexity and task_name in COMPLEXITY_DOWNGRADES:
        override_tier = COMPLEXITY_DOWNGRADES[task_name].get(complexity)
        if override_tier:
            tier = override_tier

    return MODEL_TIERS[tier]


def get_tier_for_task(task_name: str, complexity: Optional[str] = None) -> str:
    """Get the tier name (for logging/tracing purposes)."""
    tier = TASK_TIER_MAP.get(task_name, "high")
    if complexity and task_name in COMPLEXITY_DOWNGRADES:
        override_tier = COMPLEXITY_DOWNGRADES[task_name].get(complexity)
        if override_tier:
            tier = override_tier
    return tier


# ============================================================
# Task-Provider Affinity (Phase 3.1)
# ============================================================
# Some tasks benefit from specific providers (e.g., Anthropic
# for nuanced review, DeepSeek for cost-effective classification).
# Format: task_name -> preferred_provider_name
# The failover client will try this provider first, then fall back.

TASK_MODEL_AFFINITY = {
    # Deep reasoning tasks prefer Anthropic (if available)
    "review_paper": "anthropic",
    "rewrite_section": "anthropic",
    "generate_fix_proposal": "anthropic",

    # Cost-sensitive tasks prefer DeepSeek (if available)
    "classify_action_type": "deepseek",
    "auto_compact_summary": "deepseek",
    "route_issues": "deepseek",

    # Search and verification tasks - any provider works
    # (no affinity set, uses default priority order)
}


def get_preferred_provider(task_name: str) -> Optional[str]:
    """Get the preferred provider for a task, or None for default ordering."""
    return TASK_MODEL_AFFINITY.get(task_name)


def get_routing_summary() -> dict:
    """Return routing config for /stats display."""
    return {
        "model_tiers": MODEL_TIERS,
        "task_mapping": TASK_TIER_MAP,
        "downgrades": COMPLEXITY_DOWNGRADES,
        "task_affinity": TASK_MODEL_AFFINITY,
    }
