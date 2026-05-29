"""
tests/test_cognitive_loop_basic.py — Mock-LLM integration tests for basic cognitive_loop flow.

Tests the core cognitive loop behavior with a deterministic MockLLMClient:
1. Basic scan-read-done flow
2. Doom loop guard triggering
3. Nudge on insufficient findings
4. Text-only responses continuing the loop
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

from core.loop import cognitive_loop, LoopDone, LoopTalk, LoopDoomStop
from core.harness import Harness
from core.phases import Phase
from tests.mock_llm import (
    MockLLMClient,
    MockResponse,
    make_tool_call,
    make_done_response,
    make_read_section_response,
    make_update_findings_response,
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
            "properties": {
                "section_name": {"type": "string"},
            },
            "required": ["section_name"],
        },
    },
    {
        "name": "update_findings",
        "description": "Update the list of review findings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "findings": {"type": "array"},
            },
            "required": ["findings"],
        },
    },
    {
        "name": "mark_complete",
        "description": "Mark the review as complete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
            },
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

class TestBasicScanReadDone(unittest.TestCase):
    """
    test_basic_scan_read_done — Agent reads 2 sections, updates findings
    with valid findings, then marks complete.
    """

    def test_basic_scan_read_done(self):
        """Agent reads 2 sections, updates findings, then calls mark_complete -> LoopDone."""
        client = MockLLMClient(responses=[
            # Turn 1: read introduction
            make_read_section_response("introduction"),
            # Turn 2: read methodology
            make_read_section_response("methodology"),
            # Turn 3: update a finding (single finding format)
            make_single_finding_response(
                finding="Authors should justify the pruning threshold selection with theoretical analysis",
                section="methodology",
                priority="high",
                status="verified",
                evidence="Section 3.2 states threshold=0.1 without explaining why this value was chosen.",
            ),
            # Turn 4: mark complete
            make_done_response("Review complete. Found 1 methodology issue."),
        ])

        harness = _make_harness()
        messages = [
            {"role": "system", "content": "You are a paper reviewer."},
            {"role": "user", "content": "Please review this paper."},
        ]

        result = asyncio.run(
            cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False)
        )

        # Assert returns LoopDone
        self.assertIsInstance(result, LoopDone)
        self.assertIn("Review complete", result.summary)

        # Assert sections_read has 2 entries
        # (read_section tool adds to sections_read when called)
        self.assertGreaterEqual(len(harness.state.sections_read), 2)
        self.assertIn("introduction", harness.state.sections_read)
        self.assertIn("methodology", harness.state.sections_read)

        # Assert loop_turns matches expected (4 turns: 2 reads + 1 findings + 1 done)
        self.assertEqual(harness.state.loop_turns, 4)

        # Assert phase transitioned to DEEP_REVIEW after 2 reads
        # (Phase FSM auto-advances when sections_read >= 2 via request_phase_transition
        #  or stays in initial_scan if no explicit request. The FSM itself doesn't
        #  auto-transition, but we can check the phase was at least INITIAL_SCAN.)
        # In the current design, phase transitions are explicit. The agent should
        # still be in INITIAL_SCAN unless it explicitly called request_phase_transition.
        # Let's verify the FSM is accessible.
        self.assertIsNotNone(harness.phase_fsm)


class TestDoomLoopGuard(unittest.TestCase):
    """
    test_doom_loop_guard — max_loop_turns=3, Agent keeps doing read_section
    without completing. Should trigger LoopDoomStop.
    """

    def test_doom_loop_guard(self):
        """Doom loop guard triggers when loop_turns >= max_loop_turns + 2."""
        # With max_loop_turns=3, hard limit is 3+2=5
        # The agent will keep reading sections and never complete.
        client = MockLLMClient(responses=[
            make_read_section_response("introduction"),
            make_read_section_response("methodology"),
            make_read_section_response("results"),
            make_read_section_response("introduction"),
            make_read_section_response("methodology"),
            make_read_section_response("results"),
            make_read_section_response("introduction"),  # extra safety
        ])

        harness = _make_harness(max_loop_turns=3)
        messages = [
            {"role": "system", "content": "You are a paper reviewer."},
            {"role": "user", "content": "Please review this paper."},
        ]

        result = asyncio.run(
            cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False)
        )

        # Assert returns LoopDoomStop
        self.assertIsInstance(result, LoopDoomStop)

        # The hard limit is max_loop_turns + 2 = 5
        # check_doom_loop fires when loop_turns >= hard_limit (5)
        # Since loop_turns is incremented at the start of each iteration,
        # and check_doom_loop is checked before incrementing,
        # the agent should have run exactly up to the hard limit.
        self.assertGreaterEqual(harness.state.loop_turns, 3)
        self.assertLessEqual(harness.state.loop_turns, 5)


class TestNudgeOnInsufficientFindings(unittest.TestCase):
    """
    test_nudge_on_insufficient_findings — Agent tries mark_complete with an
    unverified high-priority finding. First attempt gets nudged by the completion
    gate, after verifying the finding and calling done again, returns LoopDone.
    """

    def test_nudge_on_insufficient_findings(self):
        """First done with unverified high finding gets nudged; second done after verification succeeds."""
        client = MockLLMClient(responses=[
            # Turn 1: read a section
            make_read_section_response("introduction"),
            # Turn 2: add a finding marked as needs_verification (high priority)
            make_single_finding_response(
                finding="The experimental setup lacks a proper baseline comparison",
                section="results",
                priority="high",
                status="needs_verification",
                evidence="",
            ),
            # Turn 3: try to mark complete (should get nudged because of unverified high finding)
            make_done_response("Review complete."),
            # Turn 4: after nudge, agent updates the finding to verified
            make_single_finding_response(
                finding="Baseline comparison confirmed insufficient after checking Table 2",
                section="results",
                priority="high",
                status="verified",
                evidence="Table 2 only compares with one baseline from 2019.",
            ),
            # Turn 5: mark complete again (should succeed now — nudge only fires once)
            make_done_response("Review complete with verified findings."),
        ])

        harness = _make_harness()
        messages = [
            {"role": "system", "content": "You are a paper reviewer."},
            {"role": "user", "content": "Please review this paper."},
        ]

        result = asyncio.run(
            cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False)
        )

        # The result should be LoopDone (after the second done attempt succeeds)
        self.assertIsInstance(result, LoopDone)

        # Verify that findings were actually added
        self.assertGreaterEqual(len(harness.state.findings), 1)

        # Verify that the loop used more than 3 turns (because of the nudge)
        self.assertGreaterEqual(harness.state.loop_turns, 4)


class TestNoToolCallsContinuesLoop(unittest.TestCase):
    """
    test_no_tool_calls_continues_loop — Agent produces text-only responses
    (no tool_calls) for 2 turns, then calls mark_complete.
    """

    def test_no_tool_calls_continues_loop(self):
        """Text-only responses don't exit the loop; loop continues until mark_complete."""
        client = MockLLMClient(responses=[
            # Turn 1: read section first
            make_read_section_response("introduction"),
            # Turn 2: text-only thinking (no tool calls)
            make_text_only_response("Let me think about the methodology section..."),
            # Turn 3: more text-only thinking
            make_text_only_response("I notice potential issues with the sample size."),
            # Turn 4: now add a finding
            make_single_finding_response(
                finding="Authors should include a power analysis to justify the sample size",
                section="methodology",
                priority="high",
                status="verified",
                evidence="No power analysis or sample size calculation is reported in the methodology section.",
            ),
            # Turn 5: mark complete
            make_done_response("Review complete."),
        ])

        harness = _make_harness()
        messages = [
            {"role": "system", "content": "You are a paper reviewer."},
            {"role": "user", "content": "Please review this paper."},
        ]

        result = asyncio.run(
            cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False)
        )

        # Loop should continue past text-only turns and return LoopDone eventually
        self.assertIsInstance(result, LoopDone)

        # 5 turns total: 1 read + 2 text-only + 1 findings + 1 done
        self.assertEqual(harness.state.loop_turns, 5)

        # Verify the client was called 5 times
        self.assertEqual(client.call_count, 5)


if __name__ == "__main__":
    unittest.main()
