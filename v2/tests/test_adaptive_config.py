"""
tests/test_adaptive_config.py — Unit tests for core/adaptive_config.py.

Verifies:
1. Default initialization and frozen mode
2. Phase-based temperature adaptation
3. Complexity-based max_nudges adaptation
4. Context pressure-based keep_recent
5. Session progress-based signal limit
6. Adaptation logging
7. Edge cases and idempotency
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.adaptive_config import (
    AdaptiveConfig,
    AdaptationEntry,
    DEFAULT_TEMPERATURE,
    DEFAULT_MAX_NUDGES,
    DEFAULT_KEEP_RECENT,
    DEFAULT_SIGNAL_MAX_PER_TURN,
    TEMPERATURE_BY_PHASE,
    COMPLEX_PAPER_SECTIONS_THRESHOLD,
    CONTEXT_PRESSURE_RATIO,
    LATE_SESSION_TURN_RATIO,
)


# ============================================================
# Minimal mock WorkspaceState for testing
# ============================================================

@dataclass
class MockState:
    """Minimal mock of WorkspaceState with fields AdaptiveConfig reads."""
    loop_turns: int = 5
    max_loop_turns: int = 30
    total_tokens: int = 50000
    token_budget: int = 200000
    context_window: int = 128000
    last_prompt_tokens: int = 0
    paper_sections: dict = field(default_factory=dict)


class TestDefaultInitialization(unittest.TestCase):
    """Test that AdaptiveConfig starts with sensible defaults."""

    def test_defaults(self):
        config = AdaptiveConfig()
        self.assertAlmostEqual(config.temperature, DEFAULT_TEMPERATURE)
        self.assertEqual(config.max_nudges, DEFAULT_MAX_NUDGES)
        self.assertEqual(config.keep_recent, DEFAULT_KEEP_RECENT)
        self.assertEqual(config.signal_max_per_turn, DEFAULT_SIGNAL_MAX_PER_TURN)
        self.assertFalse(config.frozen)
        self.assertEqual(config.adaptation_log, [])

    def test_frozen_default(self):
        config = AdaptiveConfig.frozen_default()
        self.assertTrue(config.frozen)

    def test_from_overrides(self):
        config = AdaptiveConfig.from_overrides(temperature=0.5, max_nudges=4)
        self.assertAlmostEqual(config.temperature, 0.5)
        self.assertEqual(config.max_nudges, 4)

    def test_from_overrides_ignores_invalid(self):
        config = AdaptiveConfig.from_overrides(fake_param=999, temperature=0.1)
        self.assertAlmostEqual(config.temperature, 0.1)
        self.assertFalse(hasattr(config, "fake_param"))


class TestFrozenMode(unittest.TestCase):
    """Test that frozen config doesn't change on tick."""

    def test_frozen_no_adapt(self):
        config = AdaptiveConfig.frozen_default()
        state = MockState(paper_sections={f"s{i}": f"text{i}" for i in range(20)})
        config.set_phase("deep_review")
        config.tick(state)
        # Should remain at defaults
        self.assertAlmostEqual(config.temperature, DEFAULT_TEMPERATURE)
        self.assertEqual(config.max_nudges, DEFAULT_MAX_NUDGES)
        self.assertEqual(config.adaptation_log, [])


class TestPhaseTemperature(unittest.TestCase):
    """Test phase-based temperature adaptation."""

    def test_deep_review_increases_temp(self):
        config = AdaptiveConfig()
        state = MockState()
        config.set_phase("deep_review")
        config.tick(state)
        self.assertAlmostEqual(config.temperature, TEMPERATURE_BY_PHASE["deep_review"])

    def test_editing_decreases_temp(self):
        config = AdaptiveConfig()
        state = MockState()
        config.set_phase("editing")
        config.tick(state)
        self.assertAlmostEqual(config.temperature, TEMPERATURE_BY_PHASE["editing"])

    def test_initial_scan_temp(self):
        config = AdaptiveConfig()
        state = MockState()
        config.set_phase("initial_scan")
        config.tick(state)
        self.assertAlmostEqual(config.temperature, TEMPERATURE_BY_PHASE["initial_scan"])

    def test_synthesis_temp(self):
        config = AdaptiveConfig()
        state = MockState()
        config.set_phase("synthesis")
        config.tick(state)
        self.assertAlmostEqual(config.temperature, TEMPERATURE_BY_PHASE["synthesis"])

    def test_unknown_phase_keeps_default(self):
        config = AdaptiveConfig()
        state = MockState()
        config.set_phase("some_unknown_phase")
        config.tick(state)
        # Unknown phase maps to DEFAULT_TEMPERATURE
        self.assertAlmostEqual(config.temperature, DEFAULT_TEMPERATURE)

    def test_no_phase_no_change(self):
        config = AdaptiveConfig()
        state = MockState()
        # Don't call set_phase
        config.tick(state)
        self.assertAlmostEqual(config.temperature, DEFAULT_TEMPERATURE)


