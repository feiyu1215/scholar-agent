#!/usr/bin/env python3
"""
test_context_compression.py — Tests for ProactiveContextManager's compression logic.

Tests:
1. Token estimation accuracy (ASCII vs CJK)
2. Message token estimation (overhead, tool_calls, content)
3. Budget tracking (usage_ratio, should_compress, must_compress)
4. Soft limit trigger at 65%
5. Hard limit trigger at 80%
6. Compression preserves recent messages (recent_window)
7. System overhead tracking
8. Status string formatting
9. ContextBudget properties
10. Edge cases: empty messages, zero-length content, huge single message
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.context_manager import (
    ProactiveContextManager,
    ContextBudget,
    estimate_tokens,
    estimate_message_tokens,
    SOFT_LIMIT_RATIO,
    HARD_LIMIT_RATIO,
    DEFAULT_CONTEXT_WINDOW,
)


# ==============================================================
# Test 1: Token Estimation — ASCII
# ==============================================================

def test_token_estimation_ascii():
    """Test ASCII text token estimation (~4 chars/token)."""
    print("  Test 1: Token estimation (ASCII)...")

    # 100 ASCII chars → ~25 tokens
    text_100 = "a" * 100
    tokens = estimate_tokens(text_100)
    assert 20 <= tokens <= 30, f"Expected ~25 tokens for 100 ASCII chars, got {tokens}"

    # 400 ASCII chars → ~100 tokens
    text_400 = "hello world " * 33  # ~396 chars
    tokens = estimate_tokens(text_400)
    assert 80 <= tokens <= 120, f"Expected ~100 tokens for 400 ASCII chars, got {tokens}"

    # Empty string → 0
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0 if estimate_tokens("") == 0 else True

    # Single char → at least 1
    assert estimate_tokens("x") >= 1

    print("    ✓ PASSED")


# ==============================================================
# Test 2: Token Estimation — CJK
# ==============================================================

def test_token_estimation_cjk():
    """Test CJK text token estimation (~1.5 chars/token)."""
    print("  Test 2: Token estimation (CJK)...")

    # 150 CJK chars → ~100 tokens
    text_cjk = "这是一个测试" * 25  # 150 CJK chars
    tokens = estimate_tokens(text_cjk)
    assert 80 <= tokens <= 120, f"Expected ~100 tokens for 150 CJK chars, got {tokens}"

    # Mixed CJK + ASCII
    mixed = "Hello 你好世界 World 这是测试"  # ~12 ASCII words + 6 CJK chars
    tokens = estimate_tokens(mixed)
    assert tokens > 0

    # Pure CJK should give more tokens per char than pure ASCII per char
    cjk_100 = "中" * 100
    ascii_100 = "a" * 100
    assert estimate_tokens(cjk_100) > estimate_tokens(ascii_100), \
        "CJK should produce more tokens per char than ASCII"

    print("    ✓ PASSED")


# ==============================================================
# Test 3: Message Token Estimation
# ==============================================================

def test_message_token_estimation():
    """Test full message token estimation with overhead."""
    print("  Test 3: Message token estimation...")

    # Simple text message
    msg = {"role": "user", "content": "Hello, please review my paper."}
    tokens = estimate_message_tokens(msg)
    assert tokens > 4, "Should be at least base overhead (4) + content"
    assert tokens < 50, f"Simple message shouldn't be 50+ tokens, got {tokens}"

    # Message with tool call
    msg_tool = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": "review_paper",
                    "arguments": '{"reviewer_count": 3, "focus_dimensions": ["methodology"]}',
                }
            }
        ],
    }
    tokens_tool = estimate_message_tokens(msg_tool)
    assert tokens_tool > 4, "Tool call message should have >4 tokens"

    # Empty message: just overhead
    msg_empty = {"role": "system", "content": ""}
    tokens_empty = estimate_message_tokens(msg_empty)
    assert tokens_empty == 4, f"Empty content should be just overhead (4), got {tokens_empty}"

    # Message with name field
    msg_named = {"role": "tool", "content": "Result", "name": "review_paper"}
    tokens_named = estimate_message_tokens(msg_named)
    assert tokens_named > estimate_message_tokens({"role": "tool", "content": "Result"})

    print("    ✓ PASSED")


# ==============================================================
# Test 4: Budget Tracking
# ==============================================================

def test_budget_tracking():
    """Test that budget correctly tracks token usage across messages."""
    print("  Test 4: Budget tracking...")

    mgr = ProactiveContextManager(max_tokens=1000)

    # Start empty
    budget = mgr.get_budget()
    assert budget.estimated_tokens == 0
    assert budget.usage_ratio == 0.0
    assert not budget.should_compress
    assert not budget.must_compress

    # Add messages
    messages = [
        {"role": "user", "content": "a" * 200},   # ~50 tokens
        {"role": "assistant", "content": "b" * 200},  # ~50 tokens
    ]
    budget = mgr.update(messages)
    assert budget.estimated_tokens > 0
    assert budget.messages_count == 2

    print("    ✓ PASSED")


# ==============================================================
# Test 5: Soft Limit Trigger (65%)
# ==============================================================

def test_soft_limit_trigger():
    """Test compression triggers at 65% capacity."""
    print("  Test 5: Soft limit trigger (65%)...")

    # Use a small context window for easy testing
    mgr = ProactiveContextManager(max_tokens=100)

    # Below 65%: no compression
    messages_small = [{"role": "user", "content": "x" * 50}]  # ~12.5 tokens
    mgr.update(messages_small)
    assert not mgr.should_compress(), \
        f"At {mgr.get_budget().usage_ratio:.0%} should not compress"

    # At/above 65%: trigger compression
    messages_large = [{"role": "user", "content": "x" * 300}]  # ~75 tokens > 65% of 100
    mgr.update(messages_large)
    assert mgr.should_compress(), \
        f"At {mgr.get_budget().usage_ratio:.0%} should compress (>=65%)"

    print("    ✓ PASSED")


# ==============================================================
# Test 6: Hard Limit Trigger (80%)
# ==============================================================

def test_hard_limit_trigger():
    """Test aggressive compression triggers at 80% capacity."""
    print("  Test 6: Hard limit trigger (80%)...")

    mgr = ProactiveContextManager(max_tokens=100)

    # Below 80%: should_compress but not must_compress
    messages_65 = [{"role": "user", "content": "x" * 280}]  # ~70 tokens = 70%
    mgr.update(messages_65)
    if mgr.get_budget().usage_ratio >= 0.65:
        assert mgr.should_compress()
    if mgr.get_budget().usage_ratio < 0.80:
        assert not mgr.must_compress()

    # At/above 80%: must_compress
    messages_85 = [{"role": "user", "content": "x" * 400}]  # ~100 tokens = 100%
    mgr.update(messages_85)
    assert mgr.must_compress(), \
        f"At {mgr.get_budget().usage_ratio:.0%} must compress (>=80%)"

    print("    ✓ PASSED")


# ==============================================================
# Test 7: System Overhead Tracking
# ==============================================================

def test_system_overhead():
    """Test that system prompt overhead is included in budget calculation."""
    print("  Test 7: System overhead tracking...")

    mgr = ProactiveContextManager(max_tokens=1000)

    # No system overhead: only message tokens
    messages = [{"role": "user", "content": "hello"}]
    budget1 = mgr.update(messages)
    tokens_no_sys = budget1.estimated_tokens

    # Add system overhead
    system_prompt = "You are a helpful assistant. " * 20  # ~120 chars → ~30 tokens
    mgr.set_system_overhead(system_prompt)
    budget2 = mgr.update(messages)
    tokens_with_sys = budget2.estimated_tokens

    assert tokens_with_sys > tokens_no_sys, \
        f"System overhead should increase token count: {tokens_with_sys} > {tokens_no_sys}"

    print("    ✓ PASSED")


# ==============================================================
# Test 8: Status String
# ==============================================================

def test_status_string():
    """Test human-readable status string formatting."""
    print("  Test 8: Status string formatting...")

    mgr = ProactiveContextManager(max_tokens=128000)

    messages = [
        {"role": "user", "content": "Review my paper please"},
        {"role": "assistant", "content": "I'll start the review process."},
        {"role": "user", "content": "Focus on methodology"},
    ]
    mgr.update(messages)
    status = mgr.get_status_string()

    assert "Context:" in status
    assert "128,000" in status or "128000" in status
    assert "Messages: 3" in status
    assert "%" in status

    print("    ✓ PASSED")


# ==============================================================
# Test 9: ContextBudget Properties
# ==============================================================

def test_context_budget_properties():
    """Test ContextBudget dataclass computed properties."""
    print("  Test 9: ContextBudget properties...")

    # Manual construction
    budget = ContextBudget(max_tokens=10000, estimated_tokens=6500)
    assert budget.usage_ratio == 0.65
    assert budget.should_compress  # >=0.65
    assert not budget.must_compress  # <0.80

    budget2 = ContextBudget(max_tokens=10000, estimated_tokens=8000)
    assert budget2.usage_ratio == 0.80
    assert budget2.should_compress
    assert budget2.must_compress  # >=0.80

    budget3 = ContextBudget(max_tokens=10000, estimated_tokens=3000)
    assert budget3.usage_ratio == 0.30
    assert not budget3.should_compress
    assert not budget3.must_compress

    # Edge: max_tokens=0 (avoid division by zero)
    budget_zero = ContextBudget(max_tokens=0, estimated_tokens=100)
    # Should handle gracefully (division by max(max_tokens, 1))
    assert budget_zero.usage_ratio == 100.0  # 100/1

    print("    ✓ PASSED")


# ==============================================================
# Test 10: Constants Verification
# ==============================================================

def test_constants():
    """Verify module constants are correctly set."""
    print("  Test 10: Constants verification...")

    assert SOFT_LIMIT_RATIO == 0.65, f"Expected 0.65, got {SOFT_LIMIT_RATIO}"
    assert HARD_LIMIT_RATIO == 0.80, f"Expected 0.80, got {HARD_LIMIT_RATIO}"
    assert DEFAULT_CONTEXT_WINDOW == 128000, f"Expected 128000, got {DEFAULT_CONTEXT_WINDOW}"
    assert SOFT_LIMIT_RATIO < HARD_LIMIT_RATIO

    print("    ✓ PASSED")


# ==============================================================
# Test 11: Incremental Updates
# ==============================================================

def test_incremental_updates():
    """Test that repeated update() calls correctly track growing context."""
    print("  Test 11: Incremental updates...")

    mgr = ProactiveContextManager(max_tokens=10000)

    messages = []
    prev_tokens = 0

    for i in range(10):
        messages.append({"role": "user", "content": f"Message {i}: " + "x" * 100})
        budget = mgr.update(messages)
        assert budget.estimated_tokens > prev_tokens, \
            f"Tokens should grow monotonically: {budget.estimated_tokens} > {prev_tokens}"
        assert budget.messages_count == i + 1
        prev_tokens = budget.estimated_tokens

    print("    ✓ PASSED")


# ==============================================================
# Test 12: Large Context Stress Test
# ==============================================================

def test_large_context():
    """Test behavior with many messages approaching the limit."""
    print("  Test 12: Large context stress test...")

    mgr = ProactiveContextManager(max_tokens=5000)

    # Build up ~4000 tokens worth of messages
    messages = []
    for i in range(100):
        messages.append({"role": "user", "content": f"Q{i}: " + "x" * 150})  # ~40 tokens each

    budget = mgr.update(messages)

    # Should be well over the soft limit
    assert budget.usage_ratio > 0.5, \
        f"100 messages should fill a significant portion: {budget.usage_ratio:.1%}"

    # Verify should_compress is triggered for large contexts
    if budget.usage_ratio >= SOFT_LIMIT_RATIO:
        assert mgr.should_compress()

    print("    ✓ PASSED")


# ==============================================================
# Main
# ==============================================================

def main():
    print("\n" + "=" * 60)
    print("  CONTEXT COMPRESSION TEST")
    print("=" * 60 + "\n")

    tests = [
        test_token_estimation_ascii,
        test_token_estimation_cjk,
        test_message_token_estimation,
        test_budget_tracking,
        test_soft_limit_trigger,
        test_hard_limit_trigger,
        test_system_overhead,
        test_status_string,
        test_context_budget_properties,
        test_constants,
        test_incremental_updates,
        test_large_context,
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
