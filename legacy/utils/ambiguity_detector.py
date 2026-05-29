"""
utils/ambiguity_detector.py — User intent ambiguity detection.

Detects when a user's message expresses uncertainty, ambiguity, or indecision
that should trigger an ask_user call rather than autonomous agent decision-making.

Design:
- Rule-based first pass (fast, no LLM cost)
- Returns a signal dict that gets injected into context
- The signal tells the LLM "you MUST call ask_user before proceeding"
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, List, Tuple


# ==============================================================
# Ambiguity Signal Patterns
# ==============================================================

# Category 1: Explicit uncertainty expressions
UNCERTAINTY_PATTERNS = [
    # Chinese
    (r"我不(太)?确定", "explicit_uncertainty", 0.9),
    (r"我不(太)?清楚", "explicit_uncertainty", 0.8),
    (r"不知道(该|应该|要)", "explicit_uncertainty", 0.85),
    (r"拿不准", "explicit_uncertainty", 0.9),
    (r"有点犹豫", "explicit_uncertainty", 0.8),
    (r"不太确定(该|应该|要|怎么)", "explicit_uncertainty", 0.9),
    (r"不好说", "explicit_uncertainty", 0.7),
    (r"可能(需要|应该).{0,10}(也可能|或者)", "dilemma", 0.85),
    # English
    (r"(?i)i('m| am) not sure", "explicit_uncertainty", 0.9),
    (r"(?i)i('m| am) uncertain", "explicit_uncertainty", 0.9),
    (r"(?i)i don'?t know (if|whether|what|which|how)", "explicit_uncertainty", 0.85),
    (r"(?i)not sure (if|whether|what|which|how)", "explicit_uncertainty", 0.85),
    (r"(?i)i('m| am) torn between", "dilemma", 0.9),
    (r"(?i)can'?t decide", "dilemma", 0.9),
    (r"(?i)what (do you|would you) (think|suggest|recommend)", "seeking_guidance", 0.8),
]

# Category 2: Dilemma / multiple options without clear preference
DILEMMA_PATTERNS = [
    (r"(A还是B|还是说|或者说|要不要)", "dilemma", 0.75),
    (r"(?i)(should i|do i need to).{0,30}(or|instead)", "dilemma", 0.8),
    (r"(两个|两种|几个|几种).{0,10}(方案|选择|方向|办法)", "dilemma", 0.8),
    (r"(?i)(option [a-z]|choice [a-z]).{0,20}(option [a-z]|choice [a-z])", "dilemma", 0.85),
    (r"(一方面|另一方面)", "dilemma", 0.7),
    (r"(?i)on (the )?one hand.{0,50}on the other", "dilemma", 0.7),
]

# Category 3: Delegation signals (user explicitly wants agent input on decision)
DELEGATION_PATTERNS = [
    (r"你(觉得|认为|建议|推荐)(呢|吗|什么)?", "seeking_guidance", 0.85),
    (r"(帮我|替我)(选|决定|判断)", "seeking_guidance", 0.9),
    (r"(?i)what('s| is) (your|the best) (recommendation|suggestion|advice)", "seeking_guidance", 0.85),
    (r"(?i)(you decide|your call|up to you)", "delegation", 0.7),  # Low score: this is explicit delegation to agent
]

# Category 4: Scope ambiguity (unclear what to work on)
SCOPE_PATTERNS = [
    (r"^(帮我|请).{0,5}(看看|检查一下|改改)$", "scope_ambiguity", 0.8),
    (r"(?i)^(help me|please).{0,10}(look at|check|fix)$", "scope_ambiguity", 0.8),
    (r"^.{0,15}(怎么办|咋办|怎么弄)$", "scope_ambiguity", 0.75),
    (r"(?i)^(what should i do|how to handle this)\??$", "scope_ambiguity", 0.75),
]

# Negative patterns: things that look ambiguous but aren't (agent should proceed)
NEGATIVE_PATTERNS = [
    r"(?i)(直接|just|go ahead|请直接|不用问我|别问我|自己决定|你自己来)",
    r"(?i)(帮我(直接)?改|直接改|全部改|改完|rewrite it|fix it|改掉)",
    r"(?i)(按你的建议|follow your advice|照你说的)",
]


@dataclass
class AmbiguitySignal:
    """Result of ambiguity detection."""
    is_ambiguous: bool
    confidence: float  # 0.0 - 1.0
    category: str  # "explicit_uncertainty", "dilemma", "seeking_guidance", "scope_ambiguity", "clear"
    matched_patterns: List[Tuple[str, float]]  # (pattern_category, score)
    injection_text: str  # Text to inject into agent context

    def __bool__(self):
        return self.is_ambiguous


def detect_ambiguity(user_message: str) -> AmbiguitySignal:
    """
    Analyze a user message for ambiguity signals.
    
    Returns an AmbiguitySignal. If is_ambiguous is True, the agent_loop
    should inject the injection_text to force ask_user behavior.
    """
    if not user_message or not user_message.strip():
        return AmbiguitySignal(
            is_ambiguous=False, confidence=0.0, category="clear",
            matched_patterns=[], injection_text="",
        )

    text = user_message.strip()

    # First check: negative patterns (user explicitly wants autonomous action)
    for neg_pattern in NEGATIVE_PATTERNS:
        if re.search(neg_pattern, text):
            return AmbiguitySignal(
                is_ambiguous=False, confidence=0.0, category="clear",
                matched_patterns=[("negative_override", 1.0)],
                injection_text="",
            )

    # Score all positive patterns
    matches: List[Tuple[str, float]] = []
    
    all_patterns = UNCERTAINTY_PATTERNS + DILEMMA_PATTERNS + DELEGATION_PATTERNS + SCOPE_PATTERNS
    for pattern, category, score in all_patterns:
        if re.search(pattern, text):
            matches.append((category, score))

    if not matches:
        return AmbiguitySignal(
            is_ambiguous=False, confidence=0.0, category="clear",
            matched_patterns=[], injection_text="",
        )

    # Compute aggregate confidence (max + bonus for multiple matches)
    max_score = max(s for _, s in matches)
    # Bonus for multiple independent categories
    categories_hit = set(c for c, _ in matches)
    category_bonus = min(0.1 * (len(categories_hit) - 1), 0.15)
    final_confidence = min(max_score + category_bonus, 1.0)

    # Determine primary category
    primary_category = max(matches, key=lambda x: x[1])[0]

    # Threshold: only trigger if confidence >= 0.75
    AMBIGUITY_THRESHOLD = 0.75
    is_ambiguous = final_confidence >= AMBIGUITY_THRESHOLD

    # Build injection text
    injection_text = ""
    if is_ambiguous:
        injection_text = _build_injection(primary_category, final_confidence, text)

    return AmbiguitySignal(
        is_ambiguous=is_ambiguous,
        confidence=final_confidence,
        category=primary_category,
        matched_patterns=matches,
        injection_text=injection_text,
    )


def _build_injection(category: str, confidence: float, user_text: str) -> str:
    """Build the context injection text that forces ask_user behavior."""
    
    base = (
        "\n\n## ⚠️ AMBIGUITY DETECTED — ask_user REQUIRED\n\n"
        f"**Signal**: User's message has ambiguity (category={category}, confidence={confidence:.2f}).\n"
        "**Rule**: When the user expresses uncertainty, indecision, or asks for your opinion on a choice, "
        "you MUST call `ask_user` to clarify BEFORE taking action.\n\n"
    )

    if category == "explicit_uncertainty":
        base += (
            "The user explicitly said they are unsure. Do NOT make the decision for them. "
            "Instead, use `ask_user` to present the available options with your analysis of each, "
            "then let the user choose.\n"
        )
    elif category == "dilemma":
        base += (
            "The user is torn between options. Use `ask_user` to:\n"
            "1. Acknowledge the dilemma\n"
            "2. Briefly analyze pros/cons of each option\n"
            "3. Ask which direction they prefer\n"
        )
    elif category == "seeking_guidance":
        base += (
            "The user is asking for your recommendation. Use `ask_user` to present your "
            "analysis and recommendation, then ask if they'd like to proceed with it or "
            "explore alternatives.\n"
        )
    elif category == "scope_ambiguity":
        base += (
            "The user's request scope is unclear. Use `ask_user` to clarify:\n"
            "- What specifically they want reviewed/fixed\n"
            "- What level of intervention they expect\n"
            "- Any particular areas of concern\n"
        )

    base += (
        "\n**Reminder**: Call `ask_user` as your FIRST tool call in this turn. "
        "Do not proceed with review/rewrite tools until the user has clarified."
    )
    return base
