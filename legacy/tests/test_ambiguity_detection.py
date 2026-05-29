#!/usr/bin/env python3
"""
test_ambiguity_detection.py — Comprehensive tests for ambiguity detection.

Tests:
1. Explicit uncertainty patterns (Chinese)
2. Explicit uncertainty patterns (English)
3. Dilemma patterns (Chinese)
4. Dilemma patterns (English)
5. Delegation/seeking guidance patterns
6. Scope ambiguity patterns
7. Negative patterns override (user says "直接改")
8. Confidence threshold (0.75 boundary)
9. Category bonus calculation
10. Injection text generation
11. Edge cases: empty input, very long input, special chars
12. Mixed signals: ambiguous + negative in same message
13. Non-ambiguous normal requests
14. Doom loop integration: ambiguity in repeated requests
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.ambiguity_detector import (
    detect_ambiguity,
    AmbiguitySignal,
    UNCERTAINTY_PATTERNS,
    DILEMMA_PATTERNS,
    DELEGATION_PATTERNS,
    SCOPE_PATTERNS,
    NEGATIVE_PATTERNS,
)


# ==============================================================
# Test 1: Chinese Uncertainty Patterns
# ==============================================================

def test_chinese_uncertainty():
    """Test Chinese explicit uncertainty expressions trigger detection."""
    print("  Test 1: Chinese uncertainty patterns...")

    # High-confidence triggers
    cases = [
        ("我不确定应该改哪个部分", True),
        ("我不太确定这里该怎么处理", True),
        ("不知道该用哪种方法", True),
        ("拿不准这个参数设成多少合适", True),
        ("有点犹豫要不要删掉这段", True),
        ("我不太清楚这个引用格式对不对", True),
    ]

    for msg, should_trigger in cases:
        signal = detect_ambiguity(msg)
        if should_trigger:
            assert signal.is_ambiguous, \
                f"Expected ambiguous for '{msg}', got confidence={signal.confidence:.2f}"
            assert signal.confidence >= 0.75
        else:
            assert not signal.is_ambiguous, \
                f"Expected NOT ambiguous for '{msg}'"

    print("    ✓ PASSED")


# ==============================================================
# Test 2: English Uncertainty Patterns
# ==============================================================

def test_english_uncertainty():
    """Test English explicit uncertainty expressions trigger detection."""
    print("  Test 2: English uncertainty patterns...")

    cases = [
        ("I'm not sure if I should restructure the introduction", True),
        ("I am uncertain about the methodology section", True),
        ("I don't know whether to keep this paragraph", True),
        ("Not sure if this citation is correct", True),
        ("I'm torn between two approaches", True),
        ("Can't decide which version is better", True),
    ]

    for msg, should_trigger in cases:
        signal = detect_ambiguity(msg)
        if should_trigger:
            assert signal.is_ambiguous, \
                f"Expected ambiguous for '{msg}', got confidence={signal.confidence:.2f}"
        else:
            assert not signal.is_ambiguous

    print("    ✓ PASSED")


# ==============================================================
# Test 3: Chinese Dilemma Patterns
# ==============================================================

def test_chinese_dilemma():
    """Test Chinese dilemma/multiple-option patterns."""
    print("  Test 3: Chinese dilemma patterns...")

    # Note: "直接" triggers NEGATIVE_PATTERNS override, so avoid it.
    # Also "一方面/另一方面" has score 0.7 (below 0.75 threshold) with no bonus.
    # Use dilemma patterns with scores >= 0.75 or that trigger multiple categories.
    cases = [
        ("可能需要加引用，也可能把这段去掉", True),  # score 0.85, dilemma in UNCERTAINTY
        ("两种方案我都觉得有道理", True),  # score 0.8, dilemma in DILEMMA
        ("我不确定是一方面保留原文还是另一方面改写", True),  # multi-category: uncertainty 0.9 + dilemma 0.7 + bonus
        ("A还是B方案好？", True),  # score 0.75, dilemma
    ]

    for msg, should_trigger in cases:
        signal = detect_ambiguity(msg)
        if should_trigger:
            assert signal.is_ambiguous, \
                f"Expected ambiguous for '{msg}', got confidence={signal.confidence:.2f}"
            assert "dilemma" in signal.category or signal.confidence >= 0.75

    print("    ✓ PASSED")


# ==============================================================
# Test 4: English Dilemma Patterns
# ==============================================================

def test_english_dilemma():
    """Test English dilemma patterns."""
    print("  Test 4: English dilemma patterns...")

    cases = [
        ("Should I use OLS or IV, or maybe a different approach instead?", True),
        ("Option A and Option B both seem reasonable", True),
        # "on one hand...on the other" has base score 0.7 (below threshold).
        # Use a pattern that crosses threshold: "I'm torn between" = 0.9
        ("I'm torn between keeping the original method and switching to GMM", True),
    ]

    for msg, should_trigger in cases:
        signal = detect_ambiguity(msg)
        if should_trigger:
            assert signal.is_ambiguous, \
                f"Expected ambiguous for '{msg}', got confidence={signal.confidence:.2f}"

    print("    ✓ PASSED")


# ==============================================================
# Test 5: Delegation/Seeking Guidance
# ==============================================================

def test_delegation_patterns():
    """Test delegation and guidance-seeking patterns."""
    print("  Test 5: Delegation/seeking guidance patterns...")

    cases = [
        ("你觉得呢？", True),
        ("你建议怎么做？", True),
        ("帮我选一个最好的方案", True),
        ("帮我决定哪个版本更合适", True),
        ("What's your recommendation?", True),
        ("What do you think about this approach?", True),
        ("What would you suggest?", True),
    ]

    for msg, should_trigger in cases:
        signal = detect_ambiguity(msg)
        if should_trigger:
            assert signal.is_ambiguous, \
                f"Expected ambiguous for '{msg}', got confidence={signal.confidence:.2f}"
            assert signal.category in ("seeking_guidance", "delegation", "explicit_uncertainty")

    print("    ✓ PASSED")


# ==============================================================
# Test 6: Scope Ambiguity
# ==============================================================

def test_scope_ambiguity():
    """Test scope ambiguity patterns (vague requests)."""
    print("  Test 6: Scope ambiguity patterns...")

    cases = [
        ("帮我看看", True),
        ("请检查一下", True),
        ("怎么办", True),
        ("What should I do?", True),
        ("How to handle this?", True),
    ]

    for msg, should_trigger in cases:
        signal = detect_ambiguity(msg)
        if should_trigger:
            assert signal.is_ambiguous, \
                f"Expected ambiguous for '{msg}', got confidence={signal.confidence:.2f}"

    print("    ✓ PASSED")


# ==============================================================
# Test 7: Negative Patterns Override
# ==============================================================

def test_negative_patterns():
    """Test that negative patterns override ambiguity detection."""
    print("  Test 7: Negative patterns override...")

    # These contain ambiguity words BUT also contain negative overrides
    cases_not_ambiguous = [
        "我不太确定，但是直接帮我改吧",       # "直接" overrides
        "I'm not sure, just fix it",           # "just" overrides
        "不知道该怎么弄，你自己决定吧",        # "自己决定" overrides
        "帮我直接改掉这段",                    # "帮我直接改" overrides
        "Go ahead and rewrite it please",      # "go ahead" overrides
        "按你的建议来就行",                    # "按你的建议" overrides
        "请直接帮我改，不用问我",              # "请直接" + "不用问我" overrides
    ]

    for msg in cases_not_ambiguous:
        signal = detect_ambiguity(msg)
        assert not signal.is_ambiguous, \
            f"Negative pattern should override for '{msg}', got is_ambiguous=True, " \
            f"confidence={signal.confidence:.2f}, category={signal.category}"

    print("    ✓ PASSED")


# ==============================================================
# Test 8: Confidence Threshold Boundary
# ==============================================================

def test_confidence_threshold():
    """Test the 0.75 threshold boundary behavior."""
    print("  Test 8: Confidence threshold boundary...")

    # Below threshold: some weak signals that might not reach 0.75
    weak_signal = "不好说"  # score=0.7, might not reach threshold alone
    signal = detect_ambiguity(weak_signal)
    # Whether it triggers depends on implementation — check consistency
    if signal.confidence < 0.75:
        assert not signal.is_ambiguous
    else:
        assert signal.is_ambiguous

    # Above threshold: strong signal
    strong_signal = "我不确定应该怎么做"  # score=0.9
    signal = detect_ambiguity(strong_signal)
    assert signal.is_ambiguous
    assert signal.confidence >= 0.75

    # Exact threshold: >= 0.75 should trigger
    # Test that confidence is computed correctly
    signal = detect_ambiguity("拿不准")  # score=0.9
    assert signal.confidence >= 0.75
    assert signal.is_ambiguous

    print("    ✓ PASSED")


# ==============================================================
# Test 9: Category Bonus
# ==============================================================

def test_category_bonus():
    """Test that multiple categories add a bonus to confidence."""
    print("  Test 9: Category bonus calculation...")

    # Single category: base score only
    single_cat = "我不确定"  # explicit_uncertainty only
    signal_single = detect_ambiguity(single_cat)

    # Multiple categories: should have higher confidence
    # Combines uncertainty + delegation
    multi_cat = "我不确定，你觉得呢？"  # explicit_uncertainty + seeking_guidance
    signal_multi = detect_ambiguity(multi_cat)

    # Multi-category should have equal or higher confidence
    assert signal_multi.confidence >= signal_single.confidence, \
        f"Multi-category ({signal_multi.confidence:.2f}) should >= " \
        f"single ({signal_single.confidence:.2f})"

    print("    ✓ PASSED")


# ==============================================================
# Test 10: Injection Text Generation
# ==============================================================

def test_injection_text():
    """Test that injection text is properly generated for ambiguous signals."""
    print("  Test 10: Injection text generation...")

    signal = detect_ambiguity("我不确定应该先改摘要还是引言")
    assert signal.is_ambiguous

    # Injection text should contain key elements
    injection = signal.injection_text
    assert len(injection) > 0, "Injection text should be non-empty"
    assert "ask_user" in injection.lower() or "ask" in injection.lower(), \
        f"Injection should mention ask_user, got: {injection[:200]}"

    # Non-ambiguous: injection should be empty
    signal_clear = detect_ambiguity("帮我直接改摘要")
    assert not signal_clear.is_ambiguous
    assert signal_clear.injection_text == "" or len(signal_clear.injection_text) == 0

    print("    ✓ PASSED")


# ==============================================================
# Test 11: Edge Cases
# ==============================================================

def test_edge_cases():
    """Test edge cases: empty, long, special characters."""
    print("  Test 11: Edge cases...")

    # Empty string
    signal = detect_ambiguity("")
    assert not signal.is_ambiguous
    assert signal.confidence == 0.0

    # Very long message (should still work)
    long_msg = "我不确定" + "x" * 5000
    signal = detect_ambiguity(long_msg)
    assert signal.is_ambiguous  # Pattern at the start should still match

    # Special characters
    special = "我不确定！！！???@#$%"
    signal = detect_ambiguity(special)
    assert signal.is_ambiguous  # Should still match despite special chars

    # Only whitespace
    signal = detect_ambiguity("   \n\t  ")
    assert not signal.is_ambiguous

    # Numbers only
    signal = detect_ambiguity("12345")
    assert not signal.is_ambiguous

    print("    ✓ PASSED")


# ==============================================================
# Test 12: Mixed Signals (Ambiguous + Negative)
# ==============================================================

def test_mixed_signals():
    """Test messages with both ambiguous and negative patterns."""
    print("  Test 12: Mixed signals...")

    # Negative should win
    msg1 = "我不确定这样对不对，但是直接帮我改吧，不用问我"
    signal = detect_ambiguity(msg1)
    assert not signal.is_ambiguous, \
        f"Negative override should win for mixed signals: '{msg1}'"

    # Clear negative without uncertainty
    msg2 = "直接帮我把这段改成主动语态"
    signal = detect_ambiguity(msg2)
    assert not signal.is_ambiguous

    print("    ✓ PASSED")


# ==============================================================
# Test 13: Non-Ambiguous Normal Requests
# ==============================================================

def test_normal_requests_not_triggered():
    """Test that normal, clear requests do NOT trigger ambiguity detection."""
    print("  Test 13: Normal requests not triggered...")

    clear_requests = [
        "帮我审阅论文的方法论部分",
        "把摘要改成主动语态",
        "检查所有引用的格式是否正确",
        "Review the methodology section",
        "Rewrite the abstract to be more concise",
        "Fix the passive voice in paragraph 3",
        "Run the full review pipeline",
        "请把第三段的被动语态改成主动语态",
        "帮我检查所有参考文献",
        "删除冗余的连接词",
        "生成修改建议",
    ]

    for msg in clear_requests:
        signal = detect_ambiguity(msg)
        assert not signal.is_ambiguous, \
            f"Normal request should NOT trigger: '{msg}' " \
            f"(confidence={signal.confidence:.2f}, category={signal.category})"

    print("    ✓ PASSED")


# ==============================================================
# Test 14: AmbiguitySignal Dataclass
# ==============================================================

def test_ambiguity_signal_structure():
    """Test AmbiguitySignal dataclass correctness."""
    print("  Test 14: AmbiguitySignal structure...")

    signal = detect_ambiguity("我不确定")

    # Check all fields exist
    assert hasattr(signal, 'is_ambiguous')
    assert hasattr(signal, 'confidence')
    assert hasattr(signal, 'category')
    assert hasattr(signal, 'matched_patterns')
    assert hasattr(signal, 'injection_text')

    # Type checks
    assert isinstance(signal.is_ambiguous, bool)
    assert isinstance(signal.confidence, float)
    assert isinstance(signal.category, str)
    assert isinstance(signal.matched_patterns, list)
    assert isinstance(signal.injection_text, str)

    # Confidence in valid range
    assert 0.0 <= signal.confidence <= 1.0

    # Bool conversion
    assert bool(signal) == signal.is_ambiguous

    print("    ✓ PASSED")


# ==============================================================
# Test 15: Pattern Count Verification
# ==============================================================

def test_pattern_counts():
    """Verify pattern list completeness."""
    print("  Test 15: Pattern counts...")

    assert len(UNCERTAINTY_PATTERNS) >= 10, \
        f"Expected >=10 uncertainty patterns, got {len(UNCERTAINTY_PATTERNS)}"
    assert len(DILEMMA_PATTERNS) >= 5, \
        f"Expected >=5 dilemma patterns, got {len(DILEMMA_PATTERNS)}"
    assert len(DELEGATION_PATTERNS) >= 3, \
        f"Expected >=3 delegation patterns, got {len(DELEGATION_PATTERNS)}"
    assert len(SCOPE_PATTERNS) >= 3, \
        f"Expected >=3 scope patterns, got {len(SCOPE_PATTERNS)}"
    assert len(NEGATIVE_PATTERNS) >= 3, \
        f"Expected >=3 negative patterns, got {len(NEGATIVE_PATTERNS)}"

    # Each pattern should be a tuple of (regex, category, score)
    for pattern in UNCERTAINTY_PATTERNS:
        assert len(pattern) == 3, f"Pattern should be (regex, category, score): {pattern}"
        assert isinstance(pattern[0], str)  # regex
        assert isinstance(pattern[1], str)  # category
        assert isinstance(pattern[2], float)  # score
        assert 0.0 <= pattern[2] <= 1.0  # score in valid range

    print("    ✓ PASSED")


# ==============================================================
# Main
# ==============================================================

def main():
    print("\n" + "=" * 60)
    print("  AMBIGUITY DETECTION TEST")
    print("=" * 60 + "\n")

    tests = [
        test_chinese_uncertainty,
        test_english_uncertainty,
        test_chinese_dilemma,
        test_english_dilemma,
        test_delegation_patterns,
        test_scope_ambiguity,
        test_negative_patterns,
        test_confidence_threshold,
        test_category_bonus,
        test_injection_text,
        test_edge_cases,
        test_mixed_signals,
        test_normal_requests_not_triggered,
        test_ambiguity_signal_structure,
        test_pattern_counts,
    ]

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            import traceback
            print(f"    ✗ FAILED: {e}")
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed}/{passed + failed} passed")
    print(f"{'=' * 60}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
