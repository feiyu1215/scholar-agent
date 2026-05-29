"""
utils/token_budget.py — Per-issue token budget calculator.

Prevents output bloat by dynamically setting max_tokens based on:
- The original section length (output shouldn't be drastically larger)
- The issue's fix_complexity (sentence-level fix needs far fewer tokens)

Design: This is a defensive mechanism. Budgets are generous enough to never
clip a good rewrite, but tight enough to catch runaway generation.
"""

from typing import Tuple

# Multiplier per complexity level (output tokens relative to original)
COMPLEXITY_MULTIPLIER = {
    "sentence_level": 1.2,     # Tiny fix: output ≈ original size
    "paragraph_level": 1.5,    # Moderate: allow some expansion
    "section_level": 1.8,      # Full rewrite: may restructure
    "cross_section": 2.0,      # Rare: cross-references may expand
}

# Hard bounds (regardless of calculation)
ABSOLUTE_MIN = 500
ABSOLUTE_MAX = 4000

# Extra buffer for formatting overhead (markdown markers, instructions echo, etc.)
FORMAT_BUFFER = 200


def calculate_max_tokens(
    original_text: str,
    fix_complexity: str = "paragraph_level",
) -> int:
    """
    Calculate max_tokens for a rewrite call.

    Logic: (estimated_original_tokens * multiplier) + buffer, clamped to [MIN, MAX].

    Args:
        original_text: The original section text being rewritten.
        fix_complexity: From the issue's fix_complexity field.

    Returns:
        max_tokens value to pass to the LLM call.
    """
    # Rough estimate: ~4 chars per token for English academic text
    estimated_original_tokens = len(original_text) // 4

    multiplier = COMPLEXITY_MULTIPLIER.get(fix_complexity, 1.5)
    budget = int(estimated_original_tokens * multiplier) + FORMAT_BUFFER

    return max(ABSOLUTE_MIN, min(budget, ABSOLUTE_MAX))


def check_output_completeness(output: str, finish_reason: str = "stop") -> Tuple[bool, str]:
    """
    Check if LLM output was truncated.

    Returns:
        (is_complete, reason)
        is_complete=False means the output was cut off and should not be used.
    """
    if finish_reason == "length":
        return False, "truncated_by_max_tokens"

    # Heuristic: check if output ends mid-sentence
    stripped = output.rstrip()
    if stripped and stripped[-1] not in '.!?")\']…':
        # Might be cut off mid-sentence (but could also be a heading/list)
        # Only flag if it's long enough that truncation is plausible
        if len(stripped) > 200:
            return False, "possibly_incomplete_ending"

    return True, "ok"
