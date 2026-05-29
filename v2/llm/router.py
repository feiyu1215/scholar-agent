"""
llm/router.py — Three-tier model routing and ModelSuggester.

Maps tasks to model tiers (HIGH/MEDIUM/LOW) based on cognitive complexity:
- HIGH: Core quality tasks (review, rewrite, proposal generation)
- MEDIUM: Structured detection/rewriting (de-AI audit, signal fix, consolidation)
- LOW: Classification, summarization (action_type routing, context compression)

Models per tier are configurable via environment variables.
Default: all tiers use the same model (graceful no-op if env not set).

ModelSuggester (Phase 3):
- Suggests models based on task description keywords matched against model tags.
- Does NOT auto-switch; returns a formatted suggestion for the user to decide.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from llm.session_model_manager import ModelInfo

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

def get_model_for_task(
    task_name: str,
    complexity: Optional[str] = None,
    session_model_mgr=None,
) -> str:
    """
    Get the appropriate model for a given task.

    Args:
        task_name: Tool/task identifier (e.g., "rewrite_section", "deai_audit")
        complexity: Optional fix_complexity for fine-grained downgrade
        session_model_mgr: Optional SessionModelManager for dynamic tier resolution.
            When provided, tier→model mapping uses providers.json config instead of env vars.

    Returns:
        Model name string to pass to LLMClient
    """
    tier = TASK_TIER_MAP.get(task_name, "high")  # Default to high (safe)

    # Fine-grained downgrade based on complexity
    if complexity and task_name in COMPLEXITY_DOWNGRADES:
        override_tier = COMPLEXITY_DOWNGRADES[task_name].get(complexity)
        if override_tier:
            tier = override_tier

    return get_tier_model(tier, session_model_mgr=session_model_mgr)


def get_tier_model(tier: str, session_model_mgr=None) -> str:
    """
    获取指定 tier 的模型。优先从 SessionModelManager 读取。

    Args:
        tier: "high" / "medium" / "low"
        session_model_mgr: Optional SessionModelManager instance.
            When provided, uses providers.json tier_models config.
            When None, falls back to static MODEL_TIERS (env vars).

    Returns:
        Model ID string (never None).
    """
    if session_model_mgr is not None:
        return session_model_mgr.resolve_tier_model(tier)
    return MODEL_TIERS.get(tier, _DEFAULT)


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


# ============================================================
# ModelSuggester — Tag-based model recommendation (Phase 3)
# ============================================================

# Keyword → tag mapping for task description analysis.
# Keys are Chinese/English keywords that may appear in user task descriptions;
# values are model tags that would be a good fit.
TASK_KEYWORD_TAGS: dict[str, list[str]] = {
    # Reasoning / deep analysis
    "推理": ["reasoning"],
    "审稿": ["reasoning"],
    "review": ["reasoning"],
    "reasoning": ["reasoning"],
    "数学": ["reasoning", "math"],
    "math": ["math"],
    "证明": ["reasoning", "math"],
    "proof": ["reasoning", "math"],
    # Writing / language quality
    "写作": ["writing", "general"],
    "改写": ["writing", "general"],
    "润色": ["writing", "general"],
    "rewrite": ["writing", "general"],
    "writing": ["writing"],
    "去AI": ["writing", "general"],
    "polish": ["writing"],
    # Code
    "代码": ["code"],
    "code": ["code"],
    "编程": ["code"],
    "debug": ["code"],
    "调试": ["code"],
    # Speed / cost-sensitive
    "快速": ["fast"],
    "fast": ["fast"],
    "分类": ["fast"],
    "classify": ["fast"],
    "摘要": ["fast"],
    "summary": ["fast"],
    "summarize": ["fast"],
    # Chinese-specific
    "中文": ["chinese"],
    "chinese": ["chinese"],
    # General / multimodal
    "通用": ["general"],
    "general": ["general"],
    "多模态": ["multimodal"],
    "multimodal": ["multimodal"],
    "图片": ["multimodal"],
    "image": ["multimodal"],
}


class ModelSuggester:
    """
    Suggests models based on task description keywords matched against model tags.

    This class does NOT auto-switch models. It returns a formatted suggestion
    string for the user to review and decide whether to switch.

    Usage:
        suggester = ModelSuggester()
        suggestion = suggester.suggest("需要深度推理审稿", available_models)
        # Returns a formatted string like:
        # "建议使用: DeepSeek R1 (deepseek-r1-friday) [high]
        #   匹配标签: reasoning
        #   其他候选: GPT-4.1 (gpt-4.1) [high]"
    """

    def __init__(
        self,
        keyword_tags: dict[str, list[str]] | None = None,
        cost_preference: str | None = None,
    ):
        """
        Args:
            keyword_tags: Custom keyword→tags mapping. Defaults to TASK_KEYWORD_TAGS.
            cost_preference: Optional cost tier preference ("low", "medium", "high").
                If set, models matching this tier are ranked higher among equal-tag matches.
        """
        self._keyword_tags = keyword_tags or TASK_KEYWORD_TAGS
        self._cost_preference = cost_preference

    def suggest(
        self,
        task_description: str,
        available_models: list["ModelInfo"],
        current_model_id: str | None = None,
    ) -> str:
        """
        Suggest the best model for a given task description.

        Matching algorithm:
            1. Extract relevant tags from task_description via keyword matching.
            2. Score each model by counting how many of its tags overlap with
               the extracted tags.
            3. Apply cost_preference bonus if set.
            4. Exclude the current model from the top suggestion (but include
               it in "other candidates" if it scores well).
            5. Return formatted suggestion string.

        Args:
            task_description: Natural language description of the task.
            available_models: List of ModelInfo objects from SessionModelManager.
            current_model_id: Currently active model ID (excluded from top pick).

        Returns:
            Formatted suggestion string. Returns a "no suggestion" message if
            no models match or if the task description is empty.
        """
        if not task_description.strip() or not available_models:
            return "无法提供建议：请描述你的任务需求。"

        # Step 1: Extract target tags from task description
        target_tags = self._extract_tags(task_description)

        if not target_tags:
            return (
                "未能从任务描述中识别出明确的模型需求。\n"
                "提示：尝试描述任务类型，如「深度推理」「快速分类」「中文写作」等。"
            )

        # Step 2: Score models
        scored = self._score_models(available_models, target_tags, current_model_id)

        if not scored:
            return "当前可用模型均不匹配该任务需求。"

        # Step 3: Format suggestion
        return self._format_suggestion(scored, target_tags, current_model_id)

    def _extract_tags(self, task_description: str) -> set[str]:
        """
        Extract relevant model tags from task description via keyword matching.

        For ASCII-only keywords (e.g., "code", "fast", "math"), uses word boundary
        matching (\\b) to avoid false positives like "unicode" matching "code".
        For keywords containing non-ASCII characters (Chinese), uses simple substring
        matching since Chinese text has no word boundaries.
        """
        desc_lower = task_description.lower()
        tags: set[str] = set()
        for keyword, keyword_tags in self._keyword_tags.items():
            kw_lower = keyword.lower()
            if kw_lower.isascii():
                # Use word boundary for English keywords to avoid substring false positives
                if re.search(r"\b" + re.escape(kw_lower) + r"\b", desc_lower):
                    tags.update(keyword_tags)
            else:
                # Chinese keywords: simple substring match (no word boundaries)
                if kw_lower in desc_lower:
                    tags.update(keyword_tags)
        return tags

    def _score_models(
        self,
        models: list["ModelInfo"],
        target_tags: set[str],
        current_model_id: str | None,
    ) -> list[tuple["ModelInfo", int, set[str]]]:
        """
        Score models by tag overlap. Returns sorted list of (model, score, matched_tags).

        Scoring:
            - +1 per matching tag
            - +1 bonus if cost_preference matches model's cost_tier AND model
              already has at least one tag match (bonus never creates a match
              from nothing)
            - Models with score 0 are excluded
        """
        results: list[tuple["ModelInfo", int, set[str]]] = []

        for model in models:
            model_tags = set(model.tags)
            matched = model_tags & target_tags
            score = len(matched)

            # Cost preference bonus — only applies when there's already a tag match
            if (
                score > 0
                and self._cost_preference
                and model.cost_tier == self._cost_preference
            ):
                score += 1

            if score > 0:
                results.append((model, score, matched))

        # Sort by score descending, then by cost_tier (high first for quality)
        tier_order = {"high": 0, "medium": 1, "low": 2}
        results.sort(key=lambda x: (-x[1], tier_order.get(x[0].cost_tier, 1)))

        return results

    def _format_suggestion(
        self,
        scored: list[tuple["ModelInfo", int, set[str]]],
        target_tags: set[str],
        current_model_id: str | None,
    ) -> str:
        """Format the scored models into a user-friendly suggestion string."""
        lines: list[str] = []

        # Find top pick (excluding current model)
        top_pick = None
        others: list[tuple["ModelInfo", int, set[str]]] = []

        for model, score, matched in scored:
            if model.id == current_model_id:
                others.append((model, score, matched))
            elif top_pick is None:
                top_pick = (model, score, matched)
            else:
                others.append((model, score, matched))

        if top_pick is None:
            # All matches are the current model
            model, score, matched = scored[0]
            return f"当前模型 {model.display_name} 已是最佳匹配（标签: {', '.join(sorted(matched))}）。"

        model, score, matched = top_pick
        lines.append(
            f"建议使用: {model.display_name} ({model.id}) [{model.cost_tier}]"
        )
        lines.append(f"  匹配标签: {', '.join(sorted(matched))}")
        lines.append(f"  任务关键词匹配: {', '.join(sorted(target_tags))}")

        # Show up to 2 other candidates
        other_candidates = [
            (m, s, mt) for m, s, mt in others if m.id != current_model_id
        ][:2]
        if other_candidates:
            lines.append("  其他候选:")
            for m, s, mt in other_candidates:
                lines.append(
                    f"    - {m.display_name} ({m.id}) [{m.cost_tier}] "
                    f"匹配: {', '.join(sorted(mt))}"
                )

        lines.append("")
        lines.append("如需切换，请使用 switch_model 工具或输入 `switch <model_id>`。")

        return "\n".join(lines)
