"""
tools/deai_precheck.py — L1 zero-cost De-AI pre-check (regex + statistics).

Positioned as a "confident pass" gate: only skip LLM audit when ALL signals
indicate the text is clean. This minimizes false negatives (letting AI-heavy
text pass without audit).

Design:
- Three independent signals vote: lexicon density, sentence-length uniformity, parallel structures
- ALL must pass for the text to skip LLM audit
- Any single flag triggers full LLM audit (conservative approach)
- Returns diagnostics for trace logging
"""

import re
import statistics
from typing import Tuple, List

# ============================================================
# AI Lexicon (curated from deai-writing skill + empirical observation)
# ============================================================

AI_LEXICON: List[str] = [
    # Transition/connectors (overused by AI)
    "furthermore", "moreover", "additionally", "it is worth noting",
    "it is important to note", "notably", "significantly",
    "in this context", "in this regard",
    # Inflated modifiers
    "comprehensive", "multifaceted", "nuanced", "pivotal", "crucial",
    "plays a crucial role", "serves as a testament", "paramount",
    # Structural clichés
    "delve into", "in the realm of", "landscape", "tapestry",
    "underscores", "in conclusion", "in summary",
    # Academic AI-specific
    "provides valuable insights", "sheds light on", "paves the way",
    "a growing body of", "has garnered significant attention",
    "it is imperative to", "warrants further investigation",
    "holistic understanding", "robust framework",
]

# ============================================================
# Pre-check Logic
# ============================================================


def quick_ai_precheck(text: str) -> Tuple[bool, dict]:
    """
    L1 zero-cost pre-check using regex + statistics.

    Returns:
        (needs_llm_audit: bool, diagnostics: dict)
        - True = text should go through full LLM deai_audit
        - False = text is clean enough to skip (confident pass)
    """
    words = text.split()
    word_count = len(words)

    # Too short to judge — skip audit (not worth the LLM tokens)
    if word_count < 50:
        return False, {"reason": "text_too_short", "word_count": word_count}

    diagnostics: dict = {}
    flags = 0

    # ── Signal 1: AI lexicon density ──
    text_lower = text.lower()
    ai_hits = [phrase for phrase in AI_LEXICON if phrase in text_lower]
    density = len(ai_hits) / (word_count / 100)  # per 100 words
    diagnostics["ai_lexicon_density"] = round(density, 2)
    diagnostics["ai_lexicon_hits"] = ai_hits[:5]
    if density > 0.8:
        flags += 1

    # ── Signal 2: Sentence-length uniformity (AI tends toward even lengths) ──
    sentences = re.split(r'(?<=[.!?])\s+', text)
    sentences = [s.strip() for s in sentences if len(s.strip().split()) >= 3]
    if len(sentences) >= 5:
        lengths = [len(s.split()) for s in sentences]
        mean_len = statistics.mean(lengths)
        if mean_len > 0:
            cv = statistics.stdev(lengths) / mean_len
        else:
            cv = 0
        diagnostics["sentence_length_cv"] = round(cv, 3)
        diagnostics["sentence_count"] = len(sentences)
        if cv < 0.25:  # CV < 0.25 means suspiciously uniform
            flags += 1
    else:
        diagnostics["sentence_length_cv"] = None
        diagnostics["sentence_count"] = len(sentences)

    # ── Signal 3: Parallel/list structure overuse ──
    parallel_patterns = [
        r"First(?:ly)?,.*?Second(?:ly)?,.*?Third(?:ly)?",
        r"On one hand.*?[Oo]n the other hand",
        # Multiple transition words within one passage
        r"(?:Additionally|Moreover|Furthermore).*?(?:Additionally|Moreover|Furthermore)",
    ]
    parallel_count = sum(
        1 for p in parallel_patterns if re.search(p, text, re.DOTALL | re.IGNORECASE)
    )
    diagnostics["parallel_structures"] = parallel_count
    if parallel_count >= 2:
        flags += 1

    diagnostics["total_flags"] = flags
    diagnostics["decision"] = "needs_audit" if flags > 0 else "pass"

    # Decision: only skip if ZERO flags (all signals clean)
    needs_audit = flags > 0
    return needs_audit, diagnostics