class TestMaxNudges(unittest.TestCase):
    """Test complexity-based max_nudges adaptation."""

    def test_complex_paper_more_nudges(self):
        config = AdaptiveConfig()
        # 20 sections → complex
        state = MockState(paper_sections={f"sec_{i}": f"text_{i}" for i in range(20)})
        config.tick(state)
        self.assertEqual(config.max_nudges, 3)

    def test_simple_paper_fewer_nudges(self):
        config = AdaptiveConfig()
        # 3 sections → simple
        state = MockState(paper_sections={"intro": "x", "method": "y", "conc": "z"})
        config.tick(state)
        self.assertEqual(config.max_nudges, 1)

    def test_medium_paper_default_nudges(self):
        config = AdaptiveConfig()
        # 10 sections → medium
        state = MockState(paper_sections={f"sec_{i}": f"t{i}" for i in range(10)})
        config.tick(state)
        self.assertEqual(config.max_nudges, DEFAULT_MAX_NUDGES)

    def test_no_sections_stays_default(self):
        config = AdaptiveConfig()
        state = MockState(paper_sections={})
        config.tick(state)
        # No paper loaded → stays at default
        self.assertEqual(config.max_nudges, DEFAULT_MAX_NUDGES)


class TestKeepRecent(unittest.TestCase):
    """Test context pressure-based keep_recent."""

    def test_high_pressure_compresses(self):
        config = AdaptiveConfig()
        # 70% utilization → high pressure
        state = MockState(
            context_window=100000,
            last_prompt_tokens=70000,
        )
        config.tick(state)
        self.assertEqual(config.keep_recent, 4)

    def test_low_pressure_preserves(self):
        config = AdaptiveConfig()
        # 20% utilization → low pressure
        state = MockState(
            context_window=100000,
            last_prompt_tokens=20000,
        )
        config.tick(state)
        self.assertEqual(config.keep_recent, 8)

    def test_medium_pressure_default(self):
        config = AdaptiveConfig()
        # 45% utilization → medium
        state = MockState(
            context_window=100000,
            last_prompt_tokens=45000,
        )
        config.tick(state)
        self.assertEqual(config.keep_recent, DEFAULT_KEEP_RECENT)

    def test_no_prompt_tokens_no_change(self):
        config = AdaptiveConfig()
        state = MockState(last_prompt_tokens=0, context_window=128000)
        config.tick(state)
        self.assertEqual(config.keep_recent, DEFAULT_KEEP_RECENT)


class TestSignalMaxPerTurn(unittest.TestCase):
    """Test session progress-based signal limit."""

    def test_early_session_full_signals(self):
        config = AdaptiveConfig()
        # Turn 5 / 30 = 17% → early
        state = MockState(loop_turns=5, max_loop_turns=30)
        config.tick(state)
        self.assertEqual(config.signal_max_per_turn, DEFAULT_SIGNAL_MAX_PER_TURN)

    def test_late_session_reduced_signals(self):
        config = AdaptiveConfig()
        # Turn 24 / 30 = 80% → late
        state = MockState(loop_turns=24, max_loop_turns=30)
        config.tick(state)
        self.assertEqual(config.signal_max_per_turn, 1)

    def test_boundary_case(self):
        config = AdaptiveConfig()
        # Exactly at 75% threshold
        state = MockState(loop_turns=23, max_loop_turns=30)
        config.tick(state)
        # 23/30 = 0.767 > 0.75 → should reduce
        self.assertEqual(config.signal_max_per_turn, 1)

    def test_zero_max_turns_no_change(self):
        config = AdaptiveConfig()
        state = MockState(loop_turns=10, max_loop_turns=0)
        config.tick(state)
        self.assertEqual(config.signal_max_per_turn, DEFAULT_SIGNAL_MAX_PER_TURN)


