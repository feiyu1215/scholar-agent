"""
tools/deai/perplexity.py - Perplexity-aware detection for de-AI engine.

Detects low-perplexity (highly predictable) text regions that are strong
indicators of AI generation. Uses n-gram frequency analysis as a zero-LLM-cost
proxy for true language model perplexity.

Key insight from deai_rules.md rule G9/S1-3:
"Avoid always choosing the most likely next word. Occasionally choose a
semantically equivalent but less common alternative to break token-prediction
patterns. NOT about using rare words."

This module provides:
1. Bigram predictability scoring per sentence
2. Low-perplexity region detection (sentences that are "too smooth")
3. Priority ranking for rewrite targets (most predictable → fix first)
"""
from __future__ import annotations

import re
import math
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
from collections import Counter


# ─── Common academic bigrams (high frequency → low perplexity when overused) ──
# These are bigrams that AI models produce at very high rates in academic text.
# Human writers use them too, but less uniformly and with more variation.

_HIGH_FREQ_BIGRAMS_EN = {
    # Verb + prep patterns AI loves
    ("plays", "a"): 0.8, ("plays", "an"): 0.8,
    ("serves", "as"): 0.9, ("acts", "as"): 0.7,
    ("aims", "to"): 0.6, ("seeks", "to"): 0.5,
    ("tends", "to"): 0.4, ("leads", "to"): 0.4,
    # AI-preferred collocations
    ("significant", "impact"): 0.7,
    ("significant", "role"): 0.7,
    ("crucial", "role"): 0.8,
    ("pivotal", "role"): 0.9,
    ("plays", "crucial"): 0.8,
    ("comprehensive", "analysis"): 0.7,
    ("comprehensive", "understanding"): 0.8,
    ("comprehensive", "overview"): 0.8,
    ("in-depth", "analysis"): 0.7,
    ("thorough", "understanding"): 0.6,
    ("notable", "exception"): 0.5,
    # Transition bigrams
    ("it", "is"): 0.3,  # Only suspicious in certain patterns
    ("this", "demonstrates"): 0.6,
    ("this", "highlights"): 0.7,
    ("this", "underscores"): 0.9,
    ("this", "suggests"): 0.4,
    ("these", "findings"): 0.5,
    ("these", "results"): 0.4,
    # Academic cliché bigrams
    ("shed", "light"): 0.9,
    ("pave", "the"): 0.8,  # pave the way
    ("stands", "as"): 0.8,
    ("remains", "a"): 0.4,
    ("offers", "a"): 0.4,
    ("provides", "a"): 0.3,
    ("represents", "a"): 0.5,
}

_HIGH_FREQ_BIGRAMS_ZH = {
    # AI-preferred Chinese collocations
    ("具有", "重要"): 0.8,
    ("发挥", "重要"): 0.7,
    ("起到", "关键"): 0.7,
    ("提供", "了"): 0.3,
    ("实现", "了"): 0.3,
    ("取得", "了"): 0.3,
    ("进行", "了"): 0.6,  # Nominalization pattern
    ("开展", "了"): 0.5,
    ("显著", "提升"): 0.6,
    ("显著", "提高"): 0.6,
    ("有效", "提升"): 0.5,
    ("深入", "探讨"): 0.7,
    ("系统", "研究"): 0.4,
    ("全面", "分析"): 0.6,
    ("深入", "分析"): 0.5,
}


@dataclass
class PerplexityScore:
    """Perplexity analysis result for a single sentence."""
    sentence: str
    score: float  # 0.0 = maximally predictable (AI), 1.0 = maximally surprising (human)
    bigram_hits: int  # Number of high-frequency bigrams found
    predictability_ratio: float  # Fraction of bigrams that are "too common"
    location: int = 0  # Sentence index in text

    @property
    def is_low_perplexity(self) -> bool:
        """True if this sentence is suspiciously predictable."""
        return self.score < 0.4

    @property
    def rewrite_priority(self) -> int:
        """Higher = more urgent to rewrite. Range 0-10."""
        if self.score < 0.2:
            return 10
        elif self.score < 0.3:
            return 8
        elif self.score < 0.4:
            return 6
        elif self.score < 0.5:
            return 4
        return 2


