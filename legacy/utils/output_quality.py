"""
Output Quality Gate — Automatic validation of critical agent outputs.

For key operations (rewrite, de-AI, citations), the agent should verify
its own output quality before presenting to the user. This module provides:

1. Structural checks (length, format, completeness)
2. Regression detection (output worse than input)
3. Constraint satisfaction (word count targets, required sections)
4. Auto-retry recommendations when quality is below threshold

This is NOT an LLM-based check (that would be too expensive). It uses
heuristic rules that catch the most common failure modes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class QualityCheck:
    """Result of a quality gate check."""
    passed: bool
    score: float  # 0.0 - 1.0
    checks_run: int
    checks_passed: int
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    should_retry: bool = False

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"Quality: {status} ({self.score:.0%}, "
            f"{self.checks_passed}/{self.checks_run} checks) "
            + (f"| Issues: {'; '.join(self.issues)}" if self.issues else "")
        )


# Minimum quality score to pass
QUALITY_THRESHOLD = 0.6
# Maximum retries when quality gate fails
MAX_QUALITY_RETRIES = 2


class OutputQualityGate:
    """Validates agent outputs using heuristic rules.

    Usage:
        gate = OutputQualityGate()
        result = gate.check_rewrite(original, rewritten, constraints)
        if not result.passed:
            # Retry or report issues
    """

    def __init__(self):
        self._retry_count: dict[str, int] = {}  # operation_key → retries

    def check_rewrite(self, original: str, rewritten: str,
                      max_length_ratio: float = 1.5,
                      min_length_ratio: float = 0.3) -> QualityCheck:
        """Validate a rewrite output against the original.

        Checks:
        - Not empty
        - Not too short (information loss)
        - Not too long (padding/repetition)
        - No obvious template artifacts
        - Maintains paragraph structure
        - No excessive repetition
        """
        checks_run = 0
        checks_passed = 0
        issues = []
        recommendations = []

        # Check 1: Not empty
        checks_run += 1
        if rewritten and len(rewritten.strip()) > 10:
            checks_passed += 1
        else:
            issues.append("Output is empty or trivially short")
            recommendations.append("Regenerate with clearer instructions")

        if not rewritten:
            return QualityCheck(
                passed=False, score=0.0,
                checks_run=checks_run, checks_passed=checks_passed,
                issues=issues, recommendations=recommendations,
                should_retry=True,
            )

        orig_len = len(original) if original else 1
        new_len = len(rewritten)

        # Check 2: Length ratio
        checks_run += 1
        ratio = new_len / max(orig_len, 1)
        if min_length_ratio <= ratio <= max_length_ratio:
            checks_passed += 1
        else:
            if ratio < min_length_ratio:
                issues.append(f"Output too short ({ratio:.0%} of original)")
                recommendations.append("Expand with more detail from original")
            else:
                issues.append(f"Output too long ({ratio:.0%} of original)")
                recommendations.append("Reduce padding and redundancy")

        # Check 3: No template artifacts
        checks_run += 1
        artifacts = [
            "[Insert ", "[TODO", "{{", "}}", "[PLACEHOLDER",
            "Lorem ipsum", "[Your ", "[Fill in",
        ]
        has_artifacts = any(a.lower() in rewritten.lower() for a in artifacts)
        if not has_artifacts:
            checks_passed += 1
        else:
            issues.append("Contains template artifacts/placeholders")
            recommendations.append("Complete all placeholder sections")

        # Check 4: Paragraph structure maintained
        checks_run += 1
        orig_paras = len([p for p in original.split("\n\n") if p.strip()]) if original else 1
        new_paras = len([p for p in rewritten.split("\n\n") if p.strip()])
        # Allow ±50% paragraph count variation
        if orig_paras * 0.5 <= new_paras <= orig_paras * 2:
            checks_passed += 1
        else:
            issues.append(
                f"Paragraph structure changed significantly "
                f"({orig_paras} → {new_paras})"
            )

        # Check 5: No excessive repetition
        checks_run += 1
        sentences = re.split(r'[.!?。！？]\s*', rewritten)
        if len(sentences) > 3:
            unique_ratio = len(set(sentences)) / len(sentences)
            if unique_ratio > 0.7:
                checks_passed += 1
            else:
                issues.append(f"High repetition detected ({1-unique_ratio:.0%} repeated)")
                recommendations.append("Reduce repeated phrases and sentences")
        else:
            checks_passed += 1  # Too short to judge repetition

        # Check 6: Not identical to original (actual change made)
        checks_run += 1
        if original and rewritten != original:
            checks_passed += 1
        elif original:
            issues.append("Output identical to original (no changes made)")
            recommendations.append("Apply the requested changes")

        # Calculate score
        score = checks_passed / max(checks_run, 1)
        passed = score >= QUALITY_THRESHOLD

        # Determine if retry is warranted
        should_retry = not passed and score < 0.5

        return QualityCheck(
            passed=passed,
            score=score,
            checks_run=checks_run,
            checks_passed=checks_passed,
            issues=issues,
            recommendations=recommendations,
            should_retry=should_retry,
        )

    def check_deai_output(self, text: str, target_score: float = 0.7) -> QualityCheck:
        """Validate de-AI output for common failure modes.

        Checks:
        - Text is non-empty
        - No obvious AI-speak patterns remaining
        - Sentence length variation (AI tends to uniform length)
        - No forbidden patterns (as per, utilize, etc.)
        """
        checks_run = 0
        checks_passed = 0
        issues = []
        recommendations = []

        # Check 1: Non-empty
        checks_run += 1
        if text and len(text.strip()) > 20:
            checks_passed += 1
        else:
            issues.append("De-AI output is empty")
            return QualityCheck(
                passed=False, score=0.0,
                checks_run=1, checks_passed=0,
                issues=issues, should_retry=True,
            )

        # Check 2: AI-speak patterns
        checks_run += 1
        ai_patterns = [
            r'\bdelve\b', r'\bfurthermore\b', r'\bmoreover\b',
            r'\bin conclusion\b', r'\bit is worth noting\b',
            r'\bcrucial\b', r'\bpivotal\b', r'\bparamount\b',
            r'\bholistic\b', r'\bseamless\b', r'\bsynergy\b',
            r'\beverchanging\b', r'\bunderscore\b', r'\bfoster\b',
        ]
        matches = sum(1 for p in ai_patterns if re.search(p, text, re.IGNORECASE))
        if matches <= 2:  # Allow up to 2 occurrences
            checks_passed += 1
        else:
            issues.append(f"Still contains {matches} AI-speak patterns")
            recommendations.append("Replace remaining AI-typical vocabulary")

        # Check 3: Sentence length variation (burstiness)
        checks_run += 1
        sentences = [s.strip() for s in re.split(r'[.!?。！？]', text) if s.strip()]
        if len(sentences) >= 3:
            lengths = [len(s.split()) for s in sentences]
            avg_len = sum(lengths) / len(lengths)
            variance = sum((l - avg_len) ** 2 for l in lengths) / len(lengths)
            cv = (variance ** 0.5) / max(avg_len, 1)  # Coefficient of variation
            if cv > 0.3:  # Good variation
                checks_passed += 1
            else:
                issues.append("Uniform sentence lengths (low burstiness)")
                recommendations.append("Vary sentence lengths for natural rhythm")
        else:
            checks_passed += 1  # Too few sentences to judge

        # Check 4: No forbidden list-style patterns
        checks_run += 1
        list_patterns = [
            r'^\s*\d+\.\s+\w',  # Numbered lists in flowing prose
            r'^\s*[-•]\s+\w',   # Bullet points in prose
        ]
        list_matches = sum(
            1 for line in text.split("\n")
            for p in list_patterns
            if re.match(p, line)
        )
        # Allow some lists (might be intentional), flag if > 30% of lines
        lines_count = max(len(text.split("\n")), 1)
        if list_matches / lines_count < 0.3:
            checks_passed += 1
        else:
            issues.append("Excessive list formatting in prose sections")
            recommendations.append("Convert bullet points to flowing prose")

        score = checks_passed / max(checks_run, 1)
        passed = score >= QUALITY_THRESHOLD

        return QualityCheck(
            passed=passed,
            score=score,
            checks_run=checks_run,
            checks_passed=checks_passed,
            issues=issues,
            recommendations=recommendations,
            should_retry=not passed,
        )

    def check_review_output(self, review_text: str) -> QualityCheck:
        """Validate review output has substance, not boilerplate.

        Checks:
        - Minimum length
        - Contains specific issue references (not just "looks good")
        - Has actionable recommendations
        - Covers multiple aspects
        """
        checks_run = 0
        checks_passed = 0
        issues = []

        # Check 1: Minimum length
        checks_run += 1
        if len(review_text) > 200:
            checks_passed += 1
        else:
            issues.append("Review too brief to be useful")

        # Check 2: Contains specifics
        checks_run += 1
        # Look for section references, line numbers, quotes
        specifics = re.findall(
            r'section|paragraph|line|page|figure|table|equation|theorem',
            review_text, re.IGNORECASE
        )
        if len(specifics) >= 2:
            checks_passed += 1
        else:
            issues.append("Review lacks specific references to paper content")

        # Check 3: Has recommendations (not just description)
        checks_run += 1
        action_words = re.findall(
            r'should|could|recommend|suggest|consider|improve|revise|add|remove|clarify',
            review_text, re.IGNORECASE
        )
        if len(action_words) >= 2:
            checks_passed += 1
        else:
            issues.append("Review lacks actionable recommendations")

        # Check 4: Not just generic praise
        checks_run += 1
        praise_only = re.findall(
            r'well[\s-]?written|excellent|good job|no issues|looks?\s+good|perfect',
            review_text, re.IGNORECASE
        )
        has_criticism = re.findall(
            r'however|but|issue|problem|weak|unclear|confusing|missing|lack',
            review_text, re.IGNORECASE
        )
        if has_criticism or not praise_only:
            checks_passed += 1
        else:
            issues.append("Review is only praise without substantive critique")

        score = checks_passed / max(checks_run, 1)
        return QualityCheck(
            passed=score >= QUALITY_THRESHOLD,
            score=score,
            checks_run=checks_run,
            checks_passed=checks_passed,
            issues=issues,
            should_retry=score < 0.5,
        )

    def should_retry(self, operation_key: str) -> bool:
        """Check if an operation has retry budget remaining."""
        count = self._retry_count.get(operation_key, 0)
        return count < MAX_QUALITY_RETRIES

    def record_retry(self, operation_key: str):
        """Record a retry attempt."""
        self._retry_count[operation_key] = self._retry_count.get(operation_key, 0) + 1

    def reset_retries(self, operation_key: str = None):
        """Reset retry counter (after success or moving to next operation)."""
        if operation_key:
            self._retry_count.pop(operation_key, None)
        else:
            self._retry_count.clear()