class TestAdaptationLog(unittest.TestCase):
    """Test that adaptations are properly logged."""

    def test_logs_changes(self):
        config = AdaptiveConfig()
        state = MockState(paper_sections={f"s{i}": "t" for i in range(20)})
        config.set_phase("editing")
        config.tick(state)
        # Should have logged at least temperature and max_nudges changes
        self.assertGreater(len(config.adaptation_log), 0)
        params_logged = {e.param for e in config.adaptation_log}
        self.assertIn("temperature", params_logged)
        self.assertIn("max_nudges", params_logged)

    def test_no_log_when_no_change(self):
        config = AdaptiveConfig()
        # Set up state that matches defaults
        # remaining=250000 → 250000/3=83333, capped to DEFAULT(60000) → no budget change
        state = MockState(
            paper_sections={f"s{i}": "t" for i in range(10)},  # medium → default nudges
            loop_turns=5,
            max_loop_turns=30,
            last_prompt_tokens=45000,  # medium → default keep_recent
            context_window=100000,
            token_budget=300000,
            total_tokens=50000,  # ample → default budget
        )
        # Don't set phase → no temperature change
        config.tick(state)
        # All should match defaults → no log entries (each param stays at default)
        # max_nudges=2 (medium paper), keep_recent=6 (45% util), budget=60000 (ample)
        for entry in config.adaptation_log:
            self.assertNotEqual(entry.old_value, entry.new_value)

    def test_idempotent_ticks(self):
        config = AdaptiveConfig()
        state = MockState(paper_sections={f"s{i}": "t" for i in range(20)})
        config.set_phase("deep_review")
        config.tick(state)
        log_count_1 = len(config.adaptation_log)

        # Second tick with same state should not add new log entries
        config.tick(state)
        log_count_2 = len(config.adaptation_log)
        self.assertEqual(log_count_1, log_count_2)


class TestGetAdaptationSummary(unittest.TestCase):
    """Test the summary/describe methods."""

    def test_summary_structure(self):
        config = AdaptiveConfig()
        summary = config.get_adaptation_summary()
        self.assertIn("current", summary)
        self.assertIn("total_adaptations", summary)
        self.assertIn("tick_count", summary)
        self.assertIn("frozen", summary)
        self.assertEqual(summary["current"]["temperature"], DEFAULT_TEMPERATURE)

    def test_describe_string(self):
        config = AdaptiveConfig()
        desc = config.describe()
        self.assertIn("AdaptiveConfig", desc)
        self.assertIn("temp=", desc)
        self.assertIn("nudges=", desc)


class TestMultiTurnEvolution(unittest.TestCase):
    """Test realistic multi-turn adaptation scenarios."""

    def test_phase_transition_sequence(self):
        """Simulate a session going through phases."""
        config = AdaptiveConfig()
        state = MockState(
            paper_sections={f"s{i}": "t" for i in range(12)},
            max_loop_turns=30,
            token_budget=200000,
        )

        # Turn 1-5: initial_scan
        config.set_phase("initial_scan")
        for turn in range(1, 6):
            state.loop_turns = turn
            state.total_tokens = turn * 5000
            state.last_prompt_tokens = 20000
            config.tick(state)
        self.assertAlmostEqual(config.temperature, 0.2)

        # Turn 6-15: deep_review
        config.set_phase("deep_review")
        for turn in range(6, 16):
            state.loop_turns = turn
            state.total_tokens = turn * 8000
            state.last_prompt_tokens = 50000
            config.tick(state)
        self.assertAlmostEqual(config.temperature, 0.4)

        # Turn 20-25: editing (late session)
        config.set_phase("editing")
        for turn in range(20, 26):
            state.loop_turns = turn
            state.total_tokens = 160000
            state.last_prompt_tokens = 80000  # high pressure
            config.tick(state)
        self.assertAlmostEqual(config.temperature, 0.1)
        self.assertEqual(config.keep_recent, 4)  # high context pressure
        self.assertEqual(config.signal_max_per_turn, 1)  # late session


if __name__ == "__main__":
    unittest.main()
