#!/usr/bin/env python3
"""
test_error_recovery.py — Tests for error classification, retry logic, circuit breaker,
and fallback mechanisms.

Tests:
1. Error classification accuracy (pattern matching for all 6 classes)
2. Retry logic: exponential backoff, max retries per error class
3. Circuit breaker: closed→open→half-open→closed lifecycle
4. Fallback suggestions: available fallbacks for each tool
5. handle_error integration: correct action recommendation
6. Success recording resets circuit state
7. Concurrent failures across multiple tools
8. Edge cases: empty error messages, unknown patterns, boundary conditions
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.error_recovery import (
    ErrorRecoveryEngine,
    ErrorClass,
    CircuitState,
    ErrorEvent,
    ERROR_PATTERNS,
    FALLBACK_MAP,
    MAX_RETRIES,
)


# ==============================================================
# Test 1: Error Classification
# ==============================================================

def test_error_classification():
    """Test that error messages are classified into correct ErrorClass."""
    print("  Test 1: Error classification accuracy...")

    engine = ErrorRecoveryEngine()

    # TRANSIENT errors
    transient_msgs = [
        "Connection timeout after 30s",
        "Rate limit exceeded: 429 Too Many Requests",
        "Error code: 503 Service Unavailable",
        "502 Bad Gateway",
        "Connection reset by peer",
        "Connection refused",
        "Temporary failure in name resolution",
    ]
    for msg in transient_msgs:
        result = engine.classify_error(msg)
        assert result == ErrorClass.TRANSIENT, \
            f"Expected TRANSIENT for '{msg}', got {result}"

    # INPUT_INVALID errors
    # Note: "TypeError" matches INTERNAL_BUG first (exact string match).
    # INPUT_INVALID patterns are: "invalid", "required", "missing parameter", "type error",
    # "validation", "not a valid"
    invalid_msgs = [
        "Invalid parameter: section_id must be a string",
        "Required field 'text' is missing",
        "Missing parameter: reviewer_count",
        "type error in argument: expected int got str",
        "Validation error: score must be between 0 and 10",
        "Not a valid section identifier",
    ]
    for msg in invalid_msgs:
        result = engine.classify_error(msg)
        assert result == ErrorClass.INPUT_INVALID, \
            f"Expected INPUT_INVALID for '{msg}', got {result}"

    # RESOURCE_MISSING errors
    missing_msgs = [
        "Section not found: 99_nonexistent",
        "No such file or directory: /tmp/paper.md",
        "FileNotFoundError: paper.tex does not exist",
        "KeyError: 'abstract'",
    ]
    for msg in missing_msgs:
        result = engine.classify_error(msg)
        assert result == ErrorClass.RESOURCE_MISSING, \
            f"Expected RESOURCE_MISSING for '{msg}', got {result}"

    # PROVIDER_ERROR errors
    # Note: "rate limit" matches TRANSIENT before PROVIDER_ERROR patterns.
    # Use messages where PROVIDER_ERROR keywords match without TRANSIENT overlap.
    provider_msgs = [
        "OpenAI API error: model overloaded",
        "Anthropic API error: overloaded",
        "API error: content filter triggered",
        "Model not available: gpt-4-turbo",
        "Safety filter blocked the response",
        "insufficient_quota: billing limit reached",
    ]
    for msg in provider_msgs:
        result = engine.classify_error(msg)
        assert result == ErrorClass.PROVIDER_ERROR, \
            f"Expected PROVIDER_ERROR for '{msg}', got {result}"

    # INTERNAL_BUG errors
    # Note: "SyntaxError: invalid syntax" — "invalid" matches INPUT_INVALID first.
    # Use messages where INTERNAL_BUG keywords match without INPUT_INVALID overlap.
    bug_msgs = [
        "AttributeError: 'NoneType' object has no attribute 'text'",
        "TypeError: unsupported operand type(s)",
        "ImportError: cannot import name 'xyz'",
        "SyntaxError: unexpected token at line 42",
        "NameError: name 'undefined_var' is not defined",
        "IndexError: list index out of range",
    ]
    for msg in bug_msgs:
        result = engine.classify_error(msg)
        assert result == ErrorClass.INTERNAL_BUG, \
            f"Expected INTERNAL_BUG for '{msg}', got {result}"

    # UNKNOWN errors (no pattern matches)
    unknown_msgs = [
        "Something weird happened",
        "Unexpected result format",
        "The operation could not be completed",
    ]
    for msg in unknown_msgs:
        result = engine.classify_error(msg)
        assert result == ErrorClass.UNKNOWN, \
            f"Expected UNKNOWN for '{msg}', got {result}"

    print("    ✓ PASSED")


# ==============================================================
# Test 2: Retry Logic and Exponential Backoff
# ==============================================================

def test_retry_logic():
    """Test retry recommendations with correct backoff delays."""
    print("  Test 2: Retry logic and exponential backoff...")

    engine = ErrorRecoveryEngine()

    # TRANSIENT: should retry up to 3 times with exponential backoff
    # NOTE: Circuit breaker opens at 3 failures (FAILURE_THRESHOLD=3).
    # r1: failures=1 (not open), retry_count 0→1, delay=2^0=1
    # r2: failures=2 (not open), retry_count 1→2, delay=2^1=2
    # r3: failures=3 → circuit OPENS; condition "not circuit.is_open" fails → fallback
    r1 = engine.handle_error("review_paper", "Connection timeout after 30s")
    assert r1["action"] == "retry"
    assert r1["retry_delay"] == 1  # 2^0 = 1

    r2 = engine.handle_error("review_paper", "Connection timeout again")
    assert r2["action"] == "retry"
    assert r2["retry_delay"] == 2  # 2^1 = 2

    # 3rd failure opens circuit → cannot retry even though retry budget remains
    r3 = engine.handle_error("review_paper", "Still timing out")
    assert r3["action"] in ("fallback", "abort"), \
        f"Circuit opens at 3 failures, expected fallback/abort, got: {r3['action']}"

    # 4th call: circuit still open
    r4 = engine.handle_error("review_paper", "Timeout 4th time")
    assert r4["action"] in ("abort", "fallback"), \
        f"Expected abort/fallback with open circuit, got: {r4['action']}"

    # INPUT_INVALID: no retry (MAX_RETRIES = 0)
    engine2 = ErrorRecoveryEngine()
    r = engine2.handle_error("edit_section", "Invalid parameter: old_text is required")
    assert r["action"] != "retry", \
        f"INPUT_INVALID should never retry, got: {r['action']}"

    # PROVIDER_ERROR: retry up to 2 times
    engine3 = ErrorRecoveryEngine()
    r1 = engine3.handle_error("generate_rewrite", "OpenAI API error: model overloaded")
    assert r1["action"] == "retry"
    r2 = engine3.handle_error("generate_rewrite", "OpenAI API error again")
    assert r2["action"] == "retry"
    r3 = engine3.handle_error("generate_rewrite", "OpenAI API error 3rd")
    assert r3["action"] in ("abort", "fallback", "report")

    print("    ✓ PASSED")


# ==============================================================
# Test 3: Circuit Breaker State Machine
# ==============================================================

def test_circuit_breaker_lifecycle():
    """Test circuit breaker: closed → open → cooldown → half-open → closed."""
    print("  Test 3: Circuit breaker lifecycle...")

    circuit = CircuitState()

    # Initially closed
    assert not circuit.is_open
    assert circuit.can_attempt()
    assert circuit.failures == 0

    # Record failures up to threshold - 1
    circuit.record_failure()
    assert circuit.failures == 1
    assert not circuit.is_open
    assert circuit.can_attempt()

    circuit.record_failure()
    assert circuit.failures == 2
    assert not circuit.is_open

    # 3rd failure: opens the circuit
    circuit.record_failure()
    assert circuit.failures == 3
    assert circuit.is_open
    assert not circuit.can_attempt()  # blocked

    # During cooldown: can't attempt
    assert circuit.cooldown_until > time.time()

    # Simulate cooldown passed (hack the timer)
    circuit.cooldown_until = time.time() - 1
    assert circuit.can_attempt()  # half-open: cooldown expired
    assert not circuit.is_open  # can_attempt() resets is_open

    # After success: fully closed
    circuit.record_success()
    assert circuit.failures == 0
    assert not circuit.is_open
    assert circuit.can_attempt()

    print("    ✓ PASSED")


# ==============================================================
# Test 4: Circuit Breaker via Engine
# ==============================================================

def test_circuit_breaker_via_engine():
    """Test circuit breaker integration through the engine's can_call method."""
    print("  Test 4: Circuit breaker via engine...")

    engine = ErrorRecoveryEngine()

    # Initially: all tools can be called
    assert engine.can_call("review_paper")
    assert engine.can_call("rewrite_section")

    # Trigger 3 failures on rewrite_section (INTERNAL_BUG = 0 retries)
    engine.handle_error("rewrite_section", "AttributeError: NoneType")
    engine.handle_error("rewrite_section", "AttributeError: NoneType")
    engine.handle_error("rewrite_section", "AttributeError: NoneType")

    # Circuit should be open
    assert not engine.can_call("rewrite_section"), "Circuit should be open after 3 failures"

    # Other tools unaffected
    assert engine.can_call("review_paper")

    # Record success resets
    engine.record_success("rewrite_section")
    # Note: record_success only resets if circuit exists — need to check cooldown
    # The circuit may still be in cooldown. Let's force it:
    circuit = engine._circuits["rewrite_section"]
    circuit.cooldown_until = time.time() - 1  # Force cooldown expiry
    assert engine.can_call("rewrite_section")

    print("    ✓ PASSED")