@dataclass
class PerplexityReport:
    """Aggregate perplexity analysis for a full text."""
    sentences: List[PerplexityScore] = field(default_factory=list)
    overall_score: float = 0.0  # Mean perplexity across sentences
    low_perplexity_count: int = 0  # Number of suspicious sentences
    rewrite_targets: List[PerplexityScore] = field(default_factory=list)

    @property
    def needs_rewrite(self) -> bool:
        """True if text has enough low-perplexity regions to warrant action."""
        if not self.sentences:
            return False
        ratio = self.low_perplexity_count / len(self.sentences)
        return ratio >= 0.3 or self.low_perplexity_count >= 3

    def get_top_targets(self, n: int = 5) -> List[PerplexityScore]:
        """Get the N most urgent rewrite targets."""
        sorted_targets = sorted(
            self.rewrite_targets,
            key=lambda s: s.score,
        )
        return sorted_targets[:n]

    def summary(self) -> str:
        """Human-readable summary."""
        if not self.sentences:
            return "Perplexity: no sentences to analyze"
        pct = (self.low_perplexity_count / len(self.sentences)) * 100
        status = "⚠️ LOW" if self.needs_rewrite else "✓ OK"
        return (
            f"Perplexity: {status} (mean={self.overall_score:.2f}, "
            f"low_ppl={self.low_perplexity_count}/{len(self.sentences)} [{pct:.0f}%])"
        )


def _tokenize_en(text: str) -> List[str]:
    """Simple English tokenizer: lowercase words, strip punctuation."""
    return [w.lower() for w in re.findall(r"\b[a-zA-Z]+(?:-[a-zA-Z]+)?\b", text)]


def _tokenize_zh(text: str) -> List[str]:
    """Simple Chinese tokenizer: extract 2-char segments (pseudo-bigrams)."""
    # Remove punctuation and spaces
    clean = re.sub(r'[\s\u3000，。！？、；：\u201c\u201d\u2018\u2019（）《》【】…—·.,!?;:"\'\(\)\[\]]', '', text)
    # Extract overlapping 2-grams
    return [clean[i:i+2] for i in range(len(clean) - 1)] if len(clean) >= 2 else []


def _compute_sentence_perplexity_en(tokens: List[str]) -> Tuple[float, int, float]:
    """Compute proxy perplexity score for an English sentence.
    
    Returns (score, bigram_hits, predictability_ratio).
    Score: 0.0 = maximally predictable, 1.0 = maximally varied.
    """
    if len(tokens) < 3:
        return (0.7, 0, 0.0)  # Too short to judge

    # Count high-frequency bigram hits
    hits = 0
    total_bigrams = len(tokens) - 1
    hit_weights: List[float] = []

    for i in range(len(tokens) - 1):
        bigram = (tokens[i], tokens[i + 1])
        if bigram in _HIGH_FREQ_BIGRAMS_EN:
            hits += 1
            hit_weights.append(_HIGH_FREQ_BIGRAMS_EN[bigram])

    # Also check for repetitive patterns (same word structure)
    # AI tends to produce "X of Y", "Z of W" patterns repeatedly
    prep_patterns = Counter()
    for i in range(len(tokens) - 2):
        if tokens[i + 1] in {"of", "in", "for", "with", "on", "to", "as", "by"}:
            prep_patterns[tokens[i + 1]] += 1

    # Penalty for over-reliance on single preposition pattern
    prep_penalty = 0.0
    for prep, count in prep_patterns.items():
        if count >= 3:
            prep_penalty += 0.1 * (count - 2)

    # Compute score
    if total_bigrams == 0:
        return (0.7, 0, 0.0)

    predictability_ratio = hits / total_bigrams
    avg_weight = sum(hit_weights) / len(hit_weights) if hit_weights else 0.0

    # Score formula: start at 1.0, subtract penalties
    # Base penalty from ratio and weight
    score = 1.0 - (predictability_ratio * 0.6) - (avg_weight * 0.3) - prep_penalty
    # Additional penalty for absolute hit count (compensates for long sentences
    # where ratio stays low despite multiple AI-pattern hits)
    if hits >= 2:
        score -= 0.15 * (hits - 1)
    score = max(0.0, min(1.0, score))

    return (round(score, 3), hits, round(predictability_ratio, 3))


