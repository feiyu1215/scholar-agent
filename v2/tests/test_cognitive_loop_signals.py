"""
tests/test_cognitive_loop_signals.py — Integration tests for SignalDispatcher + Phase FSM
behavior within the cognitive_loop.

Tests:
1. Signal dispatcher limits system messages to max 2 per turn
2. Phase transition via request_phase_transition tool
3. Signal dedup suppression within DEDUP_WINDOW
4. Fallback behavior when signal dispatcher is disabled
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure v2/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Disable checker to avoid external LLM calls in tests
import core.checker as _checker_mod
_checker_mod.CHECKER_ENABLED = False

from core.loop import cognitive_loop, LoopDone, LoopDoomStop
from core.harness import Harness
from core.phases import Phase
from tests.mock_llm import (
    MockLLMClient,
    make_done_response,
    make_read_section_response,
    make_phase_transition_response,
    make_text_only_response,
    make_single_finding_response,
)


# ============================================================
# Minimal tool definitions for tests
# ============================================================

SCHOLAR_TOOLS = [
    {
        "name": "read_section",
        "description": "Read a section of the paper.",
        "input_schema": {
            "type": "object",
            "properties": {"section_name": {"type": "string"}},
            "required": ["section_name"],
        },
    },
    {
        "name": "update_findings",
        "description": "Update the list of review findings.",
        "input_schema": {
            "type": "object",
            "properties": {"findings": {"type": "array"}},
            "required": ["findings"],
        },
    },
    {
        "name": "mark_complete",
        "description": "Mark the review as complete.",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
    {
        "name": "request_phase_transition",
        "description": "Request transition to a new cognitive phase.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_phase": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["target_phase"],
        },
    },
]


# ============================================================
# Helper: create a test harness with mocked paper loading
# ============================================================

def _make_harness(max_loop_turns: int = 50) -> Harness:
    """Create a Harness with fake paper sections, bypassing file loading."""
    with patch("core.harness._pl_load_paper"):
        h = Harness(paper_path="fake_paper.md", max_loop_turns=max_loop_turns, enable_hdwm=False)
    # Set up paper sections directly
    h.state.paper_sections = {
        "introduction": "This paper presents a novel approach to transformer pruning...",
        "methodology": "We propose a dynamic importance scoring mechanism that...",
        "results": "Our experiments on GLUE benchmark show significant improvements...",
    }
    h.state.paper_overview = "Test paper overview"
    h.state.sections_read = []
    h.state.findings = []
    return h


# ============================================================
# Test Cases
# ============================================================

class TestSignalDispatcherAndPhaseFSM(unittest.TestCase):
    """Integration tests for SignalDispatcher + Phase FSM within cognitive_loop."""

    # ----------------------------------------------------------
    # Test 1: Signal dispatcher limits messages to max 2 per turn
    # ----------------------------------------------------------

    def test_signal_dispatcher_limits_messages(self):
        """When multiple signals fire in the same turn, at most 2 non-doom
        system messages are injected per turn by the SignalDispatcher."""

        # Use a mock that returns text-only (no tool calls) for 1 turn, then done.
        # Text-only responses don't exit the loop — they continue.
        # We want exactly 1 turn of signal injection to observe, then complete.
        client = MockLLMClient(responses=[
            # Turn 1: text-only response (loop continues)
            make_text_only_response("Analyzing the paper structure..."),
            # Turn 2: mark complete to exit
            make_done_response("Done."),
        ])

        # Use high max_loop_turns to avoid doom guard, but set self_eval_first low
        # so the soft turn warning fires early.
        harness = _make_harness(max_loop_turns=50)

        # Lower self_eval thresholds so soft_turn_warning fires at turn 2
        harness.gate_config.self_eval_first = 2
        harness.gate_config.self_eval_second = 4
        harness.gate_config.self_eval_final = 6

        # Simulate being close to token budget to trigger budget_warning
        harness.state.total_tokens = 180_000

        # Simulate several sections already read without findings (cognitive nudge)
        harness.state.sections_read = ["introduction", "methodology", "results"]
        harness.state.consecutive_read_turns = 4  # triggers cognitive output nudge

        # Set loop_turns so that after increment (loop_turns += 1), it equals
        # self_eval_first (2). The check fires BEFORE increment in the signal
        # section, but soft_turn_limit uses state.loop_turns which is the current
        # value. We set to 1 so on the first iteration check it's 1 (not matching),
        # then incremented to 2. On second iteration check it's 2 (matches self_eval_first).
        # Actually, looking at loop.py: signals are checked BEFORE increment.
        # So set loop_turns = self_eval_first so the check fires immediately.
        harness.state.loop_turns = harness.gate_config.self_eval_first

        messages = [
            {"role": "system", "content": "You are a paper reviewer."},
            {"role": "user", "content": "Please review this paper."},
        ]

        # Ensure dispatcher is enabled
        with patch("core.godel_config.GODEL_SIGNAL_DISPATCHER_ENABLED", True):
            result = asyncio.run(
                cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False)
            )

        # The loop should complete
        self.assertIsInstance(result, LoopDone)

        # Count system messages that were injected during the loop
        # (excluding the original system prompt at messages[0])
        injected_system_msgs = [
            m for m in messages[1:]
            if m.get("role") == "system"
        ]

        # Per-turn limit: at most SIGNAL_DISPATCHER_MAX_PER_TURN (2) non-doom
        # system messages per turn. Even though 3+ signals could fire
        # (budget + turn + cognitive), the dispatcher caps to 2 per turn.
        # With 2 loop iterations, the upper bound is 2 * 2 = 4 system messages.
        self.assertLessEqual(len(injected_system_msgs), 4,
            f"Expected at most 4 system messages across 2 turns, got {len(injected_system_msgs)}")

        # More specifically: between consecutive assistant messages, count system messages.
        # Find system messages injected in the first turn (before first assistant response).
        first_turn_system = []
        for m in messages[2:]:  # skip original system + user
            if m.get("role") == "system":
                first_turn_system.append(m)
            elif m.get("role") == "assistant":
                break  # end of first turn's injections

        self.assertLessEqual(len(first_turn_system), 2,
            f"Expected at most 2 system messages in first turn, got {len(first_turn_system)}")

    # ----------------------------------------------------------
    # Test 2: Phase transition via tool
    # ----------------------------------------------------------

    def test_phase_transition_via_tool(self):
        """Agent reads 2 sections, calls request_phase_transition to deep_review,
        verifies phase transitions, then marks complete."""

        client = MockLLMClient(responses=[
            # Turn 1: read introduction
            make_read_section_response("introduction"),
            # Turn 2: read methodology
            make_read_section_response("methodology"),
            # Turn 3: request phase transition to deep_review
            make_phase_transition_response("deep_review", reason="Have enough context to go deeper"),
            # Turn 4: mark complete
            make_done_response("Review complete after phase transition."),
        ])

        harness = _make_harness()
        messages = [
            {"role": "system", "content": "You are a paper reviewer."},
            {"role": "user", "content": "Please review this paper."},
        ]

        result = asyncio.run(
            cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False)
        )

        # Assert LoopDone returned
        self.assertIsInstance(result, LoopDone)

        # Assert phase transitioned to DEEP_REVIEW
        self.assertEqual(harness.phase_fsm.current_phase, Phase.DEEP_REVIEW)

        # Assert transition_count == 1
        self.assertEqual(harness.phase_fsm.transition_count, 1)

        # Assert loop_turns == 4
        self.assertEqual(harness.state.loop_turns, 4)

    # ----------------------------------------------------------
    # Test 3: Signal dedup suppression
    # ----------------------------------------------------------

    def test_signal_dedup_suppression(self):
        """The same signal source should not appear in consecutive turns
        within the DEDUP_WINDOW."""

        # Run a loop with 4 text-only turns followed by done.
        # Set state so check_soft_turn_limit fires every turn.
        client = MockLLMClient(responses=[
            make_text_only_response("Thinking turn 1..."),
            make_text_only_response("Thinking turn 2..."),
            make_text_only_response("Thinking turn 3..."),
            make_text_only_response("Thinking turn 4..."),
            make_done_response("Done after 4 text turns."),
        ])

        harness = _make_harness(max_loop_turns=50)

        # Set loop_turns so that self_eval_first is hit on turn 1, and
        # would normally re-fire on subsequent turns if not deduped.
        # We'll set the gate_config thresholds to fire on consecutive turns.
        harness.gate_config.self_eval_first = 1
        harness.gate_config.self_eval_second = 2
        harness.gate_config.self_eval_final = 3

        messages = [
            {"role": "system", "content": "You are a paper reviewer."},
            {"role": "user", "content": "Please review this paper."},
        ]

        with patch("core.godel_config.GODEL_SIGNAL_DISPATCHER_ENABLED", True):
            result = asyncio.run(
                cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False)
            )

        self.assertIsInstance(result, LoopDone)

        # Collect all system messages injected (after original system prompt)
        system_messages = [
            m["content"] for m in messages[1:]
            if m.get("role") == "system"
        ]

        # Count how many times a "turn" source signal appears.
        # The soft_turn_limit messages contain "[自评时刻]" or "[资源提示]".
        turn_signal_msgs = [
            msg for msg in system_messages
            if "自评时刻" in msg or "资源提示" in msg
        ]

        # With DEDUP_WINDOW=3, the "turn" source can appear at most:
        # turn 1 (fires), turn 2 (dedup: same source within 3-turn window → suppressed
        #   BUT self_eval_second is a different check that also fires the "turn" source),
        # Actually, each self_eval_* fires with source="turn", so dedup applies.
        # After the first fire at turn 1, turns 2 and 3 are within the window and suppressed.
        # Turn 4 is outside the window (4 - 1 = 3, which is NOT < 3, so it can fire again).
        # So we expect at most 2 turn signals across 4 turns.
        # But since the signals with different thresholds fire at different turns,
        # the dispatcher dedup uses the "turn" source — same source in window is suppressed.
        self.assertLessEqual(len(turn_signal_msgs), 2,
            f"Expected at most 2 'turn' source signals due to dedup, got {len(turn_signal_msgs)}: {turn_signal_msgs}")

    # ----------------------------------------------------------
    # Test 4: Dispatcher disabled fallback (V2 stacked behavior)
    # ----------------------------------------------------------

    def test_signal_dispatcher_disabled_fallback(self):
        """When GODEL_SIGNAL_DISPATCHER_ENABLED=False, all warnings are injected
        without the 2-per-turn limit (V2 stacked behavior)."""

        # Text-only for 1 turn then done.
        client = MockLLMClient(responses=[
            make_text_only_response("Thinking..."),
            make_done_response("Done."),
        ])

        # Use high max_loop_turns to avoid doom guard
        harness = _make_harness(max_loop_turns=50)

        # Lower self_eval thresholds so soft_turn_warning fires immediately
        harness.gate_config.self_eval_first = 2
        harness.gate_config.self_eval_second = 4
        harness.gate_config.self_eval_final = 6

        # Set up conditions to trigger multiple warnings simultaneously:
        # 1. High token usage → budget_warning
        harness.state.total_tokens = 180_000
        # 2. Cognitive nudge: consecutive reads without findings
        harness.state.sections_read = ["introduction", "methodology", "results"]
        harness.state.consecutive_read_turns = 4
        # 3. Set loop_turns to match self_eval_first so soft_turn_limit fires
        harness.state.loop_turns = harness.gate_config.self_eval_first

        messages = [
            {"role": "system", "content": "You are a paper reviewer."},
            {"role": "user", "content": "Please review this paper."},
        ]

        # Disable the signal dispatcher — should fall through to stacked checks
        with patch("core.godel_config.GODEL_SIGNAL_DISPATCHER_ENABLED", False):
            result = asyncio.run(
                cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False)
            )

        self.assertIsInstance(result, LoopDone)

        # Collect system messages injected after the original prompt
        injected_system_msgs = [
            m for m in messages[1:]
            if m.get("role") == "system"
        ]

        # In stacked (V2) mode, ALL warnings that fire are injected without cap.
        # We set up conditions for at least 2 signals (budget + soft_turn or cognitive).
        # The key assertion: stacked mode can inject MORE than 2 per turn
        # (no dispatcher limit). We verify that at least some messages got through.
        self.assertGreaterEqual(len(injected_system_msgs), 1,
            "Expected at least 1 system message in stacked fallback mode")

        # Verify the messages contain the expected warning patterns.
        all_content = " ".join(m.get("content", "") for m in injected_system_msgs)

        # At least one of these patterns should appear in stacked mode:
        has_budget = "Harness 提示" in all_content or "budget" in all_content.lower()
        has_turn = "自评时刻" in all_content or "资源提示" in all_content
        has_cognitive = "认知提醒" in all_content or "认知警告" in all_content

        self.assertTrue(
            has_budget or has_turn or has_cognitive,
            f"Expected warning messages in stacked mode, got: {all_content[:300]}"
        )


if __name__ == "__main__":
    unittest.main()