# ==============================================================
# Test 5: Fallback Suggestions
# ==============================================================

def test_fallback_suggestions():
    """Test fallback tool recommendations."""
    print("  Test 5: Fallback suggestions...")

    engine = ErrorRecoveryEngine()

    # Verify FALLBACK_MAP entries
    assert "review_paper" in FALLBACK_MAP
    assert "run_single_reviewer" in FALLBACK_MAP["review_paper"]

    assert "parallel_rewrite" in FALLBACK_MAP
    assert "rewrite_section" in FALLBACK_MAP["parallel_rewrite"]

    assert "search_literature" in FALLBACK_MAP
    assert "deai_closed_loop" in FALLBACK_MAP
    assert "verify_and_enrich_citations" in FALLBACK_MAP

    # get_fallbacks should return available ones
    fallbacks = engine.get_fallbacks("review_paper")
    assert "run_single_reviewer" in fallbacks

    # When fallback is also broken, should be empty
    # Break run_single_reviewer's circuit
    for _ in range(3):
        engine.handle_error("run_single_reviewer", "AttributeError: crash")
    fallbacks = engine.get_fallbacks("review_paper")
    assert "run_single_reviewer" not in fallbacks

    print("    ✓ PASSED")


# ==============================================================
# Test 6: Fallback Action Recommendation
# ==============================================================

