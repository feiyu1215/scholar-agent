"""
tools/deai/verify.py - Self-check / verification logic (Four-Layer Self-Check Protocol).

L1: Structure fingerprint
L2: Rhythm & burstiness
L3: Forbidden patterns (zero tolerance)
L4: Voice consistency
"""

from __future__ import annotations

import re
import statistics
from typing import List, Dict, Optional

from utils.voice_profile import load_voice_profile, check_voice_drift

from tools.deai.constants import (
    STRUCTURE_PATTERNS,
    FORBIDDEN_PATTERNS,
    DIMENSION_FLOOR,
    SelfCheckResult,
    SelfCheckReport,
)
from tools.deai.signals import check_burstiness


def _check_structure_fingerprint(text: str) -> SelfCheckResult:
    """L1: Detect macro-level AI structural patterns."""
    violations = []
    total_checks = len(STRUCTURE_PATTERNS)
    triggered = 0

    for pattern_name, pattern in STRUCTURE_PATTERNS.items():
        matches = pattern.findall(text)
        if len(matches) >= 2:  # Multiple occurrences = suspicious
            triggered += 1
            violations.append(f"{pattern_name}: {len(matches)} occurrences")

    # Check paragraph length uniformity
    paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 20]
    if len(paragraphs) >= 3:
        para_lengths = [len(p.split()) for p in paragraphs]
        if para_lengths:
            para_mean = statistics.mean(para_lengths)
            para_std = statistics.stdev(para_lengths) if len(para_lengths) > 1 else 0
            para_cv = para_std / para_mean if para_mean > 0 else 0
            if para_cv < 0.25:
                triggered += 1
                violations.append(f"paragraph_uniformity: CV={para_cv:.2f} (need >=0.25)")
                total_checks += 1
            else:
                total_checks += 1

    score = 1.0 - (triggered / max(total_checks, 1))
    return SelfCheckResult(
        layer="L1",
        layer_name="Structure",
        passed=score >= 0.7,
        score=score,
        violations=violations,
        details={"patterns_triggered": triggered, "total_checked": total_checks},
    )


def _check_rhythm(text: str) -> SelfCheckResult:
    """L2: Sentence-level rhythm check via burstiness."""
    result = check_burstiness(text, min_cv=0.35)
    violations = []
    if not result["passed"]:
        violations.append(result["warning"])

    # Additional: check for repeated sentence openers
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text.strip())
    if len(sentences) >= 5:
        openers = [s.split()[0].lower() if s.split() else "" for s in sentences]
        # Count consecutive duplicate openers
        consecutive_dupes = 0
        for i in range(1, len(openers)):
            if openers[i] == openers[i-1] and openers[i] not in ("", "the", "a"):
                consecutive_dupes += 1
        if consecutive_dupes >= 3:
            violations.append(
                f"Repeated sentence openers: {consecutive_dupes} consecutive duplicates"
            )

    score = result["cv"] / 0.50 if result["cv"] < 0.50 else 1.0  # Normalize to 1.0
    score = min(1.0, max(0.0, score))

    return SelfCheckResult(
        layer="L2",
        layer_name="Rhythm",
        passed=result["passed"] and len(violations) <= 1,
        score=score,
        violations=violations,
        details={"cv": result["cv"], "mean_length": result["mean_length"], "unit": result["unit"]},
    )


def _check_forbidden(text: str) -> SelfCheckResult:
    """L3: Zero-tolerance forbidden pattern check."""
    violations = []

    for pattern, label in FORBIDDEN_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            violations.append(f"{label} (×{len(matches)})")

    score = 1.0 if not violations else max(0.0, 1.0 - len(violations) * 0.15)

    return SelfCheckResult(
        layer="L3",
        layer_name="Forbidden",
        passed=len(violations) == 0,
        score=score,
        violations=violations,
        details={"total_forbidden_hits": len(violations)},
    )


def _check_voice_consistency(text: str, original_text: str = None) -> SelfCheckResult:
    """L4: Voice drift detection — is the fix still in author's voice?"""
    voice_fp = load_voice_profile()
    violations = []

    if voice_fp.total_words_analyzed == 0:
        # No voice profile built — skip with pass
        return SelfCheckResult(
            layer="L4",
            layer_name="Voice",
            passed=True,
            score=0.8,  # Conservative: can't fully assess
            violations=["No voice profile available — skipped"],
            details={"voice_profile_available": False},
        )

    if original_text:
        drift = check_voice_drift(original_text, text, voice_fp)
        if drift.get("drift_detected"):
            for warning in drift.get("warnings", []):
                violations.append(f"Voice drift: {warning}")

    # Check specific voice metrics
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text.strip())
    word_counts = [len(s.split()) for s in sentences if len(s.split()) >= 4]

    if word_counts:
        current_avg = statistics.mean(word_counts)
        expected_avg = voice_fp.avg_sentence_length
        if expected_avg > 0:
            deviation = abs(current_avg - expected_avg) / expected_avg
            if deviation > 0.3:  # More than 30% deviation
                violations.append(
                    f"Avg sentence length: {current_avg:.0f} words "
                    f"(expected ~{expected_avg:.0f}, deviation {deviation:.0%})"
                )

    score = max(0.0, 1.0 - len(violations) * 0.25)
    return SelfCheckResult(
        layer="L4",
        layer_name="Voice",
        passed=len(violations) == 0,
        score=score,
        violations=violations,
        details={"voice_profile_available": True},
    )


def run_self_check(
    text: str,
    original_text: str = None,
) -> SelfCheckReport:
    """Run all 4 layers of self-check on text.

    Layer weights for overall score:
        L1 Structure:  0.20
        L2 Rhythm:     0.25
        L3 Forbidden:  0.30 (highest — zero tolerance is critical)
        L4 Voice:      0.25
    """
    checks = [
        _check_structure_fingerprint(text),
        _check_rhythm(text),
        _check_forbidden(text),
        _check_voice_consistency(text, original_text),
    ]

    weights = [0.20, 0.25, 0.30, 0.25]
    overall = sum(c.score * w for c, w in zip(checks, weights))

    blocking = [c.layer for c in checks if not c.passed]

    return SelfCheckReport(
        all_passed=all(c.passed for c in checks),
        overall_score=overall,
        layers=checks,
        blocking_layers=blocking,
    )