def _compute_sentence_perplexity_zh(text: str) -> Tuple[float, int, float]:
    """Compute proxy perplexity score for a Chinese sentence.
    
    Uses character bigram matching against known AI-preferred collocations.
    """
    # Extract word-level tokens for bigram matching
    # For Chinese, we use a simple 2-character sliding window approach
    clean = re.sub(r'[\s\u3000，。！？、；：""''（）《》【】…—·]', '', text)
    if len(clean) < 4:
        return (0.7, 0, 0.0)

    # Check known AI bigrams
    hits = 0
    hit_weights: List[float] = []
    total_checks = 0

    # Sliding window over 2-char segments
    for i in range(len(clean) - 3):
        seg1 = clean[i:i+2]
        seg2 = clean[i+2:i+4]
        bigram = (seg1, seg2)
        total_checks += 1
        if bigram in _HIGH_FREQ_BIGRAMS_ZH:
            hits += 1
            hit_weights.append(_HIGH_FREQ_BIGRAMS_ZH[bigram])

    if total_checks == 0:
        return (0.7, 0, 0.0)

    predictability_ratio = hits / total_checks
    avg_weight = sum(hit_weights) / len(hit_weights) if hit_weights else 0.0

    score = 1.0 - (predictability_ratio * 0.7) - (avg_weight * 0.2)
    score = max(0.0, min(1.0, score))

    return (round(score, 3), hits, round(predictability_ratio, 3))


def analyze_perplexity(text: str, is_chinese: Optional[bool] = None) -> PerplexityReport:
    """Analyze text for low-perplexity (high predictability) regions.
    
    This is a zero-LLM-cost proxy for true language model perplexity.
    Uses n-gram frequency analysis to identify sentences that are
    "too smooth" — likely generated by choosing the most probable next token.
    
    Args:
        text: The text to analyze.
        is_chinese: Override language detection. If None, auto-detects.
    
    Returns:
        PerplexityReport with per-sentence scores and rewrite targets.
    """
    from tools.deai.scene import _is_chinese_text

    if is_chinese is None:
        is_chinese = _is_chinese_text(text)

    # Split into sentences
    if is_chinese:
        sentences = [s.strip() for s in re.split(r'[。！？]', text) if s.strip() and len(s.strip()) >= 6]
    else:
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text.strip())
        sentences = [s for s in sentences if s.strip() and len(s.split()) >= 5]

    scores: List[PerplexityScore] = []

    for i, sent in enumerate(sentences):
        if is_chinese:
            score, hits, ratio = _compute_sentence_perplexity_zh(sent)
        else:
            tokens = _tokenize_en(sent)
            score, hits, ratio = _compute_sentence_perplexity_en(tokens)

        ps = PerplexityScore(
            sentence=sent[:200],  # Truncate for storage
            score=score,
            bigram_hits=hits,
            predictability_ratio=ratio,
            location=i,
        )
        scores.append(ps)

    # Compute aggregates
    low_ppl = [s for s in scores if s.is_low_perplexity]
    overall = sum(s.score for s in scores) / len(scores) if scores else 0.7

    return PerplexityReport(
        sentences=scores,
        overall_score=round(overall, 3),
        low_perplexity_count=len(low_ppl),
        rewrite_targets=low_ppl,
    )


def get_perplexity_fix_hints(report: PerplexityReport, max_hints: int = 5) -> List[Dict[str, str]]:
    """Generate fix hints for the most predictable sentences.
    
    These hints are injected into the fix prompt to guide the LLM toward
    choosing less-predictable alternatives during rewrite.
    
    Args:
        report: PerplexityReport from analyze_perplexity().
        max_hints: Maximum number of hints to return.
    
    Returns:
        List of dicts with 'sentence', 'reason', 'strategy' keys.
    """
    targets = report.get_top_targets(max_hints)
    hints: List[Dict[str, str]] = []

    for target in targets:
        hint = {
            "sentence": target.sentence[:150],
            "reason": (
                f"Low perplexity (score={target.score:.2f}): "
                f"{target.bigram_hits} predictable bigrams, "
                f"{target.predictability_ratio:.0%} of token pairs are high-frequency."
            ),
            "strategy": (
                "Choose a less-predictable but semantically equivalent word "
                "for 1-2 key positions. Break the 'best next token' pattern "
                "without using rare or unusual vocabulary."
            ),
        }
        hints.append(hint)

    return hints