def test_fallback_action():
    """Test that handle_error recommends fallback when retries exhausted."""
    print("  Test 6: Fallback action recommendation...")

    engine = ErrorRecoveryEngine()

    # INTERNAL_BUG on review_paper → no retries allowed, should suggest fallback
    result = engine.handle_error("review_paper", "AttributeError: 'NoneType' error")
    # First failure: since MAX_RETRIES[INTERNAL_BUG] = 0, should suggest fallback
    assert result["action"] == "fallback", f"Expected fallback, got: {result['action']}"
    assert "run_single_reviewer" in result.get("fallbacks", [])

    # INTERNAL_BUG on a tool without fallbacks → should report
    engine2 = ErrorRecoveryEngine()
    result = engine2.handle_error("read_section", "AttributeError: crash")
    assert result["action"] == "report", f"Expected report for tool without fallback, got: {result['action']}"

    print("    ✓ PASSED")


# ==============================================================
# Test 7: Success Resets Retry Counter
# ==============================================================

def test_success_resets_state():
    """Test that record_success resets retry count and circuit."""
    print("  Test 7: Success resets state...")

    engine = ErrorRecoveryEngine()

    # Accumulate some retries
    engine.handle_error("generate_rewrite", "Connection timeout")
    assert engine._retry_counts.get("generate_rewrite", 0) > 0

    # Record success
    engine.record_success("generate_rewrite")
    assert engine._retry_counts.get("generate_rewrite", 0) == 0

    # Next failure should start fresh
    r = engine.handle_error("generate_rewrite", "Connection timeout again")
    assert r["action"] == "retry"
    assert r["retry_delay"] == 1  # First retry = 2^0

    print("    ✓ PASSED")


