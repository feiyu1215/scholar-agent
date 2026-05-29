"""
Self-Reflection — Periodic meta-cognitive checkpoints for the agent.

The agent should reflect at key moments:
1. After completing a major phase (analysis → review transition)
2. After N consecutive tool calls without user interaction
3. When a tool returns an error or unexpected result
4. Before committing an expensive operation

Reflection is NOT an LLM call — it's a structured prompt injection that forces
the model to reason about its own progress in its next response.
"""

from __future__ import annotations

from utils.goal_tracker import GoalTracker, Phase


# Trigger conditions for self-reflection
REFLECT_EVERY_N_TOOLS = 8   # Force reflection after N tool calls without one
REFLECT_ON_PHASE_CHANGE = True  # Always reflect when phase transitions
REFLECT_ON_ERROR = True     # Reflect after tool errors

# Reflection prompt templates (injected as a system-level nudge)
REFLECTION_PROMPTS = {
    "periodic": (
        "[Self-check] You've executed {tool_count} tools since last reflection. "
        "Before your next action, briefly assess:\n"
        "1. Are you still on track toward the active goal?\n"
        "2. Is the current approach working, or should you pivot?\n"
        "3. What's the most impactful next step?\n"
        "Answer these internally (1-2 sentences each), then proceed."
    ),
    "phase_change": (
        "[Phase transition: {old_phase} → {new_phase}] "
        "You've advanced to the {new_phase} phase. Before continuing:\n"
        "1. Summarize what was accomplished in the {old_phase} phase (1 sentence)\n"
        "2. What are the key inputs/findings you're carrying forward?\n"
        "3. What's your plan for this new phase?\n"
        "State these briefly, then proceed with the appropriate tools."
    ),
    "error_recovery": (
        "[Error occurred in {tool_name}] Before retrying or trying an alternative:\n"
        "1. What went wrong? (analyze the error message)\n"
        "2. Is this recoverable, or should you try a different approach?\n"
        "3. Should you inform the user?\n"
        "Decide your recovery strategy, then act."
    ),
    "pre_expensive": (
        "[Cost check] You're about to call {tool_name}, which is expensive "
        "(estimated {cost_level}). Confirm:\n"
        "1. Is this the right tool for the current goal?\n"
        "2. Have you done the cheaper prerequisite checks?\n"
        "3. Does the user expect this level of analysis?\n"
        "If all yes, proceed. Otherwise, consider a lighter alternative."
    ),
    "goal_check": (
        "[Goal alignment check] Active goals:\n{goals_summary}\n"
        "Current phase: {phase}. "
        "Are your recent actions advancing these goals? "
        "If not, realign before continuing."
    ),
}

# Tools considered "expensive" (trigger pre_expensive reflection)
EXPENSIVE_TOOLS = {
    "review_paper": "high",
    "parallel_rewrite": "high",
    "deai_closed_loop": "high",
    "consolidate_reviews": "medium",
    "verify_and_enrich_citations": "medium",
}


class ReflectionEngine:
    """Manages when and what reflection prompts to inject.

    Does NOT make LLM calls itself — it produces text that gets injected
    into the message stream, causing the LLM to self-reflect in its next turn.
    """

    def __init__(self, tracker: GoalTracker):
        self._tracker = tracker
        self._tools_since_reflection = 0
        self._last_phase = tracker.phase
        self._reflection_count = 0

    def on_tool_complete(self, tool_name: str, had_error: bool = False) -> str | None:
        """Called after each tool execution. Returns reflection prompt if triggered, else None."""
        self._tools_since_reflection += 1

        # Check phase change
        if REFLECT_ON_PHASE_CHANGE and self._tracker.phase != self._last_phase:
            old = self._last_phase
            self._last_phase = self._tracker.phase
            self._tools_since_reflection = 0
            self._reflection_count += 1
            return REFLECTION_PROMPTS["phase_change"].format(
                old_phase=old.value,
                new_phase=self._tracker.phase.value,
            )

        # Check error
        if REFLECT_ON_ERROR and had_error:
            self._tools_since_reflection = 0
            self._reflection_count += 1
            return REFLECTION_PROMPTS["error_recovery"].format(tool_name=tool_name)

        # Check periodic
        if self._tools_since_reflection >= REFLECT_EVERY_N_TOOLS:
            self._tools_since_reflection = 0
            self._reflection_count += 1
            return REFLECTION_PROMPTS["periodic"].format(
                tool_count=REFLECT_EVERY_N_TOOLS,
            )

        return None

    def check_pre_expensive(self, tool_name: str) -> str | None:
        """Called BEFORE executing an expensive tool. Returns warning if applicable."""
        cost_level = EXPENSIVE_TOOLS.get(tool_name)
        if cost_level:
            return REFLECTION_PROMPTS["pre_expensive"].format(
                tool_name=tool_name,
                cost_level=cost_level,
            )
        return None

    def generate_goal_check(self) -> str:
        """Generate a goal alignment check prompt (called on demand)."""
        goals_summary = ""
        for g in self._tracker.goals:
            if g.status == "active":
                goals_summary += f"  - [{g.id}] {g.description}\n"
        if not goals_summary:
            goals_summary = "  (no active goals)\n"

        return REFLECTION_PROMPTS["goal_check"].format(
            goals_summary=goals_summary.rstrip(),
            phase=self._tracker.phase.value,
        )

    @property
    def reflection_count(self) -> int:
        return self._reflection_count

    def reset_counter(self):
        """Reset the periodic counter (e.g., after user message)."""
        self._tools_since_reflection = 0
