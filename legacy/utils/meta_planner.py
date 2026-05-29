"""
Meta-Planner — Plan optimization through learned experience.

Instead of relying solely on LLM reasoning for tool sequencing,
the meta-planner augments planning with historical data:

1. Suggests effective tool sequences based on past positive patterns
2. Warns against sequences that historically produced poor outcomes
3. Recommends parallel execution when patterns show independence
4. Estimates time/cost based on historical tool durations

This module doesn't MAKE plans — it ADVISES the planning process
by injecting historical context into the prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from utils.session_memory import SessionMemory, ToolPattern


@dataclass
class PlanAdvice:
    """Advice for the planning process based on historical patterns."""
    recommended_sequence: Optional[list[str]] = None
    avoid_sequences: list[list[str]] = None
    parallel_candidates: list[str] = None
    estimated_steps: int = 0
    confidence: float = 0.0
    reasoning: str = ""

    def __post_init__(self):
        if self.avoid_sequences is None:
            self.avoid_sequences = []
        if self.parallel_candidates is None:
            self.parallel_candidates = []


# Tools that are known to be safely parallelizable
PARALLELIZABLE_TOOLS = {
    "run_single_reviewer",      # Multiple reviewers can run in parallel
    "rewrite_section",          # Different sections can be rewritten simultaneously
    "verify_citations",         # Citation checks are independent
    "consistency_check",        # Can run alongside other checks
}

# Tool dependency graph: tool → tools it typically depends on
TOOL_DEPENDENCIES = {
    "rewrite_section": ["read_section", "review_paper"],
    "deai_closed_loop": ["rewrite_section", "build_voice_profile"],
    "verify_citations": ["parse_paper"],
    "review_paper": ["parse_paper"],
    "consistency_check": ["rewrite_section"],
}


class MetaPlanner:
    """Provides plan optimization advice based on learned patterns.

    Usage:
        planner = MetaPlanner(session_memory)
        advice = planner.get_plan_advice(goal_description, current_phase)
        # Inject advice.reasoning into the LLM planning prompt
    """

    def __init__(self, memory: SessionMemory):
        self._memory = memory

    def get_plan_advice(self, goal: str, phase: str = "",
                        available_tools: list[str] = None) -> PlanAdvice:
        """Generate planning advice based on historical patterns.

        Args:
            goal: What the user wants to accomplish
            phase: Current workflow phase
            available_tools: Tools currently available (after phase filtering)

        Returns:
            PlanAdvice with recommendations
        """
        # Get relevant positive patterns from memory
        patterns = self._memory.get_effective_patterns(context=goal)

        # Check for patterns to avoid (independent of positive patterns)
        negative_patterns = [
            p for p in self._memory._patterns
            if p.outcome == "negative" and p.usage_count >= 2
        ]
        avoid = [p.tool_sequence for p in negative_patterns[:3]]

        if not patterns and not avoid:
            return PlanAdvice(
                reasoning="No historical patterns available. Using default planning.",
                confidence=0.0,
            )

        # Find best matching positive pattern
        best_pattern = patterns[0] if patterns else None

        # Determine parallel candidates
        parallel = []
        if best_pattern:
            for tool in best_pattern.tool_sequence:
                if tool in PARALLELIZABLE_TOOLS:
                    parallel.append(tool)

        # Estimate step count from patterns
        avg_steps = 0
        if patterns:
            avg_steps = int(sum(len(p.tool_sequence) for p in patterns) / len(patterns))

        # Build reasoning text for prompt injection
        reasoning_parts = []
        if best_pattern:
            reasoning_parts.append(
                f"Historical pattern suggests: {' → '.join(best_pattern.tool_sequence)} "
                f"(used {best_pattern.usage_count}x, avg improvement +{best_pattern.score_delta:.1f})"
            )
        if avoid:
            reasoning_parts.append(
                f"Avoid: {'; '.join(' → '.join(seq) for seq in avoid)} "
                f"(historically produced poor outcomes)"
            )
        if parallel:
            reasoning_parts.append(
                f"Consider parallelizing: {', '.join(parallel)}"
            )

        return PlanAdvice(
            recommended_sequence=best_pattern.tool_sequence if best_pattern else None,
            avoid_sequences=avoid,
            parallel_candidates=parallel,
            estimated_steps=avg_steps,
            confidence=min(0.9, best_pattern.usage_count / 10) if best_pattern else 0.0,
            reasoning=" | ".join(reasoning_parts) if reasoning_parts else "No specific advice.",
        )

    def get_context_injection(self, goal: str, phase: str = "") -> str:
        """Generate context text for injection into planning prompts.

        Returns empty string if no useful advice available.
        """
        advice = self.get_plan_advice(goal, phase)
        if advice.confidence < 0.2:
            return ""

        lines = ["[Meta-Planner Advice (from historical patterns)]"]
        if advice.reasoning:
            lines.append(advice.reasoning)

        # Add preference context
        pref_ctx = self._memory.get_preferences_for_prompt()
        if pref_ctx:
            lines.append(pref_ctx)

        return "\n".join(lines)

    def on_plan_complete(self, tool_sequence: list[str], goal: str,
                         score_delta: float, success: bool):
        """Record plan completion for future learning.

        Called when a plan finishes execution (success or failure).
        """
        outcome = "positive" if success and score_delta > 0 else (
            "negative" if not success else "neutral"
        )
        self._memory.record_tool_sequence(
            sequence=tool_sequence,
            outcome=outcome,
            score_delta=score_delta,
            context=goal[:100],
            notes=f"Plan {'succeeded' if success else 'failed'}, delta={score_delta:.2f}",
        )