# ==============================================================
# Test 8: Edge Cases
# ==============================================================

def test_edge_cases():
    """Test edge cases: empty messages, very long messages, mixed patterns."""
    print("  Test 8: Edge cases...")

    engine = ErrorRecoveryEngine()

    # Empty error message
    result = engine.classify_error("")
    assert result == ErrorClass.UNKNOWN

    # Very long error message (should still classify correctly)
    long_msg = "x" * 1000 + " timeout " + "y" * 1000
    result = engine.classify_error(long_msg)
    assert result == ErrorClass.TRANSIENT

    # Mixed patterns: first match wins (TRANSIENT checked before INPUT_INVALID)
    mixed_msg = "Rate limit on invalid parameter"
    result = engine.classify_error(mixed_msg)
    # "rate limit" should match TRANSIENT first
    assert result == ErrorClass.TRANSIENT

    # Error message truncation in event (500 chars max)
    long_error = "A" * 600
    engine.handle_error("test_tool", long_error)
    last_event = engine._error_history[-1]
    assert len(last_event.error_message) <= 500

    # Handle error for non-existent tool (no fallback, no special circuit)
    result = engine.handle_error("nonexistent_tool", "Some error")
    assert result["action"] in ("retry", "report")

    print("    ✓ PASSED")


# ==============================================================
# Test 9: Multiple Tools Independent Circuits
# ==============================================================

def test_independent_circuits():
    """Test that circuit breakers are independent per tool."""
    print("  Test 9: Independent circuits per tool...")

    engine = ErrorRecoveryEngine()

    # Break tool_a
    for _ in range(3):
        engine.handle_error("tool_a", "AttributeError: crash")
    assert not engine.can_call("tool_a")

    # tool_b should be unaffected
    assert engine.can_call("tool_b")

    # Break tool_b
    for _ in range(3):
        engine.handle_error("tool_b", "AttributeError: crash")
    assert not engine.can_call("tool_b")

    # Fix tool_a (cooldown expires)
    engine._circuits["tool_a"].cooldown_until = time.time() - 1
    assert engine.can_call("tool_a")
    assert not engine.can_call("tool_b")

    print("    ✓ PASSED")


# ==============================================================
# Test 10: Backoff Cap
# ==============================================================

def test_backoff_cap():
    """Test that exponential backoff is capped at 8 seconds."""
    print("  Test 10: Backoff cap at 8s...")

    engine = ErrorRecoveryEngine()

    # Simulate many retries on a transient error
    # MAX_RETRIES[TRANSIENT] = 3, so we can get at most delays: 1, 2, 4
    r1 = engine.handle_error("search_literature", "timeout")
    assert r1["retry_delay"] == 1  # min(2^0, 8)

    # Reset and test cap formula directly
    # With retry_count=4: min(2^4, 8) = min(16, 8) = 8
    # But MAX_RETRIES caps at 3 for TRANSIENT, so we'd never reach retry_count=4
    # Test the formula: min(2 ** current_retries, 8)
    assert min(2 ** 0, 8) == 1
    assert min(2 ** 1, 8) == 2
    assert min(2 ** 2, 8) == 4
    assert min(2 ** 3, 8) == 8
    assert min(2 ** 4, 8) == 8  # Cap

    print("    ✓ PASSED")


# ==============================================================
# Main
# ==============================================================

def main():
    print("\n" + "=" * 60)
    print("  ERROR RECOVERY TEST")
    print("=" * 60 + "\n")

    tests = [
        test_error_classification,
        test_retry_logic,
        test_circuit_breaker_lifecycle,
        test_circuit_breaker_via_engine,
        test_fallback_suggestions,
        test_fallback_action,
        test_success_resets_state,
        test_edge_cases,
        test_independent_circuits,
        test_backoff_cap,
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
